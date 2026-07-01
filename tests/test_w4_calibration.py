"""W4：校準信心 + abstain（棄權）驗收測試。

codex 對抗審第 1 輪 [HIGH] 修正後的版本：第一版直接把「裸加權均值
confidence」塞進分位數映射表——但 `confidence` 定義上只取
`trust >= support_threshold`（預設 0.50）的 supporting 均值，數學上恆為
0（無 supporting）或 >=0.50（有 supporting），永遠不可能落在 (0, 0.50)
之間；映射表在 >=0.40 又是 identity，導致「低信心」帶在真實
`aggregate()` 輸出下永遠不可達（只剩 abstain / 正常兩態）。第一版測試
用手造、confidence 與 supporting 內容互相矛盾（aggregate() 不可能產生）
的 `TrustedBrief` 掩蓋了這個缺陷。

本版修正：`aggregate()` 改用 `_evidence_strength()`（獨立來源數 / kind
多元度 / 佐證對反方優勢比例 / 裸信心的加權綜合，見 `trust.scoring` 模組
內對應區塊的設計說明）算出一個真正能跨越 [0, 1] 的指標，再套用分位數
映射表校準。本檔測試**只透過真實 `aggregate()`（或直接測試 `_evidence_
strength`/`_calibrate_confidence` 這兩個純函式本身）建構資料，不再用
「supporting 內容與 confidence 互相矛盾」的手造 brief**，並用端到端
（合成 Document → extract_claims → score → aggregate → build_report）
場景證明 abstain / 低信心 / 正常三態在真實 pipeline 輸出下皆可達。

codex 對抗審第 2 輪 [HIGH-1][HIGH-2] 修正（真一致性）：
  - [HIGH-1] 三態用 `calibrated_confidence` 判斷，但舊版 `Report` 只存裸
    `confidence`（supporting 均值，恆為 0 或 >=0.5）——弱證據 abstain 時，
    Markdown/Web 的信心欄仍讀裸值，可能顯示「中/高」，跟 market_judgment
    已寫的「資料不足、暫不判斷」矛盾。修法：`Report` 新增
    `calibrated_confidence`/`decision_state` 結構化欄位，顯示層
    （`confidence_label`/`to_markdown`/web gauge/comparison）全部改用
    校準值＋三態標籤；裸 `confidence` 保留供對照、不砍。
  - [HIGH-2] 舊版 abstain 態的 `inferences` 仍塞入 Step3 `narrative`
    （LLM 自由生成的行文）——prompt 指示雖已要求不得出現方向詞，但對真實
    LLM 呼叫只是軟性指示，非確定性保證。修法：abstain 態的 `inferences`
    完全不採用 LLM narrative，改用純確定性模板；`facts`（原始證據文字，
    可能含方向詞，如「BTC 上漲」）仍照常透明列出——這是「觀察訊號」，
    不是「方向結論」，允許出現；但 `market_judgment`／`inferences`／
    標題等「結論層」文字必須保證零方向詞。

CEO 派工規格：
  - `trust.scoring.aggregate()` 新增 `calibrated_confidence`（硬編分位數
    映射表，確定性、免 LLM）；`confidence` 裸值保留、不砍。
  - `agent.orchestrator.build_report()` 用校準後信心取代武斷單一 0.5
    硬門檻，改為三態：
      calibrated < 0.35 或 supporting < 2（證據不足）→ abstain：中性
      措辭，不給方向性字眼。
      0.35 <= calibrated < 0.5 → 仍出結論，標「低信心」。
      calibrated >= 0.5 → 正常（既有行為逐字不變）。
  - 0.5 錨點不刪，只從唯一硬門檻降為三態分界之一；`support_threshold=0.50`
    等既有呼叫端逐字不變。

誠實聲明（比照 `trust.scoring._calibrate_confidence` / `_evidence_strength`
docstring）：這整套是簡化版工程啟發式，不是嚴謹 conformal prediction，
沒有 coverage 保證。

codex 對抗審第 3 輪 [HIGH] 修正（cross_source_signal 方向洩漏）：
  - 第 2 輪的 e2e 全輸出測試用的 abstain 情境剛好沒觸發 `detect_cross_
    source_signal`（heavy contrarian 皆為低信任、被 `trust>=0.5` 篩選排除），
    漏測了「abstain 但仍有跨源共識/背離訊號」的情境——該訊號的 summary
    固定含「偏多/偏空」中文標籤（見 `detect_cross_source_signal` 內
    `_label` 對照），舊版無條件塞進 `Report.cross_source_signal`，會透過
    Markdown「跨源訊號」區塊／Web `_render_cross_signal` 洩漏方向結論，
    跟 abstain 立場矛盾。
  - 修法：`build_report()` 在 `is_abstain` 時，`Report.cross_source_signal`
    直接設 `None`（不下方向性跨源結論；normal/low_confidence 態不變）。
  - 本輪新增 `test_e2e_abstain_with_real_cross_source_signal_is_neutralized`：
    構造一個「有 2 筆高信任、跨 kind（price+news）、方向一致」的證據，讓
    `detect_cross_source_signal` 真的產出含「偏多」的 consensus 訊號，同時
    整體 calibrated_confidence 仍 < 0.35（重方反方雜訊拉低），驗證修後
    `report.cross_source_signal is None`，且完整 Markdown/Web HTML/
    analyze.json 三管道都無方向詞洩漏。
"""
from __future__ import annotations

