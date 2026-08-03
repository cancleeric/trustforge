"""Agent 編排：信任加權 brief → 結構化 Report + Evidence List + Execution Log。

反作弊鐵則：**判斷結構、證據整合、信任評分由本 pipeline 產生**，
Bedrock 只負責把推理「行文」成可讀敘述，不得把第三方現成結論當主要結果。

顯式 3 步驟推理鏈（P0-3）：
  Step 1 — Claim 抽取（Bedrock #1）：extract_claims_with_llm → 結構化主張
  Step 2 — 判斷形成（純 pipeline）：score + aggregate → TrustedBrief
  Step 3 — 帶溯源行文（Bedrock #2）：narrative 強制引用 claim_id
  Step 4 — 限制複審（Bedrock #3，選用，預算允許才執行）
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from typing import Callable, Iterable

from ..bedrock import BedrockClient
from ..budget_guard import record_unledgered_spend
from ..direction_resolution import resolve_direction
from ..execlog import ExecutionLog
from ..ingestion.base import Document, _matches_coin
from ..ledger import append_run, estimate_cost
from ..schema import BasisItem, Evidence, QuestionType, Report, iso_utc
from ..term_annotations import annotate_terms
from ..trust.scoring import KIND_REPUTATION, ScoredClaim, TrustedBrief
from . import narrative_locale as _loc
from .authoritative_kernel_mapper import run_authoritative_judgment
from .evidence_grouper import group_evidence
from .kernel_mapper import to_kernel_claim
from .kernel_projection import KernelJudgment

# #960 canonical claim identity（見 docs/plans/ISSUE-959-...CONTRACT-2026-07-31.md §2.1）。
# `canonical_source` 採用與 repo-wide 同一口徑（trustforge_core.source_identity），
# 與 scoring._canonical_source / evidence_grouper._normalize_source 完全一致。
from trustforge_core.source_identity import canonical_source as _core_canonical_source

# Step 4 最低剩餘預算門檻（秒）：低於此值直接跳過，確保在 15 分鐘內完成
_STEP4_MIN_BUDGET_SEC = 60.0

_STEP4_SYSTEM = (
    "你是加密市場分析審查員。只能依據提供的報告文字審查，不引入外部知識。"
)
_STEP4_LIMIT_SENTINEL = "LIMITS_OK"


OBJECTIVE_KINDS = {"price", "price_live", "onchain", "regulatory", "hoyabit", "defi_tvl"}
_SENTIMENT_KINDS: set[str] = {"news", "social", "sentiment"}
# CoinGecko `dev_activity`（GitHub stars/forks/commits）刻意不歸入任一類：既非
# 客觀「市場」事實（開發活躍度與價格走勢無直接因果），也非情緒訊號，若強行
# 分類容易在跨源背離偵測中製造假背離（例如開發活躍度下降 vs 現價上漲被誤判
# 為客觀類內部矛盾）。不歸類 = 該 kind 的主張永遠不進入
# `detect_cross_source_signal` 的 objective/sentiment 分組計算。

# Tier2（真實分歧樣本）：stance_pairs 專用的最低信任門檻。刻意低於 detect_cross_
# _source_signal 主流程的 0.5「合格」門檻——真實的雙來源直接矛盾（如同議題 ETF
# 資金流向因結算時區/資料商方法論不同，當日方向相反）本質上**拿不到跨源佐證加分**
# （_corroboration 的方向閘會擋掉互相佐證，兩造各自單打獨鬥），信任分結構性地封頂在
# 「來源基礎信譽 + 滿分時效」附近（news：0.5*0.65+0.15*1.0=0.475 都到不了 0.5）。
# 若沿用 0.5 門檻，真實的兩方矛盾樣本幾乎不可能被納入 stance_pairs 掃描池，等於
# 這個偵測機制對其設計目標（呈現真實背離）永遠失效。0.35 仍濾掉低信任雜訊／被
# manipulation penalty 命中的主張，precision 由下方 stance_fn 的語意矛盾分類把關
# （非快取命中一律 fail-safe 為 "neutral"，不會誤判），不靠這個門檻把關準確度。
_STANCE_PAIR_MIN_TRUST = 0.35

# W4：校準信心三態 abstain 門檻（取代現行單一武斷 0.5 硬門檻）。
# 沿用 `trust.scoring._calibrate_confidence()` 產出的 `calibrated_confidence`
# （硬編分位數映射表，確定性、免 LLM；誠實聲明見該函式 docstring：簡化版，
# 非嚴謹 conformal coverage 保證）。0.5 錨點本身**不刪**——從「唯一硬門檻」
# 降為三態分界之一，`_derive_limits` 的 `brief.confidence < 0.5`、
# `aggregate(support_threshold=0.50)` 等既有呼叫端逐字不變（回歸鎖）。
#   calibrated < _ABSTAIN_CALIBRATED_THRESHOLD 或 supporting 獨立來源數
#   （去重，見下方 n_indep）< _ABSTAIN_MIN_SUPPORTING（證據不足、樣本量
#   過小或單源灌量）→ abstain：不給方向詞。
#   _ABSTAIN_CALIBRATED_THRESHOLD <= calibrated < 0.5 → 仍出結論，標「低信心」。
#   calibrated >= 0.5 → 正常（既有行為逐字不變）。
# W4 codex 對抗審第 5 輪修正：_ABSTAIN_MIN_SUPPORTING 原本比對 supporting
# 的「claim（句）筆數」，單一文件切兩句就能通過門檻；現改比對「去重後的
# 獨立來源數」，跟 trust.scoring._evidence_strength 的 indep_factor/
# dominance 去重口徑一致——單源不論產生幾句 claim，仍只算 1 份。
_ABSTAIN_CALIBRATED_THRESHOLD = 0.35
_ABSTAIN_MIN_SUPPORTING = 2

# N11：主行文 system prompt 移入 `narrative_locale`（多語系單一真相源）。
# 這裡保留同名常數且**字面值逐字不變**（`normalize_locale` 預設 = zh-Hant），
# 因為 `web.py` 的 prompt 治理端點會對它取 SHA-256 公開；改字面值會讓既有
# prompt 指紋變動。英文版走 `_loc.system_prompt("en")`。
SYSTEM = _loc.system_prompt(_loc.DEFAULT_LOCALE)

_PROMPT_INJECTION_RE = re.compile(
    r"(?i)(ignore\s+(?:all\s+)?previous\s+instructions?|"
    r"system\s*:|developer\s*:|assistant\s*:|"
    r"忽略(?:以上|先前|所有)?(?:的)?指令|系統\s*[:：]|開發者\s*[:：])"
)


def _untrusted_prompt_text(value: object, *, max_length: int = 1000) -> tuple[str, bool]:
    """Normalize and soft-redact instruction-shaped text used only as LLM data."""
    text = " ".join(str(value or "").replace("\x00", " ").split())[:max_length]
    suspected = bool(_PROMPT_INJECTION_RE.search(text))
    if suspected:
        text = _PROMPT_INJECTION_RE.sub("[instruction-like text removed]", text)
    return text, suspected

# codex vp-engineering 終審 MEDIUM（PR #107，已實測 100KB `<script>` payload
# 可一路存進 90 天快照——目前唯一防線是「沒人 render 它」，不可接受）：
# `Document.meta["author"]` 是連接器解析上游 RSS/Atom XML 抓來的**未經信任
# 輸入**，在進入 `Evidence.author`（進而流進每日快照 `"authors"` 鍵、留存
# 90 天）之前，這裡是唯一收斂點（news.py／social.py 兩個連接器都經過
# `_scored_to_evidence`），必須在此把關。
_AUTHOR_MAX_LEN = 200
# C0 控制字元（\x00-\x1f）+ DEL（\x7f）+ 尖括號（`<`/`>`，涵蓋 HTML 標籤／
# script payload）——正常來源平台 username 不會出現這些字元，出現即視為
# 異常/惡意輸入。
_AUTHOR_REJECT_RE = re.compile(r"[\x00-\x1f\x7f<>]")


def _sanitize_author(raw) -> str | None:
    """author 健壯性守門：長度上限 200 字、拒收含 HTML 標籤/控制字元的值。

    任一規則觸發即**整筆拒收**（回 `None`，不是截斷/跳脫後照收）：
      1. 超過 `_AUTHOR_MAX_LEN`——沒有任何來源平台 username 正常長到這樣，
         不折衷截斷（截斷仍會把巨大 payload 的前 200 字留在快照裡，一樣
         不安全，直接整筆丟棄才乾淨）。
      2. 命中 `_AUTHOR_REJECT_RE`（控制字元／`<`／`>`）——一律拒收。

    回傳 `None`（不是空字串）：語意上跟「來源本來就沒有作者概念」不同，
    但對下游（`_collect_authors()`/`_public_evidence_dict()`）而言效果
    一致——都不會出現在最終資料裡。
    """
    if not raw:
        return None
    if len(raw) > _AUTHOR_MAX_LEN:
        return None
    if _AUTHOR_REJECT_RE.search(raw):
        return None
    return raw


# --- #960 canonical claim identity — single mint helper ---------------------
# 契約 docs/plans/ISSUE-959-CANONICAL-CLAIM-IDENTITY-CONTRACT-2026-07-31.md §2.1。
# canonical claim_id = "clm1:" + run_scope_id + ":" + claim_local_fingerprint(16hex)。
# 這是 **公開穩定識別碼**（非 HMAC、非密鑰、不帶簽章）：任何知曉 claim 公開屬性
# 與 run_scope_id 的一方皆可重算同一 id（見契約 §1.1 non-goal）。因此不得作為授權、
# 信任或防竄改訊號——run 的授權由 #957 的 transport/receipt authority 擁有。
#
# 命名刻意避開 `score`/`aggregate`：test_kernel_authoritative_boundary.py 會以 AST
# 掃描 orchestrator.py 內所有 Call 的函式名，禁用 `score` 與 `aggregate`。
_CLAIM_ID_SCHEME = "clm1"
_CLAIM_ID_PURPOSE = "claim-identity/v1"
_CLAIM_ID_FP_LEN = 16
_RUN_SCOPE_ID_RE = re.compile(r"^[^:]+$")


def _claim_fingerprint16(sc: ScoredClaim) -> str:
    """claim_local_fingerprint：對 claim 穩定屬性的 **domain-separated、length-
    prefixed** tuple 取 SHA-256 截 16 hex。length-prefix（`{decimal_len}:{bytes}`）
    使欄位邊界無歧義、跨實作 collision-free（`(a,bc)` 與 `(ab,c)` 不可能同碼）。

    屬性順序（契約 §2.1）：
      1. purpose="claim-identity/v1"（domain separation）
      2. claim_type（fact | inference | opinion）
      3. direction（bullish | bearish | neutral）
      4. canonical_source（trustforge_core.source_identity，與 repo 同口徑）
      5. doc_id（來源文件識別）
      6. source_claim_suffix：最後一個 `#` 之後、不含 `#` 的子字串
         （`doc123#0`→`0`、`src#llm0`→`llm0`）；無 `#`（如 `price-BTC-ret`）則用完整指紋
      7. text：**僅 trim**——不動內部空白、大小寫、Unicode code point，不做 NFC/NFKC/casefold
         （改動會把不同 claim 靜默合併或把相同 claim 拆開）
    """
    claim = sc.claim
    doc = claim.doc
    raw_id = claim.id or ""
    if "#" in raw_id:
        suffix = raw_id.rsplit("#", 1)[-1]
    else:
        suffix = raw_id
    text = (claim.text or "").strip()
    parts = (
        _CLAIM_ID_PURPOSE,
        claim.claim_type,
        claim.direction,
        _core_canonical_source(doc.source),
        doc.id,
        suffix,
        text,
    )
    hasher = hashlib.sha256()
    for value in parts:
        raw = str(value).encode("utf-8")
        hasher.update(f"{len(raw)}:".encode("ascii"))
        hasher.update(raw)
    return hasher.hexdigest()[:_CLAIM_ID_FP_LEN]


def _canonical_claim_id(sc: ScoredClaim, run_scope_id: str) -> str:
    """mint 單一 canonical claim_id（`build_report` 的唯一鑄造入口呼叫此 helper）。

    run_scope_id 必須是非空、無冒號的字串（冒號會與 `clm1:{run_scope}:{fp}` 的分隔
    歧義）；不符即 raise ValueError（fail-closed，在發出任何 Evidence 之前）。
    """
    if (not isinstance(run_scope_id, str) or not run_scope_id
            or _RUN_SCOPE_ID_RE.match(run_scope_id) is None):
        raise ValueError("run_scope_id must be a non-empty, colon-free string")
    return f"{_CLAIM_ID_SCHEME}:{run_scope_id}:{_claim_fingerprint16(sc)}"


def _scored_to_evidence(sc: ScoredClaim, related: str) -> Evidence:
    doc = sc.claim.doc
    ref = doc.meta.get("content_reference") or sc.claim.text[:120]
    trust_components = {k: round(v, 3) for k, v in sc.components.items()}
    # W2 可解釋性接線：`dynamic_reputation=True` 時 `sc.reputation_trace` 會帶
    # 該來源的 {source, prior, final, agree_n, contradict_n, iterations_run, mode}
    # （見 trust.scoring.score docstring）。刻意只挑**數值**欄位併入
    # `trust_components`（維持該 dict 既存 str→number 契約，`source` 字串已
    # 有 `Evidence.source` 承載，不重複塞）——讓 web.py 既有的
    # `_render_trust_breakdown` 不必改資料型別就能多顯示「為什麼信譽變動」。
    # `dynamic_reputation=False`（例如舊測試/其他呼叫點）時 `reputation_trace`
    # 為 None，這裡完全不新增鍵，逐字向後相容。
    #
    # `mode`（字串 `"ds_em"`/`"entailment"`）**不放進** `trust_components`——
    # 那是破壞 API 合約的字串污染（codex 對抗審 Medium 修正）。改放到
    # `Evidence.reputation_mode` 同層兄弟欄位，`_public_evidence_dict()` 與
    # web `_render_trust_breakdown` 都從那裡取 mode，而 `trust_components`
    # 保持純數值。
    rep_mode = None
    if sc.reputation_trace is not None:
        trace = sc.reputation_trace
        trust_components["reputation_prior"] = round(trace["prior"], 3)
        trust_components["reputation_final"] = round(trace["final"], 3)
        trust_components["reputation_agree_n"] = trace["agree_n"]
        trust_components["reputation_contradict_n"] = trace["contradict_n"]
        trust_components["reputation_iterations_run"] = trace["iterations_run"]
        rep_mode = trace.get("mode", "entailment")
    return Evidence(
        source=doc.source,
        fetched_at=iso_utc(doc.ts),
        content_reference=ref,
        related_claim=related,
        source_url=doc.url,
        kind=doc.kind,
        direction=sc.claim.direction,
        trust=round(sc.trust, 3),
        trust_components=trust_components,
        # Tier2 可解釋 UX：操縱關鍵詞命中原文回填，供 web.py 渲染紅旗
        # （見 trust.scoring._manipulation_flags；空 list 時等同未命中）。
        flags=list(sc.manip_flags),
        # W3：文字相似度透明化 flag，informational-only、不影響 trust 分數，
        # 供 web.py 用中性樣式渲染（見 trust.scoring._coordination_signals；
        # 空 list 時等同未命中）。
        info_flags=list(sc.info_flags),
        # W3 前置（資料累積，非偵測）：連接器若抓到來源平台公開 username
        # （見 ingestion.social/news 的 `meta["author"]`），原文帶到
        # Evidence；無此欄位的來源（多數 news RSS、onchain、regulatory
        # 等）`doc.meta.get("author")` 回 `None`，缺鍵=未知，不填假值。
        # `_sanitize_author()` 再做一次長度上限/控制字元/HTML 標籤守門
        # （見其 docstring），未通過一律回 `None`。不參與 trust 分數、
        # 不做 UI 顯示。
        author=_sanitize_author(doc.meta.get("author")),
        # W2 動態信譽模式標註（同層兄弟欄位，不污染 `trust_components`）。
        reputation_mode=rep_mode,
        data_lineage=doc.meta.get("data_lineage") or None,
    )


def _price_trend_direction(supporting: list[ScoredClaim],
                           all_scored: list[ScoredClaim] | None = None) -> str | None:
    """從 price claims 提取 close 值算報酬率。

    計算邏輯（兩種模式）：

    模式 A（逐日 OHLCV，backfill 產生）：
      - 從 price claims 的 doc.meta 取 close 值，按 date 排序
      - 報酬率 = (最近 close - 基準 close) / 基準 close
      - 基準 = 14 天前的 close（不足 14 天則用最早的 close）

    模式 B（聚合事實，price_facts 產生）：
      - 從 price claims 的 doc.meta 取 ret_pct（已算好的報酬百分比）
      - 多筆取平均

    判定門檻：> +3% → "偏多"，< -3% → "偏空"，否則 "中性"。
    無 price claims 或無法取得有效資料 → return None。

    #347 修正：price facts 是由官方 OHLCV 自行計算的客觀事實（kind="price",
    source="ohlcv-csv"），信譽 0.95 但因零佐證+時效衰減會落在 trust<0.50，
    被 aggregate() 的 support_threshold 擋在 supporting 之外。方向判定不應
    受信任門檻限制——OHLCV 報酬率是確定性計算結果、不需要佐證。因此本函式
    在 supporting 中找不到 price claims 時，改從 all_scored（完整評分清單）
    中查找，確保客觀價格事實不被信任門檻截斷。
    """
    # 收集 (date_str, close) pairs（模式 A）
    price_points: list[tuple[str, float]] = []
    # 收集 ret_pct（模式 B）
    ret_pcts: list[float] = []

    # #347：先從 supporting 找 price claims；找不到可用方向資料時 fallback 到 all_scored
    # 「可用」= 有 ret_pct（模式 B）或同時有 close + date（模式 A）
    def _has_direction_data(sc) -> bool:
        if sc.claim.doc.kind != "price":
            return False
        meta = sc.claim.doc.meta
        if "ret_pct" in meta:
            return True
        if meta.get("close") is not None and meta.get("date") is not None:
            return True
        return False

    price_source = supporting
    if not any(_has_direction_data(sc) for sc in supporting):
        if all_scored:
            price_source = all_scored

    for sc in price_source:
        if sc.claim.doc.kind != "price":
            continue
        meta = sc.claim.doc.meta
        # 模式 B：meta 有 ret_pct
        if "ret_pct" in meta:
            try:
                ret_pcts.append(float(meta["ret_pct"]))
            except (ValueError, TypeError):
                pass
            continue
        # 模式 A：meta 有 close + date
        close_val = meta.get("close")
        date_str = meta.get("date")
        # 嘗試從 text 解析 "C=xxx" pattern（舊版 claim 相容）
        if close_val is None:
            m = re.search(r"C=([\d.]+)", sc.claim.text)
            if m:
                try:
                    close_val = float(m.group(1))
                except (ValueError, TypeError):
                    continue
        if close_val is None or date_str is None:
            continue
        try:
            close_val = float(close_val)
        except (ValueError, TypeError):
            continue
        if close_val <= 0:
            continue
        price_points.append((date_str, close_val))

    # 優先模式 A（更精確：從逐日 OHLCV 算）
    if len(price_points) >= 2:
        price_points.sort(key=lambda x: x[0])
        latest_date, latest_close = price_points[-1]
        from datetime import date as _date_type
        try:
            latest_d = _date_type.fromisoformat(latest_date)
        except (ValueError, TypeError):
            return _from_ret_pcts(ret_pcts)

        # 找離 latest_d >= 14 天的最近一筆（倒著找）
        base_close = price_points[0][1]  # fallback: 最早
        for date_str, close_val in reversed(price_points[:-1]):
            try:
                d = _date_type.fromisoformat(date_str)
            except (ValueError, TypeError):
                continue
            if (latest_d - d).days >= 14:
                base_close = close_val
                break

        ret = (latest_close - base_close) / base_close
        if ret > 0.03:
            return "偏多"
        elif ret < -0.03:
            return "偏空"
        else:
            return "中性"

    # 模式 B fallback（price_facts 產生的聚合事實）
    return _from_ret_pcts(ret_pcts)


def _from_ret_pcts(ret_pcts: list[float]) -> str | None:
    """從 ret_pct 列表判定方向。"""
    if not ret_pcts:
        return None
    avg_ret = sum(ret_pcts) / len(ret_pcts)
    # ret_pct 是百分比值（如 +5.2 代表 5.2%），門檻 3%
    if avg_ret > 3.0:
        return "偏多"
    elif avg_ret < -3.0:
        return "偏空"
    else:
        return "中性"


def _stance_consensus_direction(supporting: list[ScoredClaim]) -> str | None:
    """Layer 2 多源 Stance 加權方向判定（Issue #342）。

    從 supporting 中收集有 `sc.claim.direction` 為 "bullish" 或 "bearish" 的
    claims，用每筆的 trust score 做加權投票。需要 ≥2 個獨立來源（canonical_source
    去重）有方向才有效，避免單源灌量或偶發方向標記主導結論。

    判定規則：
    - bullish_weight > bearish_weight × 1.3 → "偏多"
    - bearish_weight > bullish_weight × 1.3 → "偏空"
    - 否則（含勢均力敵）→ return None（fallback 到 Layer 1 價格趨勢）

    設計意圖：
    - 獨立來源門檻（≥2）確保不是單一來源的方向判斷就主導結論。
    - 1.3 倍閾值確保多空差異有足夠顯著性，避免微弱優勢下硬給方向。
    - 使用 `_canonical_source` 去重（repo-wide 唯一來源正規化），與
      trust.scoring 的獨立性 invariant 一致。
    """
    from ..trust.scoring import _canonical_source

    bullish_weight = 0.0
    bearish_weight = 0.0
    directional_sources: set[str] = set()

    for sc in supporting:
        direction = sc.claim.direction
        if direction not in ("bullish", "bearish"):
            continue
        source_key = _canonical_source(sc.claim.doc.source)
        directional_sources.add(source_key)
        if direction == "bullish":
            bullish_weight += sc.trust
        else:
            bearish_weight += sc.trust

    # 需要 ≥2 個獨立來源有方向才有效
    if len(directional_sources) < 2:
        return None

    # 判定方向（需 1.3 倍顯著性）
    if bullish_weight > bearish_weight * 1.3:
        return "偏多"
    if bearish_weight > bullish_weight * 1.3:
        return "偏空"
    return None


def _direction(supporting: list[ScoredClaim],
               all_scored: list[ScoredClaim] | None = None) -> str:
    """統一方向判定：LLM 語意分析 → offline fallback (OHLCV 統計)。

    唯一管線（Issue #372 統一）：
    1. Bedrock/AgentCore 可用 → LLM 語意分析（analyze_direction + aggregate_votes）
    2. offline → OHLCV 報酬率統計 fallback

    不再有 Layer 1/2/3 多層 fallback。

    參數吃呼叫端傳入的 supporting 子集——W4 codex 對抗審第 8 輪根治後，
    `trust.scoring.aggregate(coin=)` 本身就已用 `_matches_coin` 篩過
    `TrustedBrief.supporting`（保留本幣相關＋全市場通用，排除明確他幣），
    `build_report` 直接傳 `brief.supporting` 進來即天生 coin-scoped，不必
    再由呼叫端另外篩一次。

    `all_scored`：選填（#347 修正追加）。完整評分清單——price facts 因零佐證+
    時效衰減可能 trust<0.50 被排除在 supporting 之外，但它們是自行從官方
    OHLCV 計算的客觀事實，方向判定不應受信任門檻限制。提供此參數時，
    `_price_trend_direction` 在 supporting 無 price claims 時會從中查找。
    """
    # === 主路徑：LLM 語意分析 ===
    try:
        from ..semantic_direction import analyze_direction, aggregate_votes
        client = BedrockClient()
        if not client.offline:
            evidence_by_type: dict[str, list[str]] = {}
            source_claims = all_scored if all_scored else supporting
            for sc in source_claims:
                kind = sc.claim.doc.kind or "unknown"
                type_map = {
                    "price": "price", "news": "news", "regulatory": "news",
                    "onchain": "onchain", "market": "onchain",
                    "sentiment": "sentiment", "social": "sentiment",
                }
                st = type_map.get(kind)
                if st:
                    evidence_by_type.setdefault(st, []).append(sc.claim.text)
            if evidence_by_type:
                votes = analyze_direction(evidence_by_type, client)
                if votes:
                    direction, conf = aggregate_votes(votes)
                    return {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(direction, "中性")
    except Exception:
        pass

    # === Offline fallback：OHLCV 統計 ===
    price_dir = _price_trend_direction(supporting, all_scored=all_scored)
    return price_dir or "不明"


def _derive_limits(brief: TrustedBrief) -> tuple[list[str], list[str]]:
    limits: list[str] = []
    flips: list[str] = []
    kinds = {sc.claim.doc.kind for sc in brief.supporting}
    if len(kinds) < 3:
        limits.append(f"資料來源類型僅 {len(kinds)} 類（<3），多源整合度有限，結論不確定性較高。")
    if brief.confidence < 0.5:
        limits.append("整體資訊完整度偏低，支撐證據不足以形成強判斷。")
    if brief.contrarian:
        limits.append(f"存在 {len(brief.contrarian)} 條反方／低信任訊號，已標記但未納入主結論。")
        flips.append("若反方訊號獲得高信任獨立來源佐證，結論可能反轉。")
    flips.append("出現高信任的反向鏈上大額流動或監管事件時，須重評。")
    return limits, flips


# 新核心#2（gray docs/archive/plans/PLAN-multicore-worldfirst.md，task #25）：分維度信任的
# 中文標籤。鍵集合刻意不獨立重複維護——候選維度＝`KIND_REPUTATION`（信任評分
# 唯一吃得到的 kind 全集），新增連接器只要在該表登記一筆 kind，雷達自動多出
# 一維，這裡只補顯示用的中文標籤（缺標籤時 fallback 用 kind 原字串，不會噴錯）。
_DIMENSION_LABELS: dict[str, str] = {
    "news": "新聞信任",
    "onchain": "鏈上信任",
    "social": "社群信任",
    "regulatory": "監管信任",
    "price": "價格信任",
    "price_live": "即時價格信任",
    "hoyabit": "交易所信任",
    "sentiment": "情緒信任",
    "dev_activity": "開發活躍度信任",
}


def aggregate_trust_by_kind(evidence: list[Evidence]) -> dict[str, dict]:
    """新核心#2：把 evidence 按 source kind 分組，每組聚合出一個「維度信任分」。

    ⛔ $0 保證：不重新呼叫 Bedrock／連接器、不重算信譽公式——直接複用每筆
    `Evidence.trust`（已由 `trust.scoring.score()` 依「信譽×0.5 + 佐證×0.25 +
    時效×0.15 − 操縱×0.4」算好，見該模組 docstring），同 kind 取算術平均當
    該維度信任分。純粹是對既有結果的**重新聚合**，不多打任何外部呼叫。

    誠實標單源維度（gray 抓出：regulatory 只有 SEC 1 源、social 只有 Reddit
    1 源，跟 news 12 源不能等量齊觀）：每個有資料的維度都回傳 `n_sources`
    （去重後的獨立來源數，依 `Evidence.source` 計）與 `single_source`
    （`n_sources <= 1`）——呼叫端（web.py）必須用這個旗標把單源維度明確標成
    「單一來源，非多源獨立交叉驗證」，不能包裝成跟多源維度同等可信。

    誠實顯示無資料（#24）：候選維度固定取自 `KIND_REPUTATION` 全集，本次
    analysis 若某 kind 完全沒有 evidence（例如未啟用 coingecko 連接器時的
    price_live/sentiment/dev_activity），該維度回傳 `has_data=False`、
    `trust=None`——不會用 0 或任何佔位數字冒充「有算過但分數低」，避免
    使用者把「沒資料」誤讀成「該維度信任極差」。

    回傳：`{kind: {"label", "has_data", "trust", "n_sources", "n_evidence",
    "single_source"}}`，鍵集合**嚴格等於** `KIND_REPUTATION` 全集、鍵順序固定
    依 `KIND_REPUTATION` 插入順序——不受 evidence 內容影響，確保雷達軸跨報告
    可比較。evidence 出現、但不在 `KIND_REPUTATION` 裡的 kind（空字串、
    schema drift、連接器拼字錯誤，例如把 "news" 打成 "newss"）**一律忽略、
    不動態加軸**：這類 evidence 不計入任何維度（不會被硬塞進某個「看起來
    像」的既有維度，以免污染該維度的信任平均與來源計數），只用
    `logging.warning` 記一筆可觀測 log 供事後發現分類錯誤，雷達軸本身
    絕不因此變動。

    只讀 `evidence`，不改動任何既有欄位／物件——`Report.confidence`／
    `calibrated_confidence`（信任總分）完全不受影響，分維聚合是額外呈現，
    不改總分演算法。
    """
    by_kind: dict[str, list[Evidence]] = {}
    unknown_kinds: set[str] = set()
    for ev in evidence:
        if ev.kind not in KIND_REPUTATION:
            unknown_kinds.add(ev.kind)
            continue
        by_kind.setdefault(ev.kind, []).append(ev)

    if unknown_kinds:
        logging.warning(
            "aggregate_trust_by_kind: 忽略不在 KIND_REPUTATION 的未知/空 kind"
            "（不動態加軸，可能是 schema drift 或連接器拼字錯誤）：%s",
            sorted(unknown_kinds),
        )

    out: dict[str, dict] = {}
    for kind in KIND_REPUTATION:
        kind_evidence = by_kind.get(kind, [])
        label = _DIMENSION_LABELS.get(kind, kind)
        if not kind_evidence:
            out[kind] = {
                "label": label,
                "has_data": False,
                "trust": None,
                "n_sources": 0,
                "n_evidence": 0,
                "single_source": None,
            }
            continue
        # issue #106：改用 `_independent_source_keys`（正規化去重，見其
        # docstring）——同一來源大小寫/空白變體不再被誤判成不同獨立來源。
        sources = _independent_source_keys(ev.source for ev in kind_evidence)
        avg_trust = sum(ev.trust for ev in kind_evidence) / len(kind_evidence)
        out[kind] = {
            "label": label,
            "has_data": True,
            "trust": round(avg_trust, 3),
            "n_sources": len(sources),
            "n_evidence": len(kind_evidence),
            "single_source": len(sources) <= 1,
        }
    return out


def _harvest_stance_cost_events(client: BedrockClient, log: ExecutionLog) -> None:
    """收割 `client.cost_events`（stance 語意分類真呼叫的成本）進 `log`，並清空。

    `classify_stance` 的成本無法在深層 O(n²) 迴圈中方便帶入 log，改由
    `BedrockClient` 自己在真呼叫（cache-miss）成功後累積在 `client.cost_events`；
    呼叫端在每個可能觸發 stance 呼叫的階段（Step2 `score()`、Step2.5
    `detect_cross_source_signal()`）結束後都必須呼叫本函式收割，確保：
    (1) 成本確實進帳本；(2) `client.cost_events` 歸零，不殘留到下一階段/
    下一輪 run（demo 可靠性 #32 追加 cost-integrity HIGH 修正——同一個
    client 物件常見於 comparison 模式兩幣共用同一輪，殘留未清空的
    cost_events 會被下一輪誤記到別的幣頭上）。

    `getattr` 防禦：測試用的假 client（如 FakeBedrockClient）可能沒有此屬性。
    """
    cost_events = getattr(client, "cost_events", None)
    if not cost_events:
        return
    for ev in cost_events:
        log.record_llm_cost(ev["model"], ev["tokens_in"], ev["tokens_out"], ev["cost_usd"])
    cost_events.clear()


def _normalize_source_key(source: str) -> str:
    """來源去重/相等比對用的正規化 key。

    委託給 `trust.scoring._canonical_source`（issue #72 repo-wide canonical
    source identity 的**唯一**真相來源）：先 `strip().casefold()` 治大小寫/
    前後空白變體（`"CoinDesk"` / `" coindesk "` / `"COINDESK"`），再套別名
    收斂（如 `coindesk.com` → `coindesk`、`twitter` → `x`）。**只用於比對**，
    顯示一律用原始 `source` 字串，不改寫使用者看到的來源名稱。

    保留此函式名僅為向後相容既有呼叫端；新程式碼可直接用 `_canonical_source`。
    """
    from trustforge.trust.scoring import _canonical_source

    return _canonical_source(source)


def _independent_source_keys(sources: Iterable[str | None]) -> set[str]:
    """「同一來源只算一個獨立聲音」不變量的唯一收斂實作（issue #106）。

    供本模組三處呼叫端共用：
    (a) `aggregate_trust_by_kind` 算 `n_sources`
    (b) `detect_cross_source_signal` 算 `obj_sources`/`sent_sources`
    (c) `build_report` 算 `n_indep`（直接餵 W4 abstain 三態判斷）

    先前這三處各自用 raw `{x.source for x in items}` 做 set 去重，沒有套用
    `_normalize_source_key` 正規化，導致同一 publisher 的大小寫/空白變體（如
    `"CoinDesk"` / `" coindesk "`）會被誤判成不同的獨立來源，虛增獨立來源
    計數。本函式統一收斂三處口徑（跟 `_detect_stance_pairs`／
    `_dedup_stance_pairs_by_source`／`_distinct_source_labels` 既有的
    `_normalize_source_key` 去重口徑對齊，不造第二套真相），避免同一份資料在
    不同呼叫端算出不同的「獨立來源數」，也避免同源灌水虛增 n_indep 讓本該
    abstain 的判斷沒有 abstain。

    對 falsy 項目（None、空字串）防禦性跳過，不呼叫 `_normalize_source_key`、
    不計入結果、不拋例外——呼叫端資料可能是缺 `source` 欄位的 schema drift
    情境（見 `Evidence.source`/`Document.source` 皆為必填 `str`，理論上不該
    出現 None，這裡是防禦性寫法，不假設呼叫端一定守約）。

    falsy 過濾在正規化**之後**做（而非之前）：正規化前非空的字串，正規化後
    可能變空（例如純空白字串 `" "` 經 `.strip().casefold()` 後變 `""`）——
    這種「正規化後才變空」的情況也要視為沒有來源、防禦性跳過，不能被誤判成
    一個幽靈獨立來源（qa-lead LOW-3）。
    """
    return {k for s in sources if s and (k := _normalize_source_key(s))}


def _count_independent_sources(sources: Iterable[str | None]) -> int:
    """`_independent_source_keys` 的計數版本，供只需要數量（不需要 set 本身，
    例如 `n_indep`）的呼叫端使用。
    """
    return len(_independent_source_keys(sources))


def _detect_stance_pairs(
    candidates: list[ScoredClaim],
    stance_fn: Callable[[str, str], str] | None,
) -> list[dict]:
    """掃描情緒類（news/social）主張中「不同來源 + 方向明確相反」的候選配對，
    交給 stance_fn 做語意矛盾分類，過濾掉純方向詞表面相反、實質無關的假陽性。

    真實案例（Tier2）：同議題（如 ETH 現貨 ETF 資金流向）因結算時區/資料商方法論
    不同，不同來源當日報導方向相反——兩則新聞遣詞完全不同、關鍵字重疊低，純文字
    overlap 比對（見 trust.scoring._corroboration）抓不到，需靠語意 stance 分類
    （trust/stance_cache.py，離線走持久化快取，不即時打 Bedrock）才辨識得出。

    `stance_fn` 為 None（未提供）時直接回空 list：向後相容，不改變
    `detect_cross_source_signal` 既有行為，也不會意外觸發任何 stance 呼叫。

    回傳：扁平 list，內含所有涉及至少一組矛盾配對的主張（跨配對去重），每筆
    `{"source", "stance", "claim_id", "text"}`，供未來 UI 渲染跨源矛盾對照。

    獨立性 invariant（codex #13 追加 HIGH 修正）：「同源」判斷用
    `_normalize_source_key`（正規化），不是原始字串相等——否則同一個
    publisher 用大小寫/空白變體（如 `"CoinDesk"` vs `" coindesk "`）發的
    兩則反向主張，會因為 raw string 不相等而被誤判成「跨來源」矛盾配對，
    讓單一 publisher 自我矛盾撐起一個假的「跨源分歧」訊號，直接違反本
    機制「呈現真實跨源背離」的獨立性核心承諾。同一 normalized source 的
    反向主張視為「同源自我矛盾」，不在本函式範圍內配對（不產生 pair），
    自然也不會被算進下游任何一個 stance 陣營。
    """
    if stance_fn is None:
        return []

    eligible = [
        sc for sc in candidates
        if sc.trust >= _STANCE_PAIR_MIN_TRUST and sc.claim.doc.kind in _SENTIMENT_KINDS
    ]

    seen_claim_ids: set[str] = set()
    pairs: list[dict] = []
    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            if _normalize_source_key(a.claim.doc.source) == _normalize_source_key(b.claim.doc.source):
                continue  # 同源（含大小寫/空白變體）不算跨源矛盾——同源自我矛盾
            da, db = a.claim.direction, b.claim.direction
            if "neutral" in (da, db) or da == db:
                continue  # 需方向明確且相反；方向相同或不明不算矛盾
            if stance_fn(a.claim.text, b.claim.text) != "contradiction":
                continue
            for sc in (a, b):
                if sc.claim.id in seen_claim_ids:
                    continue
                seen_claim_ids.add(sc.claim.id)
                pairs.append({
                    "source": sc.claim.doc.source,
                    "stance": sc.claim.direction,
                    "claim_id": sc.claim.id,
                    "text": sc.claim.text,
                })
    return pairs


def _dedup_stance_pairs_by_source(pairs: list[dict]) -> dict[str, list[dict]]:
    """`_detect_stance_pairs()` 回傳的 `pairs` 是逐筆明細（去重鍵 `claim.id`），
    同一來源若有兩則不同 claim 各自跟不同對手配對成功，會在 `pairs` 裡出現
    兩次——這是刻意保留的原始明細（供展開查看），**不是**「獨立來源數」。

    本函式在 `pairs` 之上疊加一層「同一 stance 陣營內按 source 去重」（對照組：
    `trust.scoring.aggregate` 的 `n_contrarian_sources`、本檔 `aggregate_trust_by_kind`
    的 `n_sources` 都是用 set/dict 依 `source` 去重，同一模式），修正「同一來源
    多筆矛盾主張被算成多個獨立來源」的虛高問題：

    - 同一來源在同一 stance（bullish 或 bearish）只保留一筆代表——取該陣營中
      該來源第一筆出現的 pair（`pairs` 本身依 `_detect_stance_pairs` 的掃描
      順序產生，是確定性順序，不含隨機性）。
    - **跨陣營不去重**：同一來源若在 bullish、bearish 都有主張（自我矛盾），
      兩邊各自保留各自的代表——這是另一個獨立訊號（來源自我矛盾），不該被
      當成雜訊吃掉，本輪不擴大處理，只維持不誤刪。
    - 純資料轉換、不改變 `pairs` 原始清單本身（呼叫端仍可用 `pairs` 取得未去重
      的完整明細）。

    去重 key 正規化（codex #13 追加修正）：比對用 `_normalize_source_key`
    （`strip().casefold()`），不是原始字串——治掉大小寫/前後空白變體（如
    `"CoinDesk"` / `" coindesk "` / `"COINDESK"`）被誤判成不同來源這個零
    成本就能修的洞。**顯示仍用原始 `source` 字串**（保留該陣營中第一筆
    出現時的大小寫/格式，不改寫使用者看到的來源名稱，只有去重比對走
    正規化）。與 `_detect_stance_pairs` 判「同源」用的是**同一個**正規化
    函式，確保「配對層」與「去重層」的獨立性判斷口徑一致（codex 第二輪
    HIGH：先前只在本函式做正規化、配對層仍比對 raw string，導致同一
    publisher 的大小寫變體能在配對層被誤判成跨來源，見
    `_detect_stance_pairs` docstring）。

    這不是完整的 canonical source identity——不同帳號/別名（如
    `"coindesk"` vs `"coindesk.com"` vs 不同 Twitter 帳號同發行商）仍會被
    當成不同來源，這是全 repo 已知限制（`trust/scoring.py` 994 行
    `#17`：`_corroboration` 同源排除也是比對 `source` 字面值，非網域/
    canonical id）。完整 canonical identity（publisher/account 穩定 ID +
    別名映射）會同時影響 `scoring.py:1408`、`orchestrator.py:232`、本函式
    三處來源計數 + ingestion 連接器層，是 repo-wide 工程，刻意不在本輪
    （#13）擴大處理，見 follow-up issue（repo-wide canonical source
    identity）。
    """
    result: dict[str, list[dict]] = {"bullish": [], "bearish": []}
    seen: dict[str, set[str]] = {"bullish": set(), "bearish": set()}
    for p in pairs:
        stance = p["stance"]
        if stance not in result:
            continue  # 理論上不會出現（方向閘已擋掉 neutral），保守略過非 bullish/bearish
        key = _normalize_source_key(p["source"])
        if key in seen[stance]:
            continue
        seen[stance].add(key)
        result[stance].append(p)
    return result


_MIN_RICH_SOURCE_KINDS = 3


def _source_kind_distribution(scored: Iterable[ScoredClaim]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for sc in scored:
        kind = (sc.claim.doc.kind or "unknown").strip() or "unknown"
        distribution[kind] = distribution.get(kind, 0) + 1
    return distribution


def _with_rich_source_kind_representatives(
    supporting: list[ScoredClaim],
    candidates: list[ScoredClaim],
    *,
    support_threshold: float = 0.5,
) -> list[ScoredClaim]:
    """Keep representative valid claims from at least three source kinds."""
    available_kinds = {
        (sc.claim.doc.kind or "unknown").strip() or "unknown"
        for sc in candidates
        if sc.trust >= support_threshold
    }
    if len(available_kinds) < _MIN_RICH_SOURCE_KINDS:
        return supporting

    admitted_ids = {sc.claim.id for sc in supporting}
    admitted_kinds = {
        (sc.claim.doc.kind or "unknown").strip() or "unknown"
        for sc in supporting
        if sc.trust >= support_threshold
    }
    if len(admitted_kinds) >= _MIN_RICH_SOURCE_KINDS:
        return supporting

    by_kind: dict[str, list[ScoredClaim]] = {}
    for sc in candidates:
        if sc.trust < support_threshold:
            continue
        kind = (sc.claim.doc.kind or "unknown").strip() or "unknown"
        by_kind.setdefault(kind, []).append(sc)

    enriched = list(supporting)
    for kind in sorted(available_kinds - admitted_kinds):
        candidates = sorted(
            by_kind.get(kind, []),
            key=lambda sc: (-sc.trust, sc.claim.id),
        )
        for candidate in candidates:
            if candidate.claim.id in admitted_ids:
                continue
            enriched.append(candidate)
            admitted_ids.add(candidate.claim.id)
            admitted_kinds.add(kind)
            break
        if len(admitted_kinds) >= _MIN_RICH_SOURCE_KINDS:
            break
    return enriched


def _distinct_source_labels(pairs: list[dict]) -> list[str]:
    """回傳 `pairs`（跨陣營，不分 bullish/bearish）涉及的來源顯示清單，供組
    user-visible summary 文字（`f"來源 {'、'.join(...)} 對同一議題..."`）用。

    codex 第三輪一致性 HIGH 修正：先前這裡直接 `sorted({p["source"] for p in
    pairs})`，是 raw string 去重——`distinct_sources` 欄位已依
    `_normalize_source_key` 去重收斂成 1 張 CoinDesk 卡，但 summary 文字仍會
    把 `"CoinDesk"`／`" coindesk "` 兩個大小寫/空白變體當成兩個不同來源列出，
    造成同一個訊號裡「結構化欄位」與「顯示文字」自相矛盾、且顯示文字本身
    仍有來源膨脹（使用者讀 summary 會誤以為有更多獨立來源佐證）。

    去重口徑跟 `_dedup_stance_pairs_by_source`／配對層完全一致：用
    `_normalize_source_key` 比對是否同源，**顯示仍用原始 `source` 字串**（保留
    該 normalized key 第一次出現時的大小寫/格式，不改寫使用者看到的來源
    名稱）。去重後依顯示字串排序，確保 summary 文字順序穩定、不受
    `pairs` 掃描順序影響（跟原本 `sorted(set(...))` 的排序意圖一致）。
    """
    seen: dict[str, str] = {}
    for p in pairs:
        key = _normalize_source_key(p["source"])
        if key not in seen:
            seen[key] = p["source"]
    return sorted(seen.values())


def detect_cross_source_signal(
    scored: list[ScoredClaim],
    stance_fn: Callable[[str, str], str] | None = None,
) -> dict | None:
    """跨源訊號偵測：判斷客觀類與情緒類訊號是否背離或共識（純函式，無副作用）。

    入參 scored = list[ScoredClaim]，包含所有可用主張（trust 任意）。
    函式內部只採用 trust >= 0.5 的主張進行計算。

    規格：
    - 客觀類 = OBJECTIVE_KINDS；情緒類 = {"news", "social"}
    - 各類信任加權方向投票：weight[dir] = sum(trust for dir)，最高者為主導；
      若最高 weight < 0.3 × 該類總 trust → 主導視為 "neutral"
    - 背離：客觀主導 ≠ neutral 且情緒主導 ≠ neutral 且兩者相反
    - 共識：兩類主導相同（非 neutral）且兩類各有 ≥1 source
    - None：任一類 0 筆 / 任一主導 neutral / 兩類 source 合計 < 2

    `stance_fn`（選用，預設 None）：Tier2 新增。None 時完全不影響上述既有規格
    （逐字相容，回歸鎖）。提供時，額外掃描情緒類主張中「同議題語意矛盾」的
    跨源配對（見 `_detect_stance_pairs`）：
    - 找到配對時：矛盾優先於聚合層級的判定（demo 可靠性 #32 追加
      correctness HIGH 修正）——
        - 若聚合層級已判定 divergence：維持 divergence，`stance_pairs` 併入
          dict，配對 claim_id 併入 `supporting_claim_ids`。
        - 若聚合層級判定 consensus（客觀/情緒多數方向剛好同向）：**改判為
          divergence**，summary 改寫為反映矛盾的背離敘述——共識底下藏著
          「已確認」的跨源矛盾時，不得繼續顯示「訊號一致」把矛盾蓋掉。
        - 若聚合層級判不出結論（回 None 的任一分支）：改回傳一個以
          stance_pairs 為主體的 `type="divergence"` 訊號——因為客觀類趨勢
          與「兩則新聞互相矛盾」是兩件獨立可觀察到的事，後者不該被前者的
          聚合結果蓋掉。
    - 找不到配對時：完全不影響既有回傳值（含 None、consensus）。

    `stance_pairs` 附加時，一律同步附加 `distinct_sources`（#13 修正）：
    `{"bullish": [...], "bearish": [...]}`，是 `stance_pairs` 依 source 在
    各自陣營內去重後的代表清單（見 `_dedup_stance_pairs_by_source`）——
    `stance_pairs` 本身刻意保留原始逐筆明細（去重鍵是 `claim.id`，供展開
    查看每一則矛盾主張），**不代表獨立來源數**；「這一輪偵測到幾個獨立
    來源支持某方向」一律以 `distinct_sources` 為準，呼叫端（UI）計數/去重
    渲染請讀這個欄位，不要直接對 `stance_pairs` 做 `len()`。

    獨立性 invariant（codex #13 第二輪 HIGH 修正，防禦層）：`stance_pairs`
    非空時，一律再驗一次「涉及的 normalized source 是否 ≥2 個」——
    `_detect_stance_pairs` 的配對迴圈已改用正規化比對「同源」（見其
    docstring），理論上每一筆成功配對本身就保證兩端 normalized source
    不同，這裡的檢查屬於顯式的第二道防線（belt-and-suspenders，不依賴
    單一程式碼路徑撐住整個獨立性承諾）：若不足 2 個真正不同來源（理論上
    不會發生，除非未來改動悄悄破壞了配對層的正規化不變式），一律視同
    `stance_fn` 沒偵測到任何有效跨源矛盾，不 emit 任何以 stance_pairs 為
    依據的訊號——不得讓單一 publisher（即使用不同大小寫/空白變體發文）
    的自我矛盾撐起一個假的「跨源分歧」。

    user-visible source list 一致性（codex #13 第三輪一致性 HIGH 修正）：
    `summary` 文字裡列出的來源名單（`_stance_pair_signal()` 的 fallback
    summary、聚合層級同向但情緒面內部矛盾時的 collision summary）一律改用
    `_distinct_source_labels()`，去重口徑與 `distinct_sources` 欄位完全
    一致（都是 `_normalize_source_key`）——避免結構化欄位（`distinct_sources`）
    已把 `CoinDesk`/`" coindesk "` 收斂成 1 筆，但顯示給使用者看的 summary
    文字卻仍把兩個大小寫/空白變體當成兩個不同來源列出，內部自相矛盾、
    使用者被誤導以為有更多獨立來源佐證。

    守 HOYA「不代客決策」：summary 使用中性提醒措辭，嚴禁決策字眼。

    `sentiment_source_count`（issue #21，CISO-LOW，僅出現在 obj_dir/sent_dir
    主分支回傳值，`_stance_pair_signal()` 備援分支不附加，該分支已保證
    stance_pairs ≥2 獨立來源）：這個 result 實際引用到的情緒類（news/
    social/sentiment）獨立來源數——`len(sent_sources | stance_pair_sentiment_
    sources)`：`sent_sources` 是 trust>=0.5 聚合投票用的來源（跟上面「兩類
    source 合計 < 2」判斷同一份資料）；`stance_pair_sentiment_sources` 是
    `stance_pairs` 非空時額外併入的來源（R1 退修，見下段）。皆用
    `_independent_source_keys`／`_normalize_source_key`（`strip().casefold()`）
    正規化字串 key 去重——**只治大小寫/空白變體**（如 `"CoinDesk"`/
    `" coindesk "` 收斂成同一 key），**不解 publisher 別名**（如 `coindesk`
    vs `coindesk.com` 仍視為 2 個不同來源）；別名 canonicalization 見
    follow-up issue #72，本輪不做。

    純展示用透明化欄位，**不影響** `sent_dir`/`signal_type`/`summary` 等既有
    計算——單一高佐證 social 源在高 corr(≈1.0)+高 recency 時 trust 可達門檻
    以上，若情緒類僅此一源即可用 100% 票重主導 `sent_dir`，觸發虛假背離/
    共識框。緩解方式選「不抑制訊號、只補透明度」（CPO/CISO 三審定案）：UI
    （見 `CrossSourceSignalPanel`）在 `sentiment_source_count == 1` 時顯示
    「單一來源主導」徽章，多源時不顯示，訊號本身照常呈現。

    為何要併入 `stance_pair_sentiment_sources`（PR #135 R1 退修，dev-manager
    實測重現、CEO 退修必修 1）：`_detect_stance_pairs` 用較寬鬆的
    `_STANCE_PAIR_MIN_TRUST`（0.35）掃描矛盾配對，比 `sent_sources` 的
    trust>=0.5 門檻低——若只算 `sent_sources`，可能出現「一筆 trust 落在
    [0.35, 0.5) 的來源沒進聚合投票、count 只算到 1」，但 `stance_pairs`
    非空時一律附加進本 result（collision 分支的 summary 甚至具名列出這些
    來源），使徽章宣稱「單一來源」跟同一畫面的 summary/stance_pairs 明明
    列出 2 個以上來源自相矛盾。改成聯集後，徽章宣稱與本 result 引用到的
    來源集合永遠一致。
    """
    # 只取 trust >= 0.5 的主張
    eligible = [sc for sc in scored if sc.trust >= 0.5]

    objective = [sc for sc in eligible if sc.claim.doc.kind in OBJECTIVE_KINDS]
    sentiment = [sc for sc in eligible if sc.claim.doc.kind in _SENTIMENT_KINDS]

    stance_pairs = _detect_stance_pairs(scored, stance_fn)
    if stance_pairs:
        _distinct_norm_sources = {_normalize_source_key(p["source"]) for p in stance_pairs}
        if len(_distinct_norm_sources) < 2:
            # 獨立性 invariant 防禦層：真正不同來源不足 2 個，不算跨源分歧
            # （理論上配對層的正規化比對已擋住此情況，這裡是顯式第二道防線）。
            stance_pairs = []

    def _stance_pair_signal() -> dict | None:
        """聚合層級判不出背離/共識時的備援：若仍偵測到同議題語意矛盾配對，
        產出以 stance_pairs 為主體的 divergence 訊號。stance_pairs 為空則回 None
        （逐字等同未提供 stance_fn 時的既有行為）。"""
        if not stance_pairs:
            return None
        sources = _distinct_source_labels(stance_pairs)
        return {
            "type": "divergence",
            "objective_direction": None,
            "sentiment_direction": None,
            "summary": (
                f"來源 {'、'.join(sources)} 對同一議題方向相反，"
                "呈背離，建議交叉驗證、留意轉折。"
            ),
            "supporting_claim_ids": [p["claim_id"] for p in stance_pairs],
            "stance_pairs": stance_pairs,
            "distinct_sources": _dedup_stance_pairs_by_source(stance_pairs),
        }

    # 任一類 0 筆 → None（除非有 stance_pairs 備援）
    if not objective or not sentiment:
        return _stance_pair_signal()

    def _dominant(group: list[ScoredClaim]) -> str:
        """回傳信任加權後的主導方向；若最高票 < 0.3×total 則回 'neutral'。"""
        weights: dict[str, float] = {}
        total = 0.0
        for sc in group:
            d = sc.claim.direction
            weights[d] = weights.get(d, 0.0) + sc.trust
            total += sc.trust
        if not total:
            return "neutral"
        best_dir = max(weights, key=lambda k: weights[k])
        return best_dir if weights[best_dir] >= 0.3 * total else "neutral"

    obj_dir = _dominant(objective)
    sent_dir = _dominant(sentiment)

    # 任一主導 neutral → None（除非有 stance_pairs 備援）
    if obj_dir == "neutral" or sent_dir == "neutral":
        return _stance_pair_signal()

    # 兩類 source 合計 < 2 → None（除非有 stance_pairs 備援）
    # issue #106：改用 `_independent_source_keys`（正規化去重）——同一來源
    # 大小寫/空白變體不再被誤判成兩個獨立來源、虛增「合計 source 數」。
    obj_sources = _independent_source_keys(sc.claim.doc.source for sc in objective)
    sent_sources = _independent_source_keys(sc.claim.doc.source for sc in sentiment)
    if len(obj_sources | sent_sources) < 2:
        return _stance_pair_signal()

    # 判定訊號類型（矛盾優先於一致，demo 可靠性 #32 追加 correctness HIGH 修正）：
    # 客觀/情緒兩類的信任加權多數方向可能剛好同向（聚合層級判定 consensus），
    # 但若 `_detect_stance_pairs` 已在情緒來源內部抓到「已確認」的跨源語意
    # 矛盾配對（stance_pairs 非空），代表這個共識底下其實藏著真矛盾——繼續
    # 顯示「訊號一致」會把矛盾蓋掉、誤導使用者，違反本功能「呈現真實背離」
    # 的目的。修法：只要 stance_pairs 非空，一律 type="divergence"，不論聚合
    # 層級算出的 obj_dir/sent_dir 是否同向；沒有 stance_pairs 時維持既有
    # divergence/consensus 判定不變（回歸鎖）。
    if obj_dir != sent_dir:
        signal_type = "divergence"
        collision = False
    elif stance_pairs:
        signal_type = "divergence"
        collision = True
    else:
        signal_type = "consensus"
        collision = False

    # 中文方向標籤（守不代客決策）
    _label = {"bullish": "偏多", "bearish": "偏空"}
    obj_label = _label.get(obj_dir, obj_dir)
    sent_label = _label.get(sent_dir, sent_dir)

    if collision:
        # obj_dir == sent_dir（聚合層級同向），但情緒來源內部已測出矛盾——
        # 矛盾優先，摘要必須反映真背離，不得沿用「訊號一致」敘述。
        stance_sources = _distinct_source_labels(stance_pairs)
        summary = (
            f"客觀與情緒多數方向雖同為{obj_label}，"
            f"但來源 {'、'.join(stance_sources)} 對同一議題方向相反，"
            "情緒面存在矛盾，呈背離，建議交叉驗證、留意轉折。"
        )
    elif signal_type == "divergence":
        summary = (
            f"客觀數據{obj_label}、情緒類{sent_label}，"
            "呈背離，建議交叉驗證、留意轉折。"
        )
    else:
        summary = f"客觀與情緒同向{obj_label}，訊號一致。"

    # 佐證 claim_ids：各類中方向符合主導的主張 + 已確認矛盾配對涉及的主張
    # （即使 renderer 只讀 supporting_claim_ids，也能指向矛盾證據，不會漏顯示）。
    supporting_ids = (
        [sc.claim.id for sc in objective if sc.claim.direction == obj_dir]
        + [sc.claim.id for sc in sentiment if sc.claim.direction == sent_dir]
    )
    for _p in stance_pairs:
        if _p["claim_id"] not in supporting_ids:
            supporting_ids.append(_p["claim_id"])

    # issue #21 R1 退修（CEO/dev-manager 實測重現，codex 彙整）：
    # `sentiment_source_count` 不能只算 `sent_sources`（trust>=0.5 聚合投票
    # 用的來源），否則會跟 `_detect_stance_pairs`（門檻 `_STANCE_PAIR_MIN_
    # TRUST`=0.35，比聚合投票寬）抓到的矛盾配對脫鉤：一筆 trust 落在
    # [0.35, 0.5) 的情緒來源不會進 `sentiment`/`sent_sources`，但只要跟另一
    # 筆情緒來源方向相反且 `stance_fn` 判定矛盾，仍會被 `_detect_stance_
    # pairs` 抓進 `stance_pairs`——而 `stance_pairs` 非空時一律附加進本
    # result（collision 分支的 summary 甚至會具名列出這些來源）。若計數
    # 只看 `sent_sources`，會出現「count=1 顯示『單一來源主導』徽章，但
    # summary/stance_pairs 明明列出 2 個矛盾來源」的自相矛盾——徽章宣稱
    # 必須跟同一個 result 裡實際引用到的來源集合一致，故改為兩者聯集。
    # `_detect_stance_pairs` 只掃描 `_SENTIMENT_KINDS`（見其 docstring），
    # 故 stance_pairs 的來源必屬情緒類，併入不會誤把客觀類來源算進本欄位。
    stance_pair_sentiment_sources = (
        _independent_source_keys(p["source"] for p in stance_pairs) if stance_pairs else set()
    )
    sentiment_source_count = len(sent_sources | stance_pair_sentiment_sources)

    result = {
        "type": signal_type,
        "objective_direction": obj_dir,
        "sentiment_direction": sent_dir,
        "summary": summary,
        "supporting_claim_ids": supporting_ids,
        # issue #21（CISO-LOW）：純展示用透明化欄位，不影響上面任何分數/方向
        # 計算——UI 讀這個數字判斷是否顯示「單一來源主導」徽章（見
        # `CrossSourceSignalPanel`）。單一高佐證 social 源在高
        # corr/recency 時可能以 100% 票重主導 `sent_dir`，這裡把「情緒類
        # 這一輪實際有幾個獨立來源」誠實攤開，不抑制訊號本身（守 TrustForge
        # 透明哲學），只補足「這個結論的證據廣度」讓使用者自行判讀。用
        # `_normalize_source_key`（strip+casefold）正規化去重的 key count，
        # 不解 publisher 別名（如 `coindesk` vs `coindesk.com` 視為不同來
        # 源）——別名映射見 follow-up issue #72，本輪不做 canonicalization。
        "sentiment_source_count": sentiment_source_count,
    }
    if stance_pairs:
        result["stance_pairs"] = stance_pairs
        result["distinct_sources"] = _dedup_stance_pairs_by_source(stance_pairs)
    return result


def build_report(query: str, coin: str, qtype: QuestionType, brief: TrustedBrief,
                 client: BedrockClient | None = None,
                 log: ExecutionLog | None = None,
                 now_fn=time.time,
                 stance_fn: Callable[[str, str], str] | None = None,
                 scored: list[ScoredClaim] | None = None,
                 kernel_judgment: KernelJudgment | None = None,
                 locale: str = _loc.DEFAULT_LOCALE,
                 run_scope_id: str = "") -> tuple[Report, list[Evidence]]:
    """`stance_fn`：選填。供跨源 stance_pairs 偵測（Step 2.5）使用；未提供時
    （例如直接呼叫 `build_report` 的測試）會自建一份**有預算上限**的
    stance_fn（`build_stance_fn`，綁 `log.remaining()`），不會無上限直接打
    Bedrock（demo 可靠性 #32 追加 HIGH-2 修正）。`run_agent_pipeline` 會傳入
    與 Step2 `score()` **共用同一個 `_StanceBudget` 實例**的 stance_fn，
    避免兩處各自另建一份預算讓真呼叫上限實質變成兩倍。

    `scored`：選填。供跨源訊號偵測（`detect_cross_source_signal`/
    `_detect_stance_pairs`）使用的**完整、未截斷**主張全集——即 `aggregate()`
    把 `supporting`/`contrarian` 分別截斷成 `[:10]`/`[:5]` **之前**的原始
    `scored` 清單（demo 可靠性 #32 追加 HIGH 修正）。

    背景：真資料上，兩則真矛盾的情緒類新聞常因方向鮮明而落在 contrarian
    （trust < 0.5）；只要同一輪還有 >5 筆信任分更高的 contrarian（不論是否與
    該矛盾對相關），這兩則就會被 `aggregate()` 的 `[:5]` 截斷擠出
    `brief.contrarian`，若這裡改用 `brief.supporting + brief.contrarian` 偵測，
    真背離就會漏抓——且截斷是否命中純看資料量與分數分布，不是可預期、可重現
    的行為。修法：一律優先用呼叫端傳入的完整 `scored`（`aggregate()` 只重排
    不刪項，`_detect_stance_pairs` 內部本就自行以 `_STANCE_PAIR_MIN_TRUST`/
    `_SENTIMENT_KINDS` 篩選，不依賴 supporting/contrarian 的截斷結果）。
    stance 預算（`_StanceBudget`）不因輸入變大而失守——候選對變多只會讓更多
    次落入持久化快取命中（免費）或提早耗盡預算改 fail-safe 回 neutral，
    真呼叫次數上限不變。

    未提供時（例如既有測試直接呼叫 `build_report(..., brief=brief)` 不給
    `scored`）退回 `brief.supporting + brief.contrarian`（既有行為，逐字向後
    相容，不影響既有合成 fixture 單元測試）。

    W4 codex 對抗審第 8 輪根治（coin-relevance 全類別收斂）：`trust.scoring.
    aggregate(coin=)` 現在直接讓 `TrustedBrief.supporting`/`contrarian`/
    `confidence` 三者本身就是 `_matches_coin` 篩過的 coin-scoped 資料（見
    `aggregate()` docstring 完整修法史），本函式不再需要任何「額外欄位」
    或「呼叫端自行重新過濾一次」——`brief.supporting`/`brief.contrarian`/
    `brief.confidence` 讀出來就是對的，`facts`／`_direction()`／evidence／
    key_basis／`_derive_limits`／最終 `Report.contrarian`／`Report.
    confidence` 全部直接沿用。唯一例外是 `scored`（本函式獨立參數，供
    `detect_cross_source_signal` 用；不是 `brief` 的一部分）——這份原始
    全集不經過 `aggregate()`，仍需在下面用同一份 `_matches_coin(doc, coin)`
    規則過濾一次，理由與規則跟 `aggregate()` 內部一致，只是資料來源不同
    （呼叫端傳入的獨立參數，無法從 `brief` 反推）。

    `locale`：選填（N11）。使用者可見敘事三欄位（`market_judgment`、Step3
    `narrative`、`BasisItem.explanation`）的輸出語系，經 `narrative_locale.
    normalize_locale()` 收斂成 `"zh-Hant"`／`"en"`，非法值 fallback 預設中文
    （不 raise）。結構化欄位（`Report.direction`、`related_claim` 標籤）與
    log/telemetry summary 刻意不隨語系改變——見 `narrative_locale` 模組
    docstring。
    """
    client = client or BedrockClient(offline=True)
    log = log or ExecutionLog(now_fn=now_fn)
    locale = _loc.normalize_locale(locale)
    _tele_t0 = time.perf_counter()

    # #960 fail-closed：run_scope_id 是 canonical claim_id 的 run 邊界（契約 §2.2）。
    # 空 / 非字串 / 帶冒號皆視為契約違反——在發出任何 Evidence 之前 raise，確保無
    # run id 的路徑（如未傳 scope 的 pipeline.run）無法產出半成品的 canonical id。
    if (not run_scope_id or not isinstance(run_scope_id, str)
            or ":" in run_scope_id):
        raise ValueError("run_scope_id must be a non-empty, colon-free string")

    # 1. 證據清單（支撐 + 反方）
    log.record(
        "evidence.build",
        summary=f"supporting={len(brief.supporting)} contrarian={len(brief.contrarian)}",
    )
    evidence: list[Evidence] = []
    evidence_directions: list[str | None] = []
    key_basis: list[BasisItem] = []
    ev_index: dict[tuple, int] = {}   # (source, content_reference) → 去重,保留最高 trust
    judgment_tag = f"{coin} 市場判斷"
    # #960：追蹤每個 admitted Evidence idx 的「存活」sc（dedup 後留最高 trust 者），
    # 供 canonical claim_id 鑄造時對齊 survivor；以及所有 admitted（含被 dedup 丟棄者）
    # 的 (sc, related, idx) 紀錄，供 source_fingerprint→canonical 別名表建立。
    idx_sc: dict[int, ScoredClaim] = {}
    admitted_records: list[tuple[ScoredClaim, str, int]] = []

    def _add_evidence(sc: ScoredClaim, related: str) -> int:
        ev = _scored_to_evidence(sc, related)
        # key 含角色(related):支撐與反方即使同來源同引用也不共用 bucket,避免 silent drop
        key = (ev.source, ev.content_reference, related, sc.claim.direction)
        if key in ev_index:
            idx = ev_index[key]
            if ev.trust > evidence[idx].trust:   # 同來源同引用 → 留最高信任那筆
                evidence[idx] = ev
                evidence_directions[idx] = sc.claim.direction
                idx_sc[idx] = sc
            return idx
        idx = len(evidence)
        evidence.append(ev)
        evidence_directions.append(sc.claim.direction)
        ev_index[key] = idx
        idx_sc[idx] = sc
        return idx

    source_pool = scored if scored is not None else [*brief.supporting, *brief.contrarian]
    contrarian_ids = {sc.claim.id for sc in brief.contrarian}
    supporting_directions = {sc.claim.direction for sc in brief.supporting}
    supporting_source_pool = [
        sc
        for sc in source_pool
        if sc.claim.id not in contrarian_ids
        and (not supporting_directions or sc.claim.direction in supporting_directions)
    ]
    report_supporting = _with_rich_source_kind_representatives(
        brief.supporting,
        supporting_source_pool,
    )
    source_kind_distribution = _source_kind_distribution(source_pool)

    for sc in report_supporting:
        idx = _add_evidence(sc, judgment_tag)
        admitted_records.append((sc, judgment_tag, idx))
        key_basis.append(BasisItem(
            claim=sc.claim.text,
            explanation=_loc.basis_explanation(
                sc.claim.doc.source, sc.claim.doc.kind, sc.trust, locale,
            ),
            evidence_idx=[idx],
        ))
    for sc in brief.contrarian:
        idx = _add_evidence(sc, "反方／低信任訊號")
        admitted_records.append((sc, "反方／低信任訊號", idx))

    # --- #960：鑄造 canonical claim_id 並蓋章（單一鑄造點，契約 §2/§3）---
    # registry 同時涵蓋 admitted（成為 Evidence 列）與 truncated（在 scored 中但被
    # aggregate() 截斷、不成 Evidence，僅註冊供 cross_source/insight 引用）——契約 §2.3/§4.2.5。
    # effective_scored 採 scored-list 順序，碰撞消歧（`.dN`）的唯一依據即此順序（§2.3.3）。
    effective_scored: list[ScoredClaim] = (
        scored if scored is not None else [*brief.supporting, *brief.contrarian]
    )
    _fp16_collisions: dict[str, int] = {}   # 16hex → 已見相異 source 指紋數（scored 順序）
    _sp_fp16: dict[str, str] = {}           # source 指紋 → 16hex
    _sp_disamb: dict[str, str] = {}         # source 指紋 → 消歧後綴（"" 或 ".dN"）
    for _sc in effective_scored:
        _sp = _sc.claim.id
        if _sp in _sp_fp16:
            continue                        # 同一 claim 已鑄造（admitted 同時也在 scored）
        _fp16 = _claim_fingerprint16(_sc)
        _seen = _fp16_collisions.get(_fp16, 0)
        _fp16_collisions[_fp16] = _seen + 1
        _sp_fp16[_sp] = _fp16
        _sp_disamb[_sp] = "" if _seen == 0 else f".d{_seen + 1}"

    def _canonical_of(sc: ScoredClaim) -> str:
        return f"{_CLAIM_ID_SCHEME}:{run_scope_id}:{_sp_fp16[sc.claim.id]}{_sp_disamb[sc.claim.id]}"

    # survivor-per-idx canonical + source 指紋→canonical 別名（被 dedup 丟棄者 alias survivor）
    idx_to_canonical: dict[int, str] = {}
    source_to_canonical: dict[str, str] = {}
    for _sc, _related, _idx in admitted_records:
        _canonical = _canonical_of(idx_sc[_idx])
        idx_to_canonical[_idx] = _canonical
        source_to_canonical[_sc.claim.id] = _canonical
    # truncated：mint + 註冊但不成 Evidence 列（契約 §4.2.5）
    canonical_registry: set[str] = set(idx_to_canonical.values())
    for _sc in effective_scored:
        if _sc.claim.id not in source_to_canonical:
            _trunc_id = _canonical_of(_sc)
            source_to_canonical[_sc.claim.id] = _trunc_id
            canonical_registry.add(_trunc_id)
    # 蓋章 Evidence.claim_id（1:1，每列恰好一個 canonical id）
    for _idx, _ev in enumerate(evidence):
        _ev.claim_id = idx_to_canonical[_idx]
    # `source_to_canonical` 保留供 PR2b remap insight / cross_source_signal / narrative 引用。

    # 2. 我方判斷（pipeline 產生，非外部結論）
    # W4：校準信心三態 abstain（見上方 `_ABSTAIN_CALIBRATED_THRESHOLD` 常數
    # 註解）。calibrated 的誠實聲明見 `trust.scoring._calibrate_confidence`
    # docstring：簡化版分位數校準，非嚴謹 conformal coverage 保證。
    calibrated = brief.calibrated_confidence
    n_supporting = len(brief.supporting)
    # W4 codex 對抗審第 5 輪（claim-vs-source 主題收斂）[HIGH]：abstain 最小
    # 支撐門檻原本用 `n_supporting`（句級 claim 筆數）——`extract_claims()`
    # 是句級切分，同一份文件寫兩句高信任內容就會產生 2 筆 supporting
    # claim，足以通過 `n_supporting >= _ABSTAIN_MIN_SUPPORTING`，即使全部
    # 出自單一來源、無任何獨立佐證，evidence_strength 仍可能落在 abstain
    # 門檻之上而給出方向性結論——跟 dominance/indep_factor 已改用去重來源數
    # 的口徑不一致，是這條門檻唯一還在用原始 claim 計數的地方。
    # 修法：改用「去重的 supporting 來源數」（`n_indep`，下方 facts/market_
    # judgment 敘事本就需要這個值，這裡只是提前算好、重複使用同一份，不
    # 重算 trust、不新增資料源）——單源不論產生幾句 claim，仍只算 1 份獨立
    # 來源，需要 ≥2 個不同來源才可能脫離 abstain。
    # issue #106 追加修正：原本這裡跟 `_evidence_strength` 一樣是 raw
    # `{x.source for x in items}` 去重，沒套 `_normalize_source_key` 正規化
    # ——同一來源大小寫/空白變體（`"CoinDesk"` vs `" coindesk "`）仍會被
    # 誤判成 2 個獨立來源，虛增 n_indep，直接餵進下面 abstain 三態判斷，
    # 可能讓該 abstain 的判斷因為「同源灌水」沒有 abstain。改用共用的
    # `_count_independent_sources`（`_normalize_source_key` 正規化去重），
    # 跟 `aggregate_trust_by_kind`/`detect_cross_source_signal` 同一口徑。
    n_indep = _count_independent_sources(sc.claim.doc.source for sc in brief.supporting)
    if kernel_judgment is not None:
        # #734: production direction/confidence/abstain are immutable Kernel
        # outputs.  Presentation code may narrate them but must never recompute
        # or override them with the former application heuristics.
        calibrated = kernel_judgment.confidence
        n_indep = kernel_judgment.independent_sources
        is_abstain = kernel_judgment.abstain
        decision_state = kernel_judgment.decision_state
        direction = {
            "bullish": "偏多",
            "bearish": "偏空",
            "neutral": "中性",
            "unknown": "不明",
        }.get(kernel_judgment.direction, kernel_judgment.direction)
        is_low_confidence = decision_state == "low_confidence"
    else:
        # Compatibility-only path for direct presentation tests and inventoried
        # callers.  Production entrypoints always provide kernel_judgment.
        is_abstain = (
            calibrated < _ABSTAIN_CALIBRATED_THRESHOLD
            or n_indep < _ABSTAIN_MIN_SUPPORTING
        )
        is_low_confidence = (not is_abstain) and calibrated < 0.5
        decision_state = (
            "abstain"
            if is_abstain
            else ("low_confidence" if is_low_confidence else "normal")
        )
        direction = _direction(brief.supporting, all_scored=scored)
    # W4 codex 對抗審第 2 輪 [HIGH-1]：三態字面值下放給 `schema.Report.
    # decision_state`，供 UI／analyze.json 消費端結構化辨態（見該欄位註解），
    # 不必再各自重算 calibrated 門檻。
    facts = [sc.claim.text for sc in brief.supporting if sc.claim.doc.kind in OBJECTIVE_KINDS]

    if is_abstain:
        # 證據不足：不代客決策，但仍嘗試從價格趨勢給出參考方向（Issue #367）。
        # 若 _direction 能從客觀 OHLCV 算出偏多/偏空/中性，附上「僅供參考」提示；
        # 若連方向都判不出（"不明"），退回原本的純中性文案。
        if direction == "不明":
            head = _loc.abstain_unknown_direction(coin, n_supporting, calibrated, locale)
        else:
            if qtype == QuestionType.HYPOTHESIS:
                head = _loc.abstain_hypothesis(query, direction, locale)
            elif qtype == QuestionType.COMPARISON:
                head = _loc.abstain_comparison(coin, direction, locale)
            else:
                head = _loc.abstain_general(coin, direction, locale)
    else:
        if qtype == QuestionType.HYPOTHESIS:
            head = _loc.judgment_hypothesis(query, coin, direction, locale)
        elif qtype == QuestionType.COMPARISON:
            head = _loc.judgment_comparison(coin, direction, locale)
        else:
            head = _loc.judgment_general(coin, direction, locale)
        if is_low_confidence:
            head += _loc.low_confidence_suffix(locale)
    market_judgment = head + _loc.judgment_stats_suffix(
        n_indep, brief.confidence, calibrated, locale,
    )
    log.record(
        "judgment.derive",
        params={
            "direction": direction, "indep_sources": n_indep,
            "calibrated_confidence": round(calibrated, 4),
            "abstain": is_abstain, "low_confidence": is_low_confidence,
            "decision_state": decision_state,
        },
    )

    # 2.5 跨源訊號偵測（純演算法，在 Bedrock 行文前完成）
    # stance_fn：未由呼叫端提供時（例如測試直接呼叫 build_report），自建一份
    # 有預算上限的 stance_fn（demo 可靠性 #32 追加 HIGH-2 修正——原本這裡另建
    # 一份「無預算」的 cached_stance_fn，線上模式 cache miss 會無上限直接打
    # Bedrock，且發生在 Step2 成本收割之後，成本永遠沒進帳本）。離線一律傳
    # None 給 build_stance_fn，讓底層 cached_stance_fn 只讀持久化快取
    # （demo/sample_data/stance_cache.json），快取 miss 時 fail-safe 回
    # "neutral"，不即時打 Bedrock、不 raise。
    if stance_fn is None:
        from ..trust.scoring import build_stance_fn  # 延遲匯入避免頂層循環
        stance_fn = build_stance_fn(
            # #9 online-stance 預算配額硬化：判斷用 `stance_offline`（非
            # `offline`）——`pipeline.run()` 在敘事離線但 online-stance 開關
            # 生效時，會建立 `stance_offline=False` 的 client，讓這裡改傳真
            # client 給 stance_fn。`getattr` 預設回退到 `client.offline`：
            # 測試用的 duck-typed fake client（如 test_multistep.py 的
            # `FakeBedrockClient`）未必有 `stance_offline` 屬性，行為維持
            # 與加入這個屬性前逐字相同。
            stance_client=(
                None if getattr(client, "stance_offline", client.offline) else client
            ),
            stance_remaining_time_fn=log.remaining,
        )
    # W4 codex 對抗審第 7 輪 [HIGH]（coin-relevance 最後一條輸入路徑）：
    # `detect_cross_source_signal`（含其內部 `_detect_stance_pairs`）吃的
    # `scored` 是本函式獨立參數（供偵測用的完整、未截斷主張全集），不是從
    # `brief` 算出來的——即使第 8 輪已讓 `brief.supporting`/`contrarian`
    # 天生 coin-scoped，這份獨立參數仍需在此用同一份 `_matches_coin(doc,
    # coin)` 規則過濾一次（保留本幣相關 + 全市場通用，只排除明確他幣），
    # 才能避免高信任他幣客觀/新聞主張混進跨源訊號；函式本身的 trust/kind
    # 篩選規格不動。
    cross_signal_input = [
        sc
        for sc in (scored if scored is not None else brief.supporting + brief.contrarian)
        if _matches_coin(sc.claim.doc, coin)
    ]
    cross_signal = detect_cross_source_signal(
        cross_signal_input,
        stance_fn=stance_fn,
    )
    # 收割本步驟（Step2.5）可能產生的 stance 呼叫成本，避免漏記帳／殘留到下一輪。
    _harvest_stance_cost_events(client, log)

    # 3. Bedrock 行文（Step 3：帶 claim_id 溯源；離線為佔位，結構不依賴它）
    # 建立 claim_id → 摘要對照，供 prompt 強制引用。`brief.supporting` 已是
    # coin-scoped（見 `aggregate()`），不會把他幣高信任 claim 塞進 LLM
    # prompt 的「事實」區塊。
    safe_query, query_injection_suspected = _untrusted_prompt_text(query)
    claim_data = []
    claim_injection_suspected = False
    for sc in brief.supporting[:8]:
        safe_claim, suspected = _untrusted_prompt_text(sc.claim.text, max_length=100)
        claim_injection_suspected = claim_injection_suspected or suspected
        claim_data.append({"claim_id": sc.claim.id, "text": safe_claim})
    # 若有跨源訊號，指示 LLM 只敘述已算好的 summary，不得自行判斷背離/共識
    _cross_note = ""
    if cross_signal:
        _cross_note = _loc.cross_signal_note(cross_signal["summary"], locale)
    if is_abstain:
        # abstain：不引導 LLM 產生任何方向性推論，只請它敘述「證據不足」現況。
        _instruction = _loc.abstain_instruction(locale)
    else:
        _instruction = _loc.narrative_instruction(locale)
    untrusted_data = {
        "question": safe_query,
        "claims": claim_data,
    }
    prompt = (
        _loc.untrusted_data_preamble(locale)
        + "<UNTRUSTED_DATA_JSON>\n"
        f"{json.dumps(untrusted_data, ensure_ascii=False)}\n"
        "</UNTRUSTED_DATA_JSON>\n"
        + _loc.prompt_header(coin, qtype.value, market_judgment, locale)
        + f"{_cross_note}{_instruction}"
    )
    _t_step3 = log._now()
    narrative_service_failed = False
    try:
        _result_step3 = client.complete(system=_loc.system_prompt(locale), prompt=prompt)
        narrative = _result_step3.text
        # 離線也會走到這（complete() 離線回傳 token=0 的佔位結果），故這裡永遠
        # 記一筆：線上是真花費，離線是 $0 ——帳本兩種情況都看得到本次 Step3 呼叫。
        log.record_llm_cost(
            _result_step3.model_id,
            _result_step3.input_tokens,
            _result_step3.output_tokens,
            estimate_cost(
                _result_step3.model_id, _result_step3.input_tokens, _result_step3.output_tokens
            ),
        )
        if client.offline:
            narrative = _loc.offline_narrative(locale)
    except Exception:
        # Bedrock 失敗 → 用結構化判斷當行文降級,不中斷管線(且仍記錄此步 log)
        # 呼叫未成功、無 usage 數字 → 不記成本
        narrative = _loc.degraded_narrative(market_judgment, locale)
        narrative_service_failed = True
    _step3_elapsed = round(log._now() - _t_step3, 2)
    log.record(
        "bedrock.complete",
        params={"step": 3, "task": "narrative_with_citations",
                "model": client.config.model_id or "offline",
                "prompt_injection_suspected": (
                    query_injection_suspected or claim_injection_suspected
                ),
                "step_elapsed_sec": _step3_elapsed},
        summary=f"帶 claim_id 溯源行文；耗時 {_step3_elapsed}s；輸入 {len(brief.supporting)} 條主張",
    )
    if is_abstain:
        # W4 codex 對抗審第 2 輪 [HIGH-2] 修正：舊版即使 abstain 仍把 Step3
        # `narrative`（LLM 自由生成的行文）塞進 inferences——prompt 的
        # `_instruction` 雖已要求「不得使用方向性字眼」，但那只是軟性指示，
        # 對真實（非 offline）LLM 呼叫沒有確定性保證，一旦模型不遵守，
        # abstain 報告的 inferences 仍可能夾帶「上漲/下跌」等方向性結論，
        # 跟 market_judgment 已經棄權的立場矛盾。
        # 修法：abstain 態的 inferences **完全不採用 LLM narrative**，改用
        # 純確定性模板——只點出「有幾筆客觀事實訊號可查」，不引用 fact 原文
        # （fact 原文本身可能含方向詞，如「BTC 上漲」，但那是「觀察訊號」，
        # 允許在 facts 欄位透明呈現；inferences 是推論層，必須保證零方向詞）。
        # Step3 呼叫本身**仍照跑**（不砍，見下方 client.complete()）——保留
        # pipeline「Step3 必呼叫、≥2 筆 bedrock.complete log」的既有契約與
        # 成本可觀測性不回歸，只是其輸出不會流入最終報表。
        _obs_line = _loc.abstain_inference_observation(len(facts), locale)
        inferences = [
            _loc.abstain_inference_strength(n_supporting, calibrated, locale),
            _obs_line,
        ]
    else:
        inferences = [
            _loc.inference_direction_line(direction, n_indep, locale),
            narrative.strip(),
        ]

    limits, flips = _derive_limits(brief)
    if narrative_service_failed:
        limits.append(
            "本次線上模型生成失敗；報告已降級為結構化規則與可追溯證據結果。"
        )
    # W4 codex 對抗審第 3 輪 [HIGH]：cross_signal 的 summary 在有真實客觀/情緒
    # 主導方向可判定時（`objective_direction`/`sentiment_direction` 皆非
    # None——consensus／divergence／collision 三種類型皆屬此類，見
    # `detect_cross_source_signal` 內的 `_label` 對照），summary 文字必含
    # 「偏多/偏空」方向標籤，abstain 態若仍無條件塞進 Report，會透過
    # Markdown「跨源訊號」區塊／Web `_render_cross_signal` 洩漏方向性結論，
    # 跟 abstain「不足以判斷、不下方向結論」的立場矛盾。修法：abstain 態下，
    # 只中和「真的帶方向標籤」的 cross_signal（設 None，不代客決策），純
    # `_stance_pair_signal()` 備援訊號（`objective_direction`/
    # `sentiment_direction` 皆為 None，summary 只講「來源方向相反、建議交叉
    # 驗證」，不帶偏多/偏空標籤）維持不變——那本質是「觀察到來源分歧」的
    # 事實陳述，不是方向結論，跟 abstain 立場不衝突，也是既有測試
    # （`test_tier2_divergence.py`／`test_stance_budget_sharing.py`）在證據
    # 稀薄（甚至 supporting=0）情境下仍預期能拿到的訊號，不得砍掉
    # （回歸鎖：blanket None 會誤殺這條，已用真跑測試驗證過）。
    report_cross_signal = cross_signal
    if is_abstain and cross_signal and cross_signal.get("objective_direction") is not None:
        report_cross_signal = None
    # Phase 1 獨特洞察層（#24/#15/#21/#72）：純函式、免 LLM，只讀 score() 已
    # 算好的 `scored` 全集 + `brief`，產出非顯而易見、可驗證的洞察（聰明錢
    # 背離 / 操縱爆量 / 來源自我矛盾）。每條洞察攜「兩個以上貢獻來源 + 方向 +
    # 強度 + 資料覆蓋閘」，覆蓋不足標「無法判定」，絕不硬湊（承接 Phase 0
    # 三態誠實合約）。與 cross_source_signal 同層級、互補但不重疊——cross_source
    # 是客觀 vs 情緒背離，這裡是更深、更可被抽查的維度。
    from ..trust.insights import detect_insights
    report_insights = detect_insights(brief, scored if scored is not None else brief.supporting + brief.contrarian, coin, qtype)

    # D1.5 假設驗證題型結構化正反方：顯式 pro/con 證據帳本綁定 Evidence List
    # （pro = 支持方 evidence 索引；con = 反方 evidence 索引），並附信心限制聲明
    # （不過度宣稱預測力，承接 Phase 0 誠實定位）。僅在 HYPOTHESIS 題型計算。
    hypothesis_ledger = None
    if qtype == QuestionType.HYPOTHESIS:
        pro_idx = [i for i, ev in enumerate(evidence) if ev.related_claim == judgment_tag]
        con_idx = [i for i, ev in enumerate(evidence) if ev.related_claim == "反方／低信任訊號"]
        confidence_limit = (
            "本驗證為「假設對照」而非預測：僅基於現有證據的正反方對照，"
            "不宣稱預測力；證據強度有限（校準值偏低或獨立來源不足）時，"
            "正反方對照僅供參考，不構成方向性結論。"
        )
        hypothesis_ledger = {"pro": pro_idx, "con": con_idx, "confidence_limit": confidence_limit}
        # 不過度宣稱：把正反方對照與信心限制明寫進報告（abstain 時放 limits，
        # 否則放 inferences），確保 reviewer 一眼看到「這只是對照、不是預測」。
        ledger_line = (
            f"假設驗證正反方對照：支持方 {len(pro_idx)} 筆證據、反方 {len(con_idx)} 筆證據"
            f"（詳見證據清單索引 E{pro_idx[0] if pro_idx else '—'}…／"
            f"E{con_idx[0] if con_idx else '—'}…）。{confidence_limit}"
        )
        if is_abstain:
            limits.append(ledger_line)
        else:
            inferences.append(ledger_line)

    # #583 注入 glossary term 標註：對 market_judgment 做 deterministic
    # 詞彙標註，供前端渲染 glossary popover。
    _annotations = annotate_terms(market_judgment)
    term_annotations = [ann.to_dict() for ann in _annotations]

    # #862 非破壞式事實聚合：在 evidence list 完整建立後，計算聚合群組。
    # 純呈現層 post-processing，不修改 evidence list 本身。
    ev_groups = group_evidence(evidence, directions=evidence_directions)
    ev_groups_dicts = [g.to_dict() for g in ev_groups]

    # #862 facts 去重：同群組（≥2 筆）只留一條聚合摘要，避免重複事實。
    # 重建 facts：群組用聚合摘要取代原始逐筆
    aggregated_facts: list[str] = []
    _seen_fact_texts: set[str] = set()
    for g in ev_groups:
        if len(g.member_indices) < 2:
            # 單筆群組：若為客觀事實，保留原始文字
            idx = g.member_indices[0]
            ev = evidence[idx]
            if ev.kind in OBJECTIVE_KINDS and ev.related_claim == judgment_tag:
                # 從原始 facts 中找出該 evidence 對應的 fact text
                fact_text = ev.content_reference
                if fact_text not in _seen_fact_texts:
                    aggregated_facts.append(fact_text)
                    _seen_fact_texts.add(fact_text)
        else:
            # 多筆群組：只取客觀事實類，產生聚合摘要
            obj_members = [i for i in g.member_indices
                           if evidence[i].kind in OBJECTIVE_KINDS
                           and evidence[i].related_claim == judgment_tag]
            if obj_members:
                rep_ev = evidence[g.representative_idx]
                source = rep_ev.source
                count = len(g.member_indices)
                indices_str = ", ".join(f"E{i}" for i in g.member_indices[:5])
                if count > 5:
                    indices_str += "…"
                if g.value_range and g.trend:
                    trend_text = {"rising": "上升趨勢", "falling": "下降趨勢",
                                  "stable": "持平"}.get(g.trend, "")
                    aggregated_facts.append(
                        f"{g.value_range}（{trend_text}），來源 {source}，"
                        f"{count} 筆觀測 [{indices_str}]"
                    )
                elif g.value_range:
                    aggregated_facts.append(
                        f"{g.value_range}，來源 {source}，"
                        f"{count} 筆觀測 [{indices_str}]"
                    )
                else:
                    # 無法提取數值，取代表文字
                    aggregated_facts.append(rep_ev.content_reference)
    # 若聚合後 facts 為空（邊界情況），退回原始 facts
    if not aggregated_facts:
        aggregated_facts = facts

    # #862 key_basis 面向多樣性：同群組只保留一條 BasisItem，帶全組 evidence_idx。
    if ev_groups:
        # 建立 evidence_idx → group 的反查
        _idx_to_group: dict[int, int] = {}
        for gi, g in enumerate(ev_groups):
            for mi in g.member_indices:
                _idx_to_group[mi] = gi
        # 去重：同一群組只留第一條出現的 BasisItem，evidence_idx 擴充為全組
        _seen_groups: set[int] = set()
        candidates: list[tuple[BasisItem, tuple[str, str] | None]] = []
        for bi in key_basis:
            if not bi.evidence_idx:
                candidates.append((bi, None))
                continue
            primary_idx = bi.evidence_idx[0]
            grp_id = _idx_to_group.get(primary_idx)
            if grp_id is not None and grp_id in _seen_groups:
                continue  # 同群組已有代表，跳過
            ev_rep = evidence[primary_idx]
            sk_key = (_normalize_source_key(ev_rep.source), ev_rep.kind)
            if grp_id is not None:
                _seen_groups.add(grp_id)
                # 擴充 evidence_idx 為全組索引
                g = ev_groups[grp_id]
                if len(g.member_indices) >= 2:
                    bi = BasisItem(
                        claim=bi.claim,
                        explanation=bi.explanation,
                        evidence_idx=list(g.member_indices),
                    )
            candidates.append((bi, sk_key))

        # 先穩定選出最多三個不同面向，再回填其餘候選。若實際只有一至
        # 兩個面向，回填仍會保留有效項目，不會因長度永遠小於三而全數丟棄。
        diverse: list[tuple[BasisItem, tuple[str, str] | None]] = []
        deferred: list[tuple[BasisItem, tuple[str, str] | None]] = []
        seen_facets: set[tuple[str, str]] = set()
        for candidate in candidates:
            _, facet = candidate
            if facet is None or facet in seen_facets or len(diverse) >= 3:
                deferred.append(candidate)
            else:
                seen_facets.add(facet)
                diverse.append(candidate)
        key_basis = [bi for bi, _ in (*diverse, *deferred)]

    # #960：蓋章 BasisItem.claim_ids（與 evidence_idx 對齊；二者須一致——契約 §4.1）。
    # 在最終 key_basis 定型後填入（diversity/group 重構會重建 BasisItem，故須在此處蓋章）。
    for _bi in key_basis:
        _bi.claim_ids = [idx_to_canonical[_i] for _i in _bi.evidence_idx]

    # 使用聚合後的 facts
    facts = aggregated_facts

    admitted_source_kind_distribution = _source_kind_distribution(idx_sc.values())
    excluded_source_kind_counts = {
        kind: total - admitted_source_kind_distribution.get(kind, 0)
        for kind, total in source_kind_distribution.items()
        if total > admitted_source_kind_distribution.get(kind, 0)
    }

    report = Report(
        coin=coin, question_type=qtype.value, question=query,
        market_judgment=market_judgment, facts=facts, inferences=inferences,
        key_basis=key_basis, confidence=brief.confidence,
        limits=limits, could_flip=flips,
        contrarian=[sc.claim.text for sc in brief.contrarian],
        generated_at=iso_utc(now_fn()),
        direction=direction,
        cross_source_signal=report_cross_signal,
        source_kind_distribution=admitted_source_kind_distribution,
        excluded_source_kind_counts=excluded_source_kind_counts,
        insights=report_insights,
        hypothesis_ledger=hypothesis_ledger,
        # W4 codex 對抗審第 2 輪 [HIGH-1]：結構化校準值＋三態，供 UI／
        # analyze.json 消費端辨態，不必再各自重算門檻（見 schema.Report
        # 欄位註解）。
        calibrated_confidence=calibrated,
        decision_state=decision_state,
        term_annotations=term_annotations,
        evidence_groups=ev_groups_dicts,
    )
    log.record("report.done", summary=f"facts={len(facts)} basis={len(key_basis)} evidence={len(evidence)}")
    # --- telemetry: record build_report invocation ---
    try:
        from ..module_telemetry import record_invocation as _rec_inv
        _tele_elapsed = (time.perf_counter() - _tele_t0) * 1000.0
        _rec_inv("agent.build_report", _tele_elapsed, "success",
                 metadata={"coin": coin, "evidence_count": len(evidence)})
    except Exception:
        pass
    # #960 no-dangling（契約 §4.2.2）：緊鄰 return 前、telemetry 之後（此處與 return
    # 之間不得再有其他程式）。任何被引用的 canonical id 必須存在於本 run 的 registry。
    # PR2a 範圍僅涵蓋已 canonicalized 的欄位——BasisItem.claim_ids 與 Evidence.claim_id；
    # insight / cross_source_signal / narrative 引用的 remap 與其 dangling 檢查留 PR2b
    # （未 remap 前它們仍持 raw source 指紋，於此檢查會誤判，故暫不納入）。
    for _bi in key_basis:
        for _cid in _bi.claim_ids:
            if _cid not in canonical_registry:
                raise ValueError(
                    f"dangling claim_id {_cid!r} in BasisItem is not in the canonical registry"
                )
    for _ev in evidence:
        if _ev.claim_id and _ev.claim_id not in canonical_registry:
            raise ValueError(
                f"dangling Evidence.claim_id {_ev.claim_id!r} is not in the canonical registry"
            )
    return report, evidence


# ---------------------------------------------------------------------------
# 顯式 3 步驟 Agent Pipeline（P0-3）
# ---------------------------------------------------------------------------

def run_agent_pipeline(
    query: str,
    coin: str,
    qtype: QuestionType,
    docs: list[Document],
    client: BedrockClient | None = None,
    log: ExecutionLog | None = None,
    now_fn=time.time,
    ledger_persistence_observer: Callable[[bool, float], None] | None = None,
    run_scope_id: str = "",
) -> tuple[Report, list[Evidence]]:
    """三步驟顯式推理鏈。

    Step 1 — Claim 抽取（Bedrock #1 或 regex fallback）
    Step 2 — pipeline 評分聚合（反作弊：純演算法，不呼叫 Bedrock）
    Step 3 — 帶 claim_id 溯源行文（Bedrock #2，by build_report 內部執行）
    Step 4 — 限制複審（Bedrock #3，選用，預算剩餘 > 60s 才執行）

    Execution Log 保證 ≥2 筆 bedrock.complete 記錄（Step1 + Step3）。
    """
    from ..trust.scoring import build_stance_fn  # 延遲匯入避免頂層循環

    client = client or BedrockClient(offline=True)
    log = log or ExecutionLog(now_fn=now_fn)
    # comparison 兩幣共用同一個 log（見 pipeline.run_comparison）：若帳本收尾直接
    # 掃「log.events 全部」的 llm.cost，第二輪會把第一輪已寫過的成本又算一次
    # （帳本累計變成 2A+B）。記下本輪開始時的事件數，收尾只彙總「本輪新增」的部分。
    _log_events_start_idx = len(log.events)

    # ------------------------------------------------------------------
    # Step 1: Claim 抽取（Bedrock #1 / regex fallback）
    # ------------------------------------------------------------------
    log.record("pipeline.step1.start", summary=f"docs={len(docs)}；準備 LLM claim 抽取")
    _t1 = log._now()
    claims = client.extract_claims_with_llm(docs, log=log)
    _step1_elapsed = round(log._now() - _t1, 2)

    # 區分是否真正走了 LLM（offline / 未設模型 → regex fallback）
    _is_llm_step1 = not (client.offline or not client.config.model_id)
    log.record(
        "bedrock.complete",
        params={
            "step": 1,
            "task": "claim_extraction",
            "model": client.config.model_id or "offline/regex-fallback",
            "step_elapsed_sec": _step1_elapsed,
            "llm_active": _is_llm_step1,
            # 保留本次真正抽取出的 provenance ID，供驗證端 fail-closed 比對；
            # 線上 LLM 的 #llmN ID 無法由 deterministic extractor 重建。
            "claim_ids": [claim.id for claim in claims],
        },
        summary=(
            f"Step1 抽取 {len(claims)} 條主張；"
            f"輸入 {len(docs)} 份文件；"
            f"耗時 {_step1_elapsed}s；"
            f"{'LLM 模式' if _is_llm_step1 else 'regex fallback'}"
        ),
    )

    # ------------------------------------------------------------------
    # Step 2: 判斷形成（純 pipeline，不呼叫 Bedrock）
    # ------------------------------------------------------------------
    log.record("pipeline.step2.start", summary="pipeline 評分 + 聚合（反作弊純演算法）")
    # codex 對抗審修正（PR #48 second-round HIGH，呼應 #12/#24 不虛增）：
    # 原本 `now_ts = max(d.ts for d in docs)`——若某份文件帶偽造/異常的
    # 未來時間戳，它會直接**變成 `now_ts` 本身**（因為它是全池最大值）。
    # 此時該偽造文件相對 `now_ts` 的年齡是 0（`age_h == 0`，不是負值），
    # `_recency_decay` 的 age<0→0.5 全域防禦完全不會觸發（它不是「>now」，
    # 而是「=now」）——偽造文件反而白拿滿分 recency=1.0，還把其餘合法
    # 文件相對這個被撐高的參考時間顯得更舊，扭曲全池的時效排序。
    # 修法：參考時間不得超過真實牆鐘（`now_fn()`，production 是
    # `time.time()`；測試可注入固定值代表「當下」）——
    #   - 偽造未來戳：`max(docs.ts)` 被 cap 回牆鐘 → 該文件變回「>now_ts」
    #     → 觸發既有的 age<0→0.5 防禦，真的被抓到。
    #   - 離線 fixture（如 HOYA 歷史資料，ts 遠早於真實牆鐘）：
    #     `max(docs.ts) < now_fn()` → 仍取 `max(docs.ts)`（dataset-relative，
    #     沿用既有離線行為，不受影響）。
    #   - 線上真資料：`max(docs.ts) ≈ now_fn() ≈ 牆鐘`，不受影響。
    #
    # codex 對抗審 HIGH（third-round，呼應 #12/#24）：`d.ts` 可能是
    # `float('nan')`（壞資料/on-chain/cache 來源皆可能夾帶）。NaN 與任何數
    # 比較恆為 False，Python 的 `max`/`min` 在混入 NaN 時**依引數/疊代順序
    # 決定結果是否被污染成 NaN**（實測：NaN 排在後面時 `max` 才會回傳
    # NaN），若 `now_ts` 因此變成 NaN，會繼續往下游 `_recency_decay`／
    # `score()` 傳播，重演同一個滿分污染問題。修法：先濾掉非有限
    # （`math.isfinite`）的 `d.ts` 再取 `max`，全部非有限則 fallback 牆鐘，
    # `now_ts` 永遠是有限值。
    _wall_clock = now_fn()
    _finite_ts = [d.ts for d in docs if math.isfinite(d.ts)]
    now_ts = min(max(_finite_ts, default=_wall_clock), _wall_clock)
    # W1.5（#15）：線上帶真 client（cache miss 才即時呼叫 Bedrock）；離線／未設模型帶
    # None（CEO+codex 對抗審修正：None 不代表關掉語意矛盾閘，score() 仍會建立
    # cached_stance_fn(None) 去讀持久化快取 demo/sample_data/stance_cache.json，
    # 只有在快取也 miss 時才 fail-safe 回 neutral——離線 demo 才看得到 #15 的修復）。
    # 第二輪對抗審修正：接 log.remaining()（即時剩餘的官方 15 分鐘執行時間）當
    # stance 呼叫的時間預算，跟配對硬上限一起防 O(n²) 呼叫吃光整條執行窗口。
    # 第三輪對抗審修正（demo 可靠性 #32 追加 HIGH-2）：先建「一份」共用的
    # budgeted stance_fn，Step2 的交叉佐證矛盾閘與 Step3（build_report 內的
    # 跨源 stance_pairs 偵測）共用同一個 `_StanceBudget` 實例——避免兩處各自
    # 另建一份預算，讓「單次執行真呼叫 Bedrock 的配對硬上限」實質變成兩倍、
    # 也讓兩處共用同一份即時剩餘時間（`log.remaining()`）判斷。
    shared_stance_fn = build_stance_fn(
        # #9 online-stance 預算配額硬化：見上方 `build_report` 內同款判斷的
        # docstring——判斷用 `stance_offline`（非 `offline`），`getattr`
        # 預設回退到 `client.offline` 維持向後相容。
        stance_client=(
            None if getattr(client, "stance_offline", client.offline) else client
        ),
        stance_remaining_time_fn=log.remaining,
    )
    # W2 啟用（gray `docs/archive/plans/PLAN-w2-enable-final.md`）：truth-discovery 動態來源
    # 信譽由 PR #29 打底、預設關（`scoring.score` 的 `dynamic_reputation:
    # bool = False`，見該處 docstring），本行是全 repo 生產唯一呼叫點正式
    # 開啟。$0 確定性：K 輪迭代與下方 `_reputation_evidence` 都吃同一份
    # `shared_stance_fn`（即上面已跟 Step 2.5 stance_pairs 偵測共用的同一顆
    # `_StanceBudget`），不會多打一次 Bedrock（見 `score()` docstring、
    # `tests/test_stance_budget_sharing.py`）。小樣本守門（<3 獨立佐證來源
    # 強制 α=1，見 `scoring.py` `_iterate_source_reputation`）本身就是失效
    # 安全，故不做 feature flag，直接預設開。
    resolved_direction = resolve_direction(
        tuple(to_kernel_claim(claim) for claim in claims),
        coin=coin,
        pit_epoch=now_ts,
    )
    kernel_output, scored, brief, kernel_judgment = run_authoritative_judgment(
        claims,
        pit_epoch=now_ts,
        coin=coin,
        query=query,
        direction=resolved_direction,
        stance_fn=shared_stance_fn,
        offline=getattr(client, "offline", False),
    )
    # Resolution 跑完、Step2 交叉佐證矛盾閘可能觸發的 stance 呼叫都已發生，
    # 這裡統一收割進 log、並清空 client.cost_events，避免下個 run 重複計費。
    _harvest_stance_cost_events(client, log)
    report_scored, report_brief = scored, brief
    log.record(
        "judgment.derive",
        params={
            "judgment_source": "trustforge_core.run_kernel",
            "contract_version": kernel_output.contract_version,
            "supporting": kernel_output.supporting_count,
            "confidence": kernel_output.confidence,
            "direction": kernel_output.direction,
            "abstain": kernel_output.abstain,
            "decision_state": kernel_output.decision_state,
            "reason_codes": kernel_output.reason_codes,
            "provider_calls": 0,
            "cost_usd": 0.0,
        },
        summary="Step2 canonical kernel judgment complete",
    )

    # Observation has no activation authority.  The active result remains the
    # legacy result byte-for-byte regardless of shadow success or failure.
    # ------------------------------------------------------------------
    # Step 3: 帶溯源行文（Bedrock #2，由 build_report 執行並記錄 log）
    # ------------------------------------------------------------------
    log.record("pipeline.step3.start", summary="準備 Bedrock 行文（Step3）")
    report, evidence = build_report(
        query=query, coin=coin, qtype=qtype, brief=report_brief,
        client=client, log=log, now_fn=now_fn,
        stance_fn=shared_stance_fn,
        # demo 可靠性 #32 追加 HIGH 修正：傳完整（未截斷）scored 全集做跨源訊號
        # 偵測，避免 aggregate() 的 supporting[:10]/contrarian[:5] 截斷把真矛盾
        # 配對擠出偵測範圍（見 build_report docstring）。
        scored=report_scored,
        kernel_judgment=kernel_judgment,
        run_scope_id=run_scope_id,
    )
    # 防禦性再收割一次（demo 可靠性 #32 追加 cost-integrity HIGH 修正）：
    # build_report() 內部已在 Step2.5 偵測後自行收割一次，這裡屬於
    # belt-and-suspenders——`run_agent_pipeline()` 自身保證每輪結束時
    # client.cost_events 必為空，不依賴 build_report() 未來重構仍維持
    # 內部收割時機正確；目前正常路徑下這裡永遠是 no-op（cost_events 已空）。
    _harvest_stance_cost_events(client, log)

    # ------------------------------------------------------------------
    # Step 4: 限制複審（Bedrock #3，選用）
    # ------------------------------------------------------------------
    _should_step4 = (
        not client.offline
        and client.config.model_id
        and log.remaining() > _STEP4_MIN_BUDGET_SEC
    )
    if _should_step4:
        log.record("pipeline.step4.start", summary=f"準備限制複審；剩餘預算 {log.remaining():.0f}s")
        _review_prompt = (
            f"以下是一份加密市場分析報告的「已知限制」段落：\n"
            f"{chr(10).join('- ' + x for x in report.limits)}\n\n"
            f"市場判斷：{report.market_judgment}\n"
            f"信心：{report.confidence:.2f}\n\n"
            f"請審查限制說明是否完整。若有遺漏，補充 JSON list of strings；"
            f"若已充分，輸出 JSON list 包含單一元素 \"{_STEP4_LIMIT_SENTINEL}\"。"
            f"只輸出 JSON list，不要其他文字。"
        )
        _t4 = log._now()      # 先賦值,確保 except 路徑也能算耗時(不依賴 dir() 探測)
        _additions: list[str] = []
        try:
            _result_step4 = client.complete(system=_STEP4_SYSTEM, prompt=_review_prompt)
            log.record_llm_cost(
                _result_step4.model_id,
                _result_step4.input_tokens,
                _result_step4.output_tokens,
                estimate_cost(
                    _result_step4.model_id, _result_step4.input_tokens, _result_step4.output_tokens
                ),
            )
            _review_raw = _result_step4.text
            _s = _review_raw.find("[")
            _e = _review_raw.rfind("]") + 1
            if _s != -1 and _e > 0:
                import json as _json
                _parsed = _json.loads(_review_raw[_s:_e])
                _additions = [
                    str(x) for x in _parsed
                    if isinstance(x, str) and x != _STEP4_LIMIT_SENTINEL and x.strip()
                ]
                if _additions:
                    report.limits.extend(_additions)
        except Exception:
            _additions = []
            # 呼叫未成功、無 usage 數字 → 不記成本
        _step4_elapsed = round(log._now() - _t4, 2)
        log.record(
            "bedrock.complete",
            params={"step": 4, "task": "limitation_review",
                    "model": client.config.model_id or "offline",
                    "step_elapsed_sec": _step4_elapsed},
            summary=f"Step4 限制複審；補充 {len(_additions)} 條；耗時 {_step4_elapsed}s",
        )

    # ------------------------------------------------------------------
    # 帳本：run 收尾寫一筆跨 run 持久化成本紀錄（append-only，不影響 report/evidence）
    # 只彙總「本輪開始後新增」的 llm.cost 事件（見 _log_events_start_idx），
    # 避免 comparison 兩幣共用同一 log 時，第二輪把第一輪已寫過的成本重複計入。
    # ------------------------------------------------------------------
    _llm_calls = [
        {
            "model": e["params"].get("model"),
            "tokens_in": e["params"].get("tokens_in", 0),
            "tokens_out": e["params"].get("tokens_out", 0),
            "cost_usd": e["params"].get("cost_usd", 0.0),
        }
        for e in log.events[_log_events_start_idx:]
        if e["tool"] == "llm.cost"
    ]
    _run_total_cost_usd = round(sum(c["cost_usd"] for c in _llm_calls), 6)
    _persisted = append_run({
        "ts": iso_utc(now_fn()),
        "question_type": qtype.value,
        "coin": coin,
        "offline": client.offline,
        "calls": _llm_calls,
        "total_cost_usd": _run_total_cost_usd,
    })
    if ledger_persistence_observer is not None:
        ledger_persistence_observer(_persisted, _run_total_cost_usd)
    # codex HIGH 追加（記帳完整性）：append_run() 的 primary+fallback 都失敗
    # 時（storage 唯讀/滿/不可用），這筆真的花掉的成本從未進到帳本——
    # daily_cost_usd() 讀不到，若不做點什麼，guard 會一直看到「未用預算」，
    # 讓後續重複請求無限繞過 $3/day cap（見 budget_guard._UnledgeredSpend
    # docstring）。這裡不影響 report/evidence（帳本仍是非關鍵 side-channel，
    # 不中斷已算完的分析），只把花費補記到 process-local fail-closed 計數器，
    # 並留一筆可觀測的 warning log。
    if not _persisted and _run_total_cost_usd > 0:
        record_unledgered_spend(_run_total_cost_usd)
        logging.warning(
            "run_agent_pipeline: ledger.append_run() 持久化失敗（coin=%s，"
            "total_cost_usd=%s），已改記到 process-local 未記帳花費計數器"
            "（budget_guard._UNLEDGERED_SPEND），確保每日 cap 仍算得到這筆花費",
            coin, _run_total_cost_usd,
        )

    return report, evidence
