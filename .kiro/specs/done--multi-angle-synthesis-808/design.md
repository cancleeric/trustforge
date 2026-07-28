# Multi-angle Synthesis 設計文件

## 架構概覽

```
analysis_results (DB, 已有)
   │
   │  五角度各自完成後的 payload_json
   ▼
angle_result_from_payload(mode, payload_json)
   │
   │  → AngleResult × 5
   ▼
synthesize_angles(angles)
   │
   │  確定性比對演算法
   ▼
MultiAngleReport
   │
   │  存入 DB / 回傳 API
   ▼
前端 MultiAngleOverview
```

## 模組位置

`src/trustforge/multi_angle.py` — 新檔案，獨立模組

## 資料結構設計

### AngleResult

```python
@dataclass
class AngleResult:
    angle: str                          # "risk" | "sentiment" | "fundamentals" | "news" | "catalyst"
    qtype: QuestionType                 # MULTI_SOURCE | HYPOTHESIS
    direction: str                      # "偏多" | "偏空" | "中性" | "不明"
    calibrated_confidence: float        # 0.0 ~ 1.0
    decision_state: str                 # "normal" | "low_confidence" | "abstain"
    key_basis: list[str]                # 關鍵依據摘要（前 3 條）
    evidence_sources: set[str]          # 去重的 evidence source 集合
    evidence_count: int                 # evidence 總數
    market_judgment: str                # 完整 market_judgment 原文
    snapshot_id: str                    # 追溯用
    job_id: str | None                  # 追溯用
```

### AngleConflict

```python
@dataclass
class AngleConflict:
    angle_a: str
    angle_b: str
    conflict_type: str                  # "direction_divergence" | "confidence_gap" | "evidence_overlap"
    detail: dict                        # 數值細節，可追溯
    summary: str                        # 確定性模板文字
```

### MultiAngleReport

```python
@dataclass
class MultiAngleReport:
    coin: str
    snapshot_id: str
    angles: list[AngleResult]
    consensus: str                      # "偏多" | "偏空" | "中性" | "分歧" | "partial_abstain" | "full_abstain"
    consensus_confidence: float         # 加權信心
    conflicts: list[AngleConflict]
    agreement_matrix: dict[str, dict[str, str]]  # {angle_a: {angle_b: "agree"|"disagree"|"one_abstain"}}
    synthesis_summary: str              # 確定性模板組裝
    evidence_independence: float        # 獨立來源 / 總來源
    limits: list[str]
    generated_at: str                   # ISO8601
```

## 演算法設計

### synthesize_angles()

```python
def synthesize_angles(angles: list[AngleResult], coin: str, snapshot_id: str) -> MultiAngleReport:
    # Phase 1: 分類
    active = [a for a in angles if a.decision_state != "abstain"]
    abstained = [a for a in angles if a.decision_state == "abstain"]
    
    # Phase 2: 全角度 abstain 快速路徑
    if not active:
        return _full_abstain_report(angles, coin, snapshot_id)
    
    # Phase 3: 方向統計
    direction_counts = Counter(a.direction for a in active if a.direction in ("偏多", "偏空", "中性"))
    
    # Phase 4: 衝突偵測
    conflicts = []
    for a1, a2 in combinations(active, 2):
        # 4a. 方向背離
        if _is_opposing(a1.direction, a2.direction):
            conflicts.append(AngleConflict(
                a1.angle, a2.angle, "direction_divergence",
                {"a_direction": a1.direction, "b_direction": a2.direction},
                f"{MODE_LABELS[a1.angle]}{a1.direction}，{MODE_LABELS[a2.angle]}{a2.direction}。"
            ))
        # 4b. 信心差距
        gap = abs(a1.calibrated_confidence - a2.calibrated_confidence)
        if gap > 0.3 and a1.decision_state == "normal" and a2.decision_state == "normal":
            conflicts.append(AngleConflict(
                a1.angle, a2.angle, "confidence_gap",
                {"gap": round(gap, 3), "a_conf": a1.calibrated_confidence, "b_conf": a2.calibrated_confidence},
                f"{MODE_LABELS[a1.angle]}信心 {a1.calibrated_confidence:.2f}，{MODE_LABELS[a2.angle]}信心 {a2.calibrated_confidence:.2f}，差距 {gap:.2f}。"
            ))
    
    # Phase 5: 證據獨立性
    all_sources = set()
    for a in angles:
        all_sources |= a.evidence_sources
    per_angle_sources = [a.evidence_sources for a in angles]
    shared = set.intersection(*per_angle_sources) if per_angle_sources else set()
    independence = 1.0 - (len(shared) / len(all_sources)) if all_sources else 0.0
    if independence < 0.3:
        # 高度重疊警示（但不作為 conflict，只加 limit）
        pass
    
    # Phase 6: 共識推導
    consensus = _derive_consensus(active, abstained, conflicts, direction_counts)
    
    # Phase 7: agreement_matrix
    matrix = _build_agreement_matrix(angles)
    
    # Phase 8: 組裝
    return MultiAngleReport(...)
```