import dataclasses
import html as _html
import json

from trustforge import web
from trustforge.agent.orchestrator import build_report, detect_cross_source_signal
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import (
    Claim,
    ScoredClaim,
    _calibrate_confidence,
    _evidence_strength,
    aggregate,
    extract_claims,
    score,
)

# 不得出現在 abstain 措辭裡的方向性字眼（守「不代客決策」鐵律）。
_DIRECTIONAL_WORDS = ("偏多", "偏空", "看漲", "看跌", "上漲", "下跌")


def _observed_texts(report) -> list[str]:
    """回傳一份報告裡所有「觀察訊號」原文（供 `_assert_directional_words_
    confined_to_facts` 挖掉）：`facts`（客觀事實）＋ `key_basis[].claim`
    （supporting 主張原文，含 `facts` 未涵蓋的 news/social 等情緒類 kind，
    見 `orchestrator.build_report` 的 `key_basis` 迴圈——逐一走訪
    `brief.supporting`，不限 `OBJECTIVE_KINDS`）＋ `contrarian`（反方低信任
    原文）。這三者都是「透明列出的來源原文」，不是本報告自己下的方向性
    結論——即使其中一句剛好含方向詞（如某則新聞寫「轉為看漲」），那是
    對來源內容的如實引用，不是 pipeline 自己判斷的方向，允許出現；真正
    要保證零方向詞的是 market_judgment／inferences／標題／cross_source
    summary 這些「pipeline 自己組出來的結論層文字」。"""
    return (
        list(report.facts)
        + [b.claim for b in report.key_basis]
        + list(report.contrarian)
    )


def _assert_directional_words_confined_to_facts(full_text: str, facts: list[str]) -> None:
    """codex 對抗審第 2 輪 [HIGH-2] 端到端驗證：把 `facts`（允許含方向詞的
    「觀察訊號」原文，呼叫端應傳入 `_observed_texts(report)` 取得完整範圍，
    非只 `report.facts`）從完整輸出文字裡挖掉後，剩餘文字（含
    market_judgment／inferences／標題／cross_source 等「結論層」內容）不得
    再出現任何方向詞——確保方向性結論不會透過任何管道（Markdown／Web
    HTML／analyze.json）洩漏。"""
    masked = full_text
    for f in facts:
        masked = masked.replace(f, "")
        masked = masked.replace(_html.escape(f), "")
    for w_ in _DIRECTIONAL_WORDS:
        assert w_ not in masked, (
            f"方向詞「{w_}」出現在 facts 觀察訊號以外的內容（結論層）：\n{masked[:3000]}"
        )


def _doc(id_: str, kind: str, source: str, text: str = "", ts: float = 1_000_000.0, meta: dict | None = None) -> Document:
    return Document(id=id_, kind=kind, source=source, text=text, ts=ts, meta=meta or {})


def _run_report(brief, qtype=QuestionType.MULTI_SOURCE, query="分析 BTC", now: float = 1_000_000.0):
    return build_report(
        query=query, coin="BTC", qtype=qtype, brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: now),
        now_fn=lambda: now,
    )


