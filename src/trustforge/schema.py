"""TrustForge 核心資料模型 — 對齊官方交付規格。

Evidence 欄位嚴格對應命題文件（source / fetched_at / content_reference / related_claim），
因主辦會抽查證據回溯性。Report 結構強制「事實 → 推論 → 結論」分層與三大必備章節。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

# 官方幣種池
COIN_POOL = ("BTC", "ETH", "SOL", "BNB", "XRP")


class QuestionType(str, Enum):
    MULTI_SOURCE = "multi_source"   # 多源整合
    HYPOTHESIS = "hypothesis"       # 假設驗證
    COMPARISON = "comparison"       # 比較分析


def iso_utc(ts: float) -> str:
    """epoch 秒 → ISO8601 UTC。ts<=0 或超出合理範圍視為未知，回空字串（不崩）。"""
    if not ts or ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return ""


@dataclass
class Evidence:
    """證據清單一筆。欄位對應官方規格，缺一不可回溯。"""
    source: str
    fetched_at: str          # ISO8601 UTC
    content_reference: str   # 引用片段 / 區間 / 數值 / 查詢條件
    related_claim: str
    source_url: str = ""
    kind: str = ""           # price/onchain/regulatory/hoyabit/news/social
    trust: float = 0.0
    trust_components: dict = field(default_factory=dict)
    # Tier2 可解釋 UX：操縱關鍵詞命中清單（由 trust.scoring._manipulation_flags
    # 回填，見 agent.orchestrator._scored_to_evidence）。預設空 list，向後相容——
    # 舊呼叫點（皆用 keyword 建構）不受影響，序列化/反序列化多一個可省略欄位。
    # 這是「確定判定為操縱」的紅旗🚩，會反映在 trust 分數（見 scoring.manip）。
    flags: list[str] = field(default_factory=list)
    # W3：informational-only 透明化 flag（由 trust.scoring._coordination_signals
    # 回填，見 agent.orchestrator._scored_to_evidence）。與 `flags` 不同，這裡的
    # 訊號（如多源文字高度相似）**不代表已判定操縱、也不影響 trust 分數**——
    # 純粹是「相似度高，供人工判讀」的中性提示，UI 不可用操縱紅旗樣式呈現
    # （見 web.py 的中性樣式）。CEO 定案：文字相似度單獨無法證明協同操縱，
    # 自動扣分必然誤傷合法聯播/引用，故拆成獨立欄位、獨立語意。預設空 list，
    # 向後相容。
    info_flags: list[str] = field(default_factory=list)
    # W3 前置（資料累積，非偵測）：來源平台公開 username 原文（由
    # ingestion 連接器寫入 Document.meta["author"]，見
    # agent.orchestrator._scored_to_evidence）。僅少數源（如 Reddit RSS
    # `<author>`、新聞 RSS `<author>`/`dc:creator`）有作者欄位；其餘源
    # 無此概念。不參與 trust 分數、不做任何跨平台關聯，也不在 UI 顯示。
    #
    # codex vp-engineering 終審 MEDIUM（PR #107）：型別改 `str | None`、
    # 預設 `None`（原本 `str = ""`）——空字串會把「來源沒有作者概念」跟
    # 「來源有作者概念、但這筆碰巧抓不到值」兩種完全不同的情況都抹平成
    # 同一個 falsy 值，違反本專案「缺鍵=未知」慣例（別的 optional 欄位都是
    # 用 `None`/缺鍵表達「未知」，不是用空字串冒充）。`None` 才是誠實的
    # 「未擷取到」，不代表「已確認無作者」。向後相容：舊快照/序列化資料
    # 反序列化回 Evidence 時缺這個欄位一樣正常（落到預設 `None`）。
    author: str | None = None
    # W2 可解釋性：動態信譽的運算模式標註（`"ds_em"` = 離線 Dawid-Skene EM
    # fallback；`"entailment"` = 線上有真語意互證）。這是**字串標註**，刻意
    # 放在 `trust_components` 的**同層兄弟欄位**、不塞進 `trust_components`
    # 本身——後者合約約定只放數值（API 消費者假設遍歷得到數值），放字串會
    # 破壞合約（codex 對抗審 Medium 修正）。`None` 表示無動態信譽 trace
    # （`dynamic_reputation=False` 或舊資料），向後相容。
    reputation_mode: str | None = None
    # 價格基準（特別是主辦五年 OHLCV）可重現性：檔名、SHA-256、列數、完整
    # 覆蓋期、分析窗口與 schema。None 代表非檔案型 Evidence，並非資料遺漏。
    data_lineage: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BasisItem:
    """關鍵依據一條：判斷 + 解釋 + 對應的證據索引。"""
    claim: str
    explanation: str
    evidence_idx: list[int] = field(default_factory=list)


@dataclass
class Report:
    coin: str
    question_type: str
    question: str
    market_judgment: str            # 1. 結論 / 市場判斷
    facts: list[str]                # 事實層（客觀、高信任）
    inferences: list[str]           # 推論層（Agent 推理）
    key_basis: list[BasisItem]      # 2. 關鍵依據（對應 Evidence）
    confidence: float               # 0–1
    limits: list[str]               # 3. 信心說明：已知限制 / 資料不足
    could_flip: list[str]           # 可能推翻結論的條件
    contrarian: list[str]           # 反方 / 低信任證據
    generated_at: str
    direction: str = ""             # 結構化方向欄位（偏多/偏空/中性），由 build_report 填入
    cross_source_signal: dict | None = field(default=None)  # 跨源訊號背離/共識，由 orchestrator 填入
    # Phase 1 獨特洞察層（#24/#15/#21/#72）：非顯而易見、可驗證的信任洞察清單
    # （聰明錢背離 / 操縱爆量 / 來源自我矛盾），由 `agent.orchestrator.build_report`
    # 呼叫 `trust.insights.detect_insights` 填入。每條洞察攜「兩個以上貢獻來源 +
    # 方向 + 強度 + 資料覆蓋閘」，覆蓋不足標 "insufficient"/「無法判定」，絕不
    # 硬湊（承接 Phase 0 三態誠實合約）。型別為 `trust.insights.Insight` 清單，
    # 此處刻意只用寬型別（不 import `Insight` dataclass）避免 schema↔insights
    # 循環依賴；序列化成 JSON 時 `dataclasses.asdict` 會遞迴展開嵌套 dataclass。
    insights: list | None = field(default=None)  # Phase 1 獨特洞察層，由 orchestrator 填入
    # Phase 1 D1.5 假設驗證題型結構化正反方：顯式 pro/con 證據帳本綁定 Evidence
    # List（pro = 支持方 evidence 索引；con = 反方 evidence 索引），並附信心限制
    # 聲明（不過度宣稱預測力，承接 Phase 0 誠實定位）。僅在 qtype==HYPOTHESIS 時
    # 由 `agent.orchestrator.build_report` 填入；其他題型為 None（選填、向前相容）。
    hypothesis_ledger: dict | None = field(default=None)  # D1.5 假設驗證正反方帳本，由 orchestrator 填入
    # 結構逐字沿用（不新增 dataclass 欄位，向後相容）。Tier2（真實分歧樣本）新增
    # 一個「選填」key：cross_source_signal["stance_pairs"]（list[dict]，
    # 每筆 {"source", "stance", "claim_id", "text"}）——只在
    # agent.orchestrator.detect_cross_source_signal 實際偵測到「同議題、跨源、
    # 語意矛盾」的主張配對時才會出現（見該函式 docstring）；無背離/無矛盾配對
    # 時完全不填，既有讀取 cross_source_signal 的程式碼（含本檔 to_markdown、
    # web.py render）不受影響。供未來 UI 渲染跨源矛盾對照用，本次不消費此欄位。
    #
    # issue #21（CISO-LOW）新增選填 key：cross_source_signal["sentiment_source_
    # count"]（int）——情緒類這一輪主導方向涉及的獨立來源數，純展示透明化
    # 欄位，不影響任何分數/方向計算，見 agent.orchestrator.
    # detect_cross_source_signal docstring。

    # W4 codex 對抗審第 2 輪 [HIGH-1] 修正：三態 abstain/低信心/正常判斷用的是
    # `trust.scoring.aggregate()` 算出的 `calibrated_confidence`（見
    # `agent.orchestrator._ABSTAIN_CALIBRATED_THRESHOLD` 一帶），但 Report 舊版
    # 只存裸 `confidence`（supporting 均值，恆為 0 或 >=0.5）——弱證據被判
    # abstain 時，Markdown/Web 的信心欄仍讀裸值，可能顯示「中/高」，跟
    # market_judgment 已經寫的「資料不足、暫不判斷」自相矛盾，下游（UI／
    # analyze.json 消費端）也沒有結構化欄位可辨三態。
    # 修法：新增 `calibrated_confidence`（校準值，`confidence` 裸值語意不變、
    # 不砍，供對照）與 `decision_state`（三態字面值 "abstain" | "low_confidence"
    # | "normal"），由 `agent.orchestrator.build_report` 依同一份判斷結果填入。
    # 預設 calibrated_confidence=0.0／decision_state="normal"：向後相容——舊測試
    # / 舊呼叫端手造 `Report(...)` 不傳這兩個欄位時維持「正常態」語意，不影響
    # 既有斷言（見 `tests/test_cross_source_signal.py` 手造 Report 的用法）。
    calibrated_confidence: float = 0.0
    decision_state: str = "normal"

    def confidence_label(self) -> str:
        """三態優先於純數字分桶：abstain/低信心用結構化狀態直接標示，避免
        校準值落在門檻附近時被舊版純數字 bucket 誤標成「中/高」，跟
        market_judgment 的棄權措辭矛盾（codex 對抗審第 2 輪 [HIGH-1]）。
        「正常」態改用 `calibrated_confidence`（校準值）分桶，不用裸 `confidence`
        ——顯示層統一改採校準後信心，裸值只留供對照（見 dataclass 欄位註解）。
        """
        if self.decision_state == "abstain":
            return "棄權／資料不足"
        if self.decision_state == "low_confidence":
            return "資訊完整度偏低"
        c = self.calibrated_confidence
        return "高" if c >= 0.7 else "中" if c >= 0.45 else "低"

    def _direction_label(self) -> str:
        """從 market_judgment 擷取方向詞（偏多/偏空/中性）。"""
        for kw in ("偏多", "偏空", "中性"):
            if kw in self.market_judgment:
                return kw
        return "不明"

    def to_markdown(self, evidence: list[Evidence]) -> str:
        L: list[str] = []
        L.append(f"# {self.coin} 市場分析報告")
        L.append(f"> 題型：{self.question_type}｜生成時間：{self.generated_at}")
        L.append(f"> 問題：{self.question}\n")

        L.append("## 1. 結論 / 市場判斷")
        L.append(self.market_judgment or "（待 Agent 生成）")
        # W4 [HIGH-1]：顯示改用校準值＋三態標籤（confidence_label() 已含三態），
        # 裸值並列供對照、不隱藏（見 dataclass 欄位註解，不砍舊欄位語意）。
        L.append(
            f"\n**資訊完整度：{self.confidence_label()}"
            f"（校準後 {self.calibrated_confidence:.2f}｜裸均值 {self.confidence:.2f}）**\n"
        )

        L.append("## 2. 關鍵依據（事實 → 推論 → 結論）")
        L.append("### 事實（客觀資料）")
        for f in self.facts:
            L.append(f"- {f}")
        L.append("\n### 推論（Agent 推理）")
        for inf in self.inferences:
            L.append(f"- {inf}")
        L.append("\n### 依據對應證據")
        for b in self.key_basis:
            tags = "".join(f"[E{i}]" for i in b.evidence_idx)
            L.append(f"- **{b.claim}** {tags}\n  - {b.explanation}")

        L.append("\n## 3. 資訊完整度說明")
        L.append(
            f"資訊完整度：**{self.confidence_label()}**"
            f"（校準後 {self.calibrated_confidence:.2f}｜裸均值 {self.confidence:.2f}）"
        )
        if self.limits:
            L.append("\n已知限制 / 資料不足：")
            for x in self.limits:
                L.append(f"- {x}")
        if self.could_flip:
            L.append("\n可能推翻結論的條件：")
            for x in self.could_flip:
                L.append(f"- {x}")

        if self.cross_source_signal:
            sig = self.cross_source_signal
            type_label = "背離" if sig.get("type") == "divergence" else "共識"
            L.append(f"\n## 跨源訊號（{type_label}）")
            L.append(sig.get("summary", ""))
            ids = sig.get("supporting_claim_ids", [])
            if ids:
                L.append(f"佐證 claim_ids：{', '.join(ids)}")

        if self.insights:
            L.append("\n## 獨特洞察層（非顯而易見、可驗證）")
            for ins in self.insights:
                cov = "（覆蓋不足：無法判定）" if ins.coverage == "insufficient" else ""
                L.append(f"### {ins.title}{cov}")
                L.append(ins.summary)
                for c in ins.contributions:
                    L.append(f"- 貢獻來源：{c.source}（{c.kind}）"
                             f"｜方向：{c.direction}｜信任：{c.trust}")
                    L.append(f"  - {c.text}")

        if self.hypothesis_ledger:
            hl = self.hypothesis_ledger
            L.append("\n## 假設驗證：正反方證據對照")
            L.append(f"- 支持方（pro）證據索引：{', '.join('E'+str(i) for i in hl.get('pro', [])) or '（無）'}")
            L.append(f"- 反方（con）證據索引：{', '.join('E'+str(i) for i in hl.get('con', [])) or '（無）'}")
            L.append(f"- 侷限說明：{hl.get('confidence_limit', '')}")

        if self.contrarian:
            L.append("\n## 反方 / 低信任證據（已標記，未納入主結論）")
            for x in self.contrarian:
                L.append(f"- {x}")

        L.append("\n## 證據清單對照")
        L.append("| # | source | fetched_at | trust | content_reference |")
        L.append("|---|--------|-----------|-------|-------------------|")
        for i, e in enumerate(evidence):
            ref = e.content_reference.replace("|", "\\|")[:80]
            L.append(f"| E{i} | {e.source} | {e.fetched_at} | {e.trust:.2f} | {ref} |")

        lineage_items = [e.data_lineage for e in evidence if e.data_lineage]
        if lineage_items:
            # The same official CSV generates several price facts; render each
            # dataset/window combination once so the report stays readable and
            # an auditor can still reproduce every numerical claim.
            seen: set[tuple[str, str]] = set()
            L.append("\n## 資料血緣 / 可重現性")
            for lineage in lineage_items:
                key = (str(lineage.get("sha256", "")), str(lineage.get("analysis_window", "")))
                if key in seen:
                    continue
                seen.add(key)
                coverage = lineage.get("coverage", {})
                L.append(
                    f"- {lineage.get('dataset_name', '')}｜{lineage.get('file', '')}｜"
                    f"完整期間 {coverage.get('start_date', '')}~{coverage.get('end_date', '')}｜"
                    f"本次窗口 {lineage.get('analysis_window', '')}｜"
                    f"{lineage.get('rows', '')} 日｜SHA-256 `{lineage.get('sha256', '')}`"
                )
        return "\n".join(L)


# ---------------------------------------------------------------------------
# 比較分析報告 Markdown（P1-1）
# ---------------------------------------------------------------------------

def _md_cell(s: str, maxlen: int = 0) -> str:
    """Markdown 表格欄位安全轉換：替換管道字元與換行，避免破壞表格結構。"""
    out = s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    return out[:maxlen] if maxlen else out


def comparison_to_markdown(
    report_a: Report,
    evidence_a: list[Evidence],
    report_b: Report,
    evidence_b: list[Evidence],
    query: str,
) -> str:
    """並列比較兩幣種的 Markdown 報告（P1-1 comparison 題型）。

    章節：
      1. 相對強弱比較（方向 / 信心 / 獨立來源 / 反方訊號）
      2. 流動性 / 波動指標（price facts 摘要）
      3. 各類訊號一致程度（來源 kind 分佈）
      4-5. 各幣詳細分析（呼叫既有 to_markdown）
      6. 合併證據清單（每列標明幣種，可被評審抽查）
    """
    L: list[str] = []
    L.append(f"# {report_a.coin} vs {report_b.coin} 比較分析報告")
    L.append(f"> 題型：comparison｜生成時間：{report_a.generated_at}")
    L.append(f"> 問題：{query}\n")

    # ── 1. 相對強弱 ──────────────────────────────────────────────
    L.append("## 1. 相對強弱比較")
    L.append(f"| 項目 | {report_a.coin} | {report_b.coin} |")
    L.append("|------|------|------|")
    dir_a = report_a.direction or report_a._direction_label()
    dir_b = report_b.direction or report_b._direction_label()
    L.append(f"| 市場方向 | {dir_a} | {dir_b} |")
    # W4 [HIGH-1]：改用校準值＋三態標籤（confidence_label() 已含三態）。
    L.append(
        f"| 資訊完整度 | {report_a.confidence_label()}（{report_a.calibrated_confidence:.2f}）"
        f" | {report_b.confidence_label()}（{report_b.calibrated_confidence:.2f}）|"
    )
    src_a = len({e.source for e in evidence_a})
    src_b = len({e.source for e in evidence_b})
    L.append(f"| 獨立來源數 | {src_a} | {src_b} |")
    L.append(f"| 反方訊號數 | {len(report_a.contrarian)} | {len(report_b.contrarian)} |")
    L.append("")

    # ── 2. 流動性 / 波動 ─────────────────────────────────────────
    L.append("## 2. 流動性 / 波動指標（price facts）")
    for rpt, evs in ((report_a, evidence_a), (report_b, evidence_b)):
        price_evs = [e for e in evs if e.kind == "price"]
        L.append(f"**{rpt.coin}**（{len(price_evs)} 條價格證據）")
        for e in price_evs[:3]:
            L.append(f"- {e.content_reference[:100]}")
        if not price_evs:
            L.append("- （無價格證據）")
    L.append("")

    # ── 3. 各類訊號一致程度 ───────────────────────────────────────
    L.append("## 3. 各類訊號一致程度")
    for rpt, evs in ((report_a, evidence_a), (report_b, evidence_b)):
        kind_count: dict[str, int] = {}
        for e in evs:
            kind_count[e.kind] = kind_count.get(e.kind, 0) + 1
        kind_str = "、".join(f"{k}:{v}筆" for k, v in sorted(kind_count.items()))
        rpt_dir = rpt.direction or rpt._direction_label()
        L.append(
            f"**{rpt.coin}**：{kind_str or '（無）'}"
            f"｜方向：{rpt_dir}"
            f"｜資訊完整度：{rpt.calibrated_confidence:.2f}（{rpt.confidence_label()}）"
        )
    L.append("")

    # ── 4-5. 各幣詳細分析 ────────────────────────────────────────
    L.append(f"## 4. {report_a.coin} 詳細分析")
    L.append(report_a.to_markdown(evidence_a))

    L.append(f"\n## 5. {report_b.coin} 詳細分析")
    L.append(report_b.to_markdown(evidence_b))

    # ── 6. 合併證據清單（標明幣種）─────────────────────────────
    L.append("\n## 6. 合併證據清單（標明幣種，可被評審抽查）")
    L.append("| # | 幣種 | source | fetched_at | trust | content_reference |")
    L.append("|---|------|--------|-----------|-------|-------------------|")
    for i, e in enumerate(evidence_a):
        ref = _md_cell(e.content_reference, 60)
        L.append(
            f"| E{i} | {_md_cell(report_a.coin)} | {_md_cell(e.source)}"
            f" | {_md_cell(e.fetched_at)} | {e.trust:.2f} | {ref} |"
        )
    offset = len(evidence_a)
    for i, e in enumerate(evidence_b):
        ref = _md_cell(e.content_reference, 60)
        L.append(
            f"| E{offset + i} | {_md_cell(report_b.coin)} | {_md_cell(e.source)}"
            f" | {_md_cell(e.fetched_at)} | {e.trust:.2f} | {ref} |"
        )

    return "\n".join(L)
