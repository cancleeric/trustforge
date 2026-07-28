# Multi-angle Synthesis 資料契約與確定性演算法

> Issue: #808
> 依據: docs/reports/PLAN-MULTI-ANGLE-IMPLEMENTATION-2026-07-28.md (PR1)

## 需求

將 TrustForge 的五個分析視角（risk / sentiment / fundamentals / news / catalyst）正規化為統一契約，並提供確定性交叉比對演算法，產出五角度綜合報告。

## 功能需求

### FR-1: AngleResult 正規化結構
- 從既有 Report + Evidence 投影出單角度結果
- 包含：angle, qtype, direction, calibrated_confidence, decision_state, key_basis_count, evidence_refs
- 保留完整 Report 和 Evidence 引用（drilldown 用）

### FR-2: AngleConflict 衝突描述
- 記錄哪兩個角度衝突
- 衝突類型：direction_divergence / confidence_gap / evidence_overlap
- 附帶 summary 文字（確定性模板，非 LLM）
- 附帶相關 evidence 引用

### FR-3: MultiAngleReport 綜合報告
- 包含所有 AngleResult
- 包含所有偵測到的 AngleConflict
- consensus 字串（偏多 / 偏空 / 中性 / 分歧 / partial_abstain / full_abstain）
- agreement_matrix：角度兩兩關係（agree / disagree / one_abstain）
- synthesis_summary：確定性模板組裝的摘要文字
- evidence_independence：獨立來源比例
- limits：限制聲明清單

### FR-4: synthesize_angles() 確定性演算法
- 方向背離偵測：兩兩比對 direction（偏多 vs 偏空 = divergence）
- 信心差距偵測：calibrated_confidence 差 > 0.3 標為 confidence_gap
- 證據獨立性：所有角度的 evidence source 聯集 vs 交集 Jaccard
- abstain 保護：任一角度 abstain → 綜合不得為 normal（退為 partial_abstain）
- 全角度 abstain → full_abstain
- 共識推導：non-abstain 角度的 direction 多數決 + calibrated_confidence 加權
- 100% 確定性，零 LLM 呼叫

### FR-5: angle_result_from_payload() 反序列化
- 從 analysis_results.payload_json 還原為 AngleResult
- 處理 payload 缺欄位的容錯（舊資料相容）

## 非功能需求

### NFR-1: 零第三方依賴
- 只用 stdlib + 現有 trustforge 模組
- 不引入新 package

### NFR-2: 效能
- synthesize_angles 對 5 個角度的計算應 < 10ms（純記憶體比對）

### NFR-3: 可解釋性
- 每個 conflict 可追溯到具體角度 + 具體數值
- consensus 推導邏輯可被人工重現

## 約束

- 不修改既有 Report / Evidence / TrustedBrief 任何欄位
- 不修改 scoring.py / orchestrator.py / analysis_flow.py
- synthesis 結論由公式產出，LLM 不參與決策