def _aggregate_from_docs(docs: list[Document], query: str = "分析 BTC", now: float = 1_000_000.0):
    return aggregate(score(extract_claims(docs), now=now), query=query)


# ---------------------------------------------------------------------------
# 1. `_calibrate_confidence` 純函式：固定校準表性質
# ---------------------------------------------------------------------------

def test_calibrate_confidence_0_3_lands_below_abstain_threshold():
    """輸入 0.3 → 校準後應落入 abstain 區間（< 0.35）。"""
    calibrated = _calibrate_confidence(0.3)
    assert calibrated < 0.35, f"預期 0.3 校準後 < 0.35（abstain），實得 {calibrated}"


def test_calibrate_confidence_0_4_lands_in_low_confidence_band():
    """輸入 0.4 → 校準後應落入低信心區間 [0.35, 0.5)。"""
    calibrated = _calibrate_confidence(0.4)
    assert 0.35 <= calibrated < 0.5, f"預期 0.4 校準後落在 [0.35, 0.5)（低信心），實得 {calibrated}"


def test_calibrate_confidence_0_6_lands_in_normal_band():
    """輸入 0.6 → 校準後應落入正常區間（>= 0.5）。"""
    calibrated = _calibrate_confidence(0.6)
    assert calibrated >= 0.5, f"預期 0.6 校準後 >= 0.5（正常），實得 {calibrated}"


def test_calibrate_confidence_fixed_table_exact_values():
    """固定校準表回歸鎖：釘住表上明確錨點的精確輸出，未來改表需明確更新此測試。"""
    assert _calibrate_confidence(0.0) == 0.0
    assert _calibrate_confidence(0.3) == 0.20
    assert _calibrate_confidence(0.4) == 0.40
    assert _calibrate_confidence(0.55) == 0.55
    assert _calibrate_confidence(1.0) == 1.0


def test_calibrate_confidence_monotonic_non_decreasing():
    """確定性、免 LLM：校準函式必須是單調不減（分位數映射的基本要求）。"""
    xs = [i / 100 for i in range(0, 101)]
    ys = [_calibrate_confidence(x) for x in xs]
    for a, b in zip(ys, ys[1:]):
        assert b >= a, "校準表插值不應出現非單調（違反分位數映射基本假設）"


def test_calibrate_confidence_clamps_out_of_range_input():
    """輸入超出 [0, 1]（防禦性，理論上不該發生）時 clamp 到邊界，不 crash。"""
    assert _calibrate_confidence(-1.0) == 0.0
    assert _calibrate_confidence(2.0) == 1.0


def test_calibrate_confidence_deterministic_same_input_same_output():
    """確定性：同輸入呼叫多次結果逐字相同（免 LLM、無隨機性）。"""
    results = {_calibrate_confidence(0.437) for _ in range(5)}
    assert len(results) == 1


# ---------------------------------------------------------------------------
# 2. `_evidence_strength` 純函式：綜合指標本身能跨越 [0, 1]（codex 修正核心）
# ---------------------------------------------------------------------------

def _fake_sc(source: str, kind: str, trust: float = 0.6) -> ScoredClaim:
    doc = Document(id=f"{source}-{kind}", kind=kind, source=source, text="", ts=1.0)
    claim = Claim(id=doc.id, text="x", doc=doc, direction="neutral")
    return ScoredClaim(claim=claim, trust=trust)


def test_evidence_strength_empty_supporting_is_zero():
    assert _evidence_strength([], [], 0.0) == 0.0


def test_evidence_strength_single_source_much_lower_than_many_sources():
    """1 個獨立來源 vs 6 個獨立來源佐證同一裸信心，強度應差很多
    （codex 要求：獨立來源數必須反映在指標上）。"""
    single = [_fake_sc("only-src", "price", 0.6)]
    many = [_fake_sc(f"src-{i}", "price", 0.6) for i in range(6)]
    strength_single = _evidence_strength(single, [], confidence=0.6)
    strength_many = _evidence_strength(many, [], confidence=0.6)
    assert strength_many > strength_single + 0.2, (
        f"多源佐證強度應明顯高於單源：single={strength_single} many={strength_many}"
    )