### _is_opposing()

```python
_OPPOSING = {("偏多", "偏空"), ("偏空", "偏多")}

def _is_opposing(d1: str, d2: str) -> bool:
    return (d1, d2) in _OPPOSING
```

### _derive_consensus()

```python
def _derive_consensus(active, abstained, conflicts, direction_counts) -> tuple[str, float]:
    # 有 abstain → partial_abstain
    if abstained:
        base_state = "partial_abstain"
    else:
        base_state = "normal"
    
    # 有方向背離 → "分歧"
    has_divergence = any(c.conflict_type == "direction_divergence" for c in conflicts)
    if has_divergence:
        return "分歧", _weighted_confidence(active)
    
    # 多數決
    if direction_counts.get("偏多", 0) > direction_counts.get("偏空", 0):
        direction = "偏多"
    elif direction_counts.get("偏空", 0) > direction_counts.get("偏多", 0):
        direction = "偏空"
    else:
        direction = "中性"
    
    # partial_abstain 時不升級為純方向結論
    if base_state == "partial_abstain":
        return f"partial_abstain", _weighted_confidence(active)
    
    return direction, _weighted_confidence(active)
```

### _weighted_confidence()

```python
def _weighted_confidence(active: list[AngleResult]) -> float:
    if not active:
        return 0.0
    total = sum(a.calibrated_confidence for a in active)
    return round(total / len(active), 4)
```

## 反序列化設計

### angle_result_from_payload()

```python
def angle_result_from_payload(mode: str, payload_json: str) -> AngleResult:
    """從 analysis_results.payload_json 反序列化。容錯處理舊格式。"""
    data = json.loads(payload_json)
    report = data.get("report", {})
    evidence = data.get("evidence", [])
    
    return AngleResult(
        angle=mode,
        qtype=QuestionType(report.get("question_type", "multi_source")),
        direction=report.get("direction", "不明"),
        calibrated_confidence=float(report.get("calibrated_confidence", 0.0)),
        decision_state=report.get("decision_state", "normal"),
        key_basis=[b.get("claim", "") for b in report.get("key_basis", [])[:3]],
        evidence_sources={e.get("source", "") for e in evidence if e.get("source")},
        evidence_count=len(evidence),
        market_judgment=report.get("market_judgment", ""),
        snapshot_id=data.get("snapshot_id", ""),
        job_id=None,  # 由呼叫端填入
    )
```

## 測試策略

| 案例 | 輸入 | 預期 |
|------|------|------|
| 全 normal 同方向 | 5 角度偏多 | consensus="偏多", conflicts=[] |
| 方向背離 | risk 偏空 + sentiment 偏多 | consensus="分歧", 1 conflict |
| 單角度 abstain | fundamentals abstain | consensus="partial_abstain" |
| 全角度 abstain | 5 角度 abstain | consensus="full_abstain" |
| 證據高度重疊 | 5 角度同 source | evidence_independence < 0.3, limit 警示 |
| 信心差距 | risk 0.8 + news 0.3 | 1 confidence_gap conflict |
| 混合情境 | 3 偏多 1 偏空 1 abstain | consensus="partial_abstain", 1 divergence |

## 不做什麼

- 不修改 Report / Evidence / TrustedBrief
- 不修改 scoring.py / orchestrator.py / analysis_flow.py
- 不呼叫 LLM
- 不新增前端元件（#810）
- 不新增 API endpoint（#809）