def test_evidence_strength_heavy_contrarian_dominance_lowers_strength():
    """佐證被大量反方證據夾擊時，強度應明顯下降。"""
    supporting = [_fake_sc("s1", "price", 0.6), _fake_sc("s2", "price", 0.6)]
    strength_no_contra = _evidence_strength(supporting, [], confidence=0.6)
    strength_heavy_contra = _evidence_strength(
        supporting, [_fake_sc(f"c{i}", "social", 0.2) for i in range(8)], confidence=0.6
    )
    assert strength_heavy_contra < strength_no_contra


def test_evidence_strength_spans_full_range_reaches_all_three_bands():
    """確定性驗證：`_evidence_strength` 加上 `_calibrate_confidence` 的組合，
    在合理輸入下能真的分別落入 abstain(<0.35) / 低信心([0.35,0.5)) /
    正常(>=0.5) 三個區間 —— 不是只能在兩態間跳。"""
    low = _calibrate_confidence(_evidence_strength(
        [_fake_sc("only-src", "price", 0.6)],
        [_fake_sc(f"c{i}", "social", 0.2) for i in range(6)],
        confidence=0.6,
    ))
    mid = _calibrate_confidence(_evidence_strength(
        [_fake_sc("s1", "price", 0.75), _fake_sc("s2", "price", 0.75)],
        [_fake_sc(f"c{i}", "social", 0.2) for i in range(3)],
        confidence=0.75,
    ))
    high = _calibrate_confidence(_evidence_strength(
        [_fake_sc(f"s{i}", k, 0.8) for i, k in enumerate(["price", "onchain", "regulatory", "news"])],
        [],
        confidence=0.8,
    ))
    assert low < 0.35, f"低強度案例應落 abstain，實得 {low}"
    assert 0.35 <= mid < 0.5, f"中強度案例應落低信心，實得 {mid}"
    assert high >= 0.5, f"高強度案例應落正常，實得 {high}"


# ---------------------------------------------------------------------------
# 3. `aggregate()` 附上 `calibrated_confidence`，`confidence` 裸值不砍
# ---------------------------------------------------------------------------

def test_aggregate_sets_calibrated_confidence_from_evidence_strength():
    """回歸鎖：`calibrated_confidence` 必須是 `_evidence_strength()` 經
    `_calibrate_confidence()` 算出來的（用同一份 supporting/contrarian/
    confidence 重算應逐字相同），不是另外一套邏輯各自漂移。"""
    docs = [
        _doc("a", "onchain", "glassnode", "大額 BTC 轉入交易所造成賣壓，價格下跌。"),
        _doc("b", "social", "x-anon", "BTC 翻倍 to the moon 穩賺！"),
    ]
    brief = _aggregate_from_docs(docs, query="BTC 賣壓")
    assert 0.0 <= brief.confidence <= 1.0
    assert 0.0 <= brief.calibrated_confidence <= 1.0
    # 測試資料量小（<=10 supporting、<=5 contrarian），brief.supporting/
    # contrarian 未被截斷，可直接拿來重算比對。
    expected = _calibrate_confidence(
        _evidence_strength(brief.supporting, brief.contrarian, brief.confidence)
    )
    assert brief.calibrated_confidence == expected


def test_aggregate_no_supporting_confidence_and_calibrated_both_zero():
    """既有行為：無 supporting 時 confidence=0.0；calibrated 亦應為 0.0。"""
    docs = [_doc("a", "social", "x-anon", "BTC 翻倍 to the moon 穩賺快上車！")]
    brief = _aggregate_from_docs(docs, query="無關查詢字串")
    assert not brief.supporting
    assert brief.confidence == 0.0
    assert brief.calibrated_confidence == 0.0


def test_aggregate_many_independent_diverse_sources_yields_high_calibrated_confidence():
    """多獨立來源 + 多元 kind + 無反方 → calibrated 應落入正常區間（>= 0.5）。"""
    docs = [
        _doc("p1", "price", "exch-a", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("p2", "onchain", "glassnode", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("p3", "regulatory", "sec-gov", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("p4", "news", "coindesk", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
    ]
    brief = _aggregate_from_docs(docs)
    assert brief.calibrated_confidence >= 0.5, brief.calibrated_confidence


def test_confidence_field_stays_raw_not_calibrated():
    """`confidence` 裸值語意不變：不等於 `calibrated_confidence`（除非剛好同值）。"""
    docs = [
        _doc("p1", "price", "exch-a", "BTC 盤整 持穩。"),
        _doc("p2", "price", "exch-b", "BTC 盤整 持穩。"),
    ] + [_doc(f"c{i}", "social", f"anon-{i}", "BTC 翻倍 to the moon 穩賺快上車！") for i in range(3)]
    brief = _aggregate_from_docs(docs)
    assert brief.confidence != brief.calibrated_confidence
    assert brief.confidence == 0.75


# ---------------------------------------------------------------------------
# 4. `build_report` 三態 abstain —— 全部只透過真實 aggregate() 建 brief
# ---------------------------------------------------------------------------

def test_e2e_single_weak_source_with_heavy_contrarian_abstains():
    """單一獨立來源 + 大量反方雜訊 → calibrated < 0.35（真實 aggregate 產出，
    非手造）→ abstain：中性措辭、無方向詞。

    p1 故意含方向詞「上漲」——驗證 facts（觀察訊號）可以透明保留原文方向詞，
    但 market_judgment 不得出現（[HIGH-2] 結論層／觀察層分離）。"""
    docs = [
        _doc("p1", "price", "exch-a", "BTC 早盤 短暫 上漲，隨後 拉回 整理，整體 呈 盤整。"),
        _doc("p2", "price", "exch-a", "BTC 盤整 持穩。"),
    ] + [_doc(f"c{i}", "social", f"anon-{i}", "BTC 翻倍 to the moon 穩賺快上車！") for i in range(6)]
    brief = _aggregate_from_docs(docs)
    assert brief.calibrated_confidence < 0.35, brief.calibrated_confidence
    assert len(brief.supporting) >= 2, "本案例故意驗證『calibrated 驅動』的 abstain，非筆數不足驅動"

    report, evidence = _run_report(brief)
    assert report.direction == "不明"
    assert "不足" in report.market_judgment
    # [HIGH-1] 結構化三態欄位：decision_state 必須為 "abstain"，
    # calibrated_confidence 必須與 brief 一致（非裸 confidence）。
    assert report.decision_state == "abstain"
    assert report.calibrated_confidence == brief.calibrated_confidence
    assert report.confidence_label() == "棄權／資料不足"
    # facts 允許含方向詞（觀察訊號，透明呈現）——本案例特意驗證這點。
    assert any("上漲" in f for f in report.facts), "本案例應驗證 facts 保留原始方向詞"
    for w in _DIRECTIONAL_WORDS:
        assert w not in report.market_judgment, f"abstain 措辭不應含方向詞「{w}」：{report.market_judgment}"
    # [HIGH-2] inferences（推論層）必須零方向詞，即使 facts 有、即使 Bedrock
    # narrative 理論上可能違反 prompt 指示——inferences 已改確定性模板，
    # 完全不採 LLM narrative，不依賴 LLM 是否守規矩。
    for inf in report.inferences:
        for w in _DIRECTIONAL_WORDS:
            assert w not in inf, f"abstain inferences 不應含方向詞「{w}」：{inf}"

    # 完整輸出（Markdown／Web HTML／analyze.json）皆須驗證：方向詞只能出現
    # 在 facts 原文裡，市場判斷/推論/標題等結論層絕對零方向詞。
    md = report.to_markdown(evidence)
    _assert_directional_words_confined_to_facts(md, _observed_texts(report))

    log = ExecutionLog(now_fn=lambda: 1_000_000.0)
    web_html = web._render_report(report, evidence, log)
    _assert_directional_words_confined_to_facts(web_html, _observed_texts(report))

    payload = json.dumps(dataclasses.asdict(report), ensure_ascii=False)
    _assert_directional_words_confined_to_facts(payload, _observed_texts(report))
    assert payload  # analyze.json 的 report 區塊可正常序列化，不拋錯


def test_e2e_abstain_with_real_cross_source_signal_is_neutralized():
    """codex 對抗審第 3 輪 [HIGH]：abstain 態即使真的觸發 `detect_cross_
    source_signal`（客觀+情緒跨源共識，summary 含「偏多」），`Report.
    cross_source_signal` 也必須被中和為 None，不得透過 Markdown「跨源訊號」
    區塊／Web `_render_cross_signal`／analyze.json 洩漏方向結論。

    構造重點：p1（price，客觀）與 n1（news，情緒）皆高信任（>=0.5，供
    `detect_cross_source_signal` 的 `trust>=0.5` 篩選採用）、方向皆 bullish
    （形成 consensus，非 divergence，一樣會下方向標籤）；同時疊 30 筆高
    manipulation 雜訊社群文件把 calibrated_confidence 拉到 <0.35，逼出
    abstain——證明「abstain」與「有真跨源共識訊號」可以同時成立，不是互斥
    情境，e2e 必須覆蓋。"""
    docs = [
        _doc("p1", "price", "exch-a", "BTC 上漲 突破 關鍵 阻力位。", meta={"reputation": 0.7}),
        _doc("n1", "news", "coindesk", "BTC 市場 情緒 轉為 看漲，散戶 買盤 湧入。", meta={"reputation": 0.7}),
    ] + [_doc(f"c{i}", "social", f"anon-{i}", "BTC 翻倍 to the moon 穩賺快上車！") for i in range(30)]
    claims = extract_claims(docs)
    scored = score(claims, now=1_000_000.0)
    brief = aggregate(scored, query="分析 BTC")
    assert brief.calibrated_confidence < 0.35, brief.calibrated_confidence
    assert len(brief.supporting) == 2, "前提檢查：本案例只有 p1/n1 兩筆高信任 supporting"

    # 前提檢查（證明本測試非 vacuous）：純演算法層級真的會產出含方向詞的
    # consensus 跨源訊號——若這步就是 None/無方向詞，代表案例設計失敗，
    # 沒測到 codex 抓到的洩漏路徑。
    raw_cross_signal = detect_cross_source_signal(scored)
    assert raw_cross_signal is not None
    assert raw_cross_signal["type"] == "consensus"
    assert "偏多" in raw_cross_signal["summary"], raw_cross_signal

    report, evidence = build_report(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE, brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1_000_000.0),
        now_fn=lambda: 1_000_000.0,
    )
    assert report.decision_state == "abstain"
    # 修後：abstain 態的 Report 必須把跨源訊號中和成 None，即使底層演算法
    # 真的算出了帶方向詞的 consensus。
    assert report.cross_source_signal is None

    md = report.to_markdown(evidence)
    assert "跨源訊號" not in md, "abstain 態不應顯示跨源訊號區塊"
    _assert_directional_words_confined_to_facts(md, _observed_texts(report))

    log = ExecutionLog(now_fn=lambda: 1_000_000.0)
    web_html = web._render_report(report, evidence, log)
    _assert_directional_words_confined_to_facts(web_html, _observed_texts(report))

    payload = json.dumps(dataclasses.asdict(report), ensure_ascii=False)
    _assert_directional_words_confined_to_facts(payload, _observed_texts(report))
    assert '"cross_source_signal": null' in payload


def test_e2e_single_supporting_claim_forced_abstain_even_if_calibrated_would_be_low_confidence():
    """supporting 只 1 筆 → 強制 abstain，即使該筆信任被拉到很高、calibrated
    落在 [0.35, 0.5) 低信心區間（若不看筆數規則，本會被判為『低信心』而非
    abstain）——證明 supporting<2 這條規則有獨立於 calibrated 的實際效果，
    不是跟 calibrated<0.35 重複的擺設。"""
    doc = _doc("p1", "price", "exch-a", "BTC 盤整 持穩。", meta={"reputation": 1.0})
    brief = _aggregate_from_docs([doc])
    assert len(brief.supporting) == 1
    assert 0.35 <= brief.calibrated_confidence < 0.5, (
        f"前提檢查失敗：本案例應驗證『筆數不足』獨立於『calibrated 過低』生效，"
        f"實得 calibrated={brief.calibrated_confidence}"
    )

    report, _evidence = _run_report(brief)
    assert report.direction == "不明"
    assert "不足" in report.market_judgment
    assert report.decision_state == "abstain"
    assert report.calibrated_confidence == brief.calibrated_confidence
    assert report.confidence_label() == "棄權／資料不足"
    for w in _DIRECTIONAL_WORDS:
        assert w not in report.market_judgment, f"abstain 措辭不應含方向詞「{w}」：{report.market_judgment}"
    for inf in report.inferences:
        for w in _DIRECTIONAL_WORDS:
            assert w not in inf, f"abstain inferences 不應含方向詞「{w}」：{inf}"


def test_e2e_moderate_evidence_low_confidence_state_still_gives_conclusion_but_marked():
    """2 個獨立來源、單一 kind、有一定反方雜訊 → calibrated 落 [0.35, 0.5)
    （真實 aggregate 產出）→ 仍出結論（有方向），但標「低信心」。"""
    docs = [
        _doc("p1", "price", "exch-a", "BTC 盤整 持穩。"),
        _doc("p2", "price", "exch-b", "BTC 盤整 持穩。"),
    ] + [_doc(f"c{i}", "social", f"anon-{i}", "BTC 翻倍 to the moon 穩賺快上車！") for i in range(3)]
    brief = _aggregate_from_docs(docs)
    assert 0.35 <= brief.calibrated_confidence < 0.5, brief.calibrated_confidence

    report, _evidence = _run_report(brief)
    assert report.direction != "不明"
    assert "低信心" in report.market_judgment
    assert "不足" not in report.market_judgment
    # [HIGH-1] 結構化三態欄位：decision_state 必須為 "low_confidence"。
    assert report.decision_state == "low_confidence"
    assert report.calibrated_confidence == brief.calibrated_confidence
    assert report.confidence_label() == "低信心"


def test_e2e_strong_multi_source_evidence_normal_state_unmarked():
    """多獨立來源、多元 kind、無反方 → calibrated >= 0.5（真實 aggregate 產出）
    → 正常，不含 abstain/低信心標記（既有行為逐字不變）。"""
    docs = [
        _doc("p1", "price", "exch-a", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("p2", "onchain", "glassnode", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("p3", "regulatory", "sec-gov", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("p4", "news", "coindesk", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
    ]
    brief = _aggregate_from_docs(docs)
    assert brief.calibrated_confidence >= 0.5, brief.calibrated_confidence

    report, _evidence = _run_report(brief)
    assert report.direction == "偏多"
    assert "低信心" not in report.market_judgment
    assert "不足" not in report.market_judgment
    # [HIGH-1] 結構化三態欄位：decision_state 必須為 "normal"，confidence_label
    # 用校準值分桶（本案例 calibrated 夠高，應落「高」或「中」，不因裸值分桶）。
    assert report.decision_state == "normal"
    assert report.calibrated_confidence == brief.calibrated_confidence
    assert report.confidence_label() in ("高", "中")


def test_e2e_report_confidence_field_is_raw_value_not_calibrated():
    """`Report.confidence` 沿用既有語意（裸值），回歸鎖：不得被 W4 悄悄換成
    校準值（用真實 aggregate() 產出的低信心案例驗證）。"""
    docs = [
        _doc("p1", "price", "exch-a", "BTC 盤整 持穩。"),
        _doc("p2", "price", "exch-b", "BTC 盤整 持穩。"),
    ] + [_doc(f"c{i}", "social", f"anon-{i}", "BTC 翻倍 to the moon 穩賺快上車！") for i in range(3)]
    brief = _aggregate_from_docs(docs)
    report, _evidence = _run_report(brief)
    assert report.confidence == brief.confidence
    assert report.confidence != brief.calibrated_confidence


def test_default_report_decision_state_and_calibrated_confidence_backward_compatible():
    """[HIGH-1] 向後相容回歸鎖：舊呼叫端手造 `Report(...)` 不傳
    `calibrated_confidence`/`decision_state` 時，預設值須維持「正常態」語意，
    不影響既有測試斷言（見 `tests/test_cross_source_signal.py` 的手造用法）。"""
    from trustforge.schema import Report

    r = Report(
        coin="BTC", question_type="multi_source", question="test",
        market_judgment="偏空", facts=[], inferences=[], key_basis=[],
        confidence=0.6, limits=[], could_flip=[], contrarian=[],
        generated_at="2026-07-01T00:00:00Z",
    )
    assert r.calibrated_confidence == 0.0
    assert r.decision_state == "normal"
    # 正常態沿用舊版純數字分桶邏輯（此時 calibrated_confidence 為預設 0.0，
    # 分桶結果會是「低」——這是刻意的：舊呼叫端若真的在意分桶結果，本就該
    # 改用 build_report() 走真實 pipeline 填入 calibrated_confidence，手造
    # Report 不應假裝有校準值）。
    assert r.confidence_label() == "低"
