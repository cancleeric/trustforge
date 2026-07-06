# TrustForge 合規性對照（vs 官方命題文件）

> 日期：2026-07-01｜依據 `docs/competition/COMPETITION-OFFICIAL.md`（官方權威原文）逐條核對。CEO 查 code 驗證，非假設。
> 結論：**開發方向正確、4 交付件與 5 能力全數合規**；2 個 flag（AWS 模型約束衝突 / Kiro +10% 未取）。

## A. 5 能力
| 官方能力 | 我方現況 | 狀態 |
|---|---|---|
| 多源資料整合 | price/onchain/news/social(Reddit)/regulatory(SEC)/hoyabit 6 類連接器（統一介面+優雅降級） | ✅ |
| 有層次推理（事實→推論→結論） | Report `market_judgment` + `key_basis(BasisItem 事實→推論→結論)` + 分區呈現 | ✅ |
| 不確定性與限制說明 | `confidence` + `confidence_label` + `report.limits`（收集失敗來源/資料不足） | ✅ |
| 可回溯證據管理 | Evidence 欄位 `source/fetched_at/content_reference/related_claim`(+`source_url`) 完全對應 | ✅ |
| 具洞察分析角度 | 反方證據分流、`cross_source_signal`（跨源背離/共識）、操縱旗標；**W1.5 語意矛盾偵測深化中** | ✅（深化中） |

## B. 題型 / 幣種 / 交付件
| 項目 | 官方 | 我方 | 狀態 |
|---|---|---|---|
| 題型 | 多源整合/假設驗證/比較分析 | `MULTI_SOURCE / HYPOTHESIS / COMPARISON` 皆可跑 | ✅ |
| 幣種池 | BTC/ETH/SOL/BNB/XRP | 同（coin-filter 防跨幣污染） | ✅ |
| Final Report | 結論/關鍵依據/信心說明 | 三者齊備 | ✅ |
| Evidence List | 4 必含欄位 + 可回溯 | 完全對應 | ✅ |
| Execution Log | 時戳+工具呼叫+資料取得+流程 | `execution_log.jsonl`（JSONL，含時戳/工具/預算追蹤） | ✅ |
| Source/Config | Agent 程式碼+設定+說明 | GitHub repo + README | ✅ |
| 基準 OHLCV 近五年 CSV | date,open,high,low,close,volume UTC | 已整合官方 OHLCV（rep 0.95 納入評分） | ✅（賽前確認資料包實際年限/單位） |

## C. 執行限制 / 反作弊
| 官方 | 我方 | 狀態 |
|---|---|---|
| ≤15 分鐘 | 壓測真實 25-68s（13× 餘裕）；**W1.5 stance 加預算上限/timeout 防超時（進行中）** | ✅（W1.5 補強） |
| 1 次正式執行 | execlog 預算追蹤；架構單次可完成 | ✅ |
| 反作弊：判斷/推理/報告須自有 Agent，不得把第三方現成結論當主結果 | Trust Layer 全自有確定性公式；Bedrock 僅行文 +（W1.5）逐對 stance 原子標籤餵我方公式（判斷結構仍我方） | ✅ |
| 來源多樣/獨立性/不過度依賴分析師文/可回溯 | 6 類來源、同源排除、佐證交叉驗證、Evidence 全回溯 | ✅ |

## D. 評分標準對位
- 15% 創意：「情報的情報」定位 + 逐主張可解釋信任 + 跨源背離 + W1.5 語意矛盾（大廠皆無）→ 強。
- 25% 技術：可運行 Agent 架構（3 步 pipeline）+ AWS 部署 + 穩定執行 → 強。
- 20% 商業：可讀信任儀表板 + 可採信（可解釋非黑箱）→ 中強。
- 30% 主題：多源/回溯/矛盾處理/信心校準/限制 全具備；核心自有 Agent（非依賴第三方結論）→ 強。
- 10% 完成度：端到端可跑、4 交付件齊 → 強。
- **+10% Kiro：未取（見 flag 2）。**

## Flags（需決策）
1. **AWS 模型約束衝突**：命題文件說模型可自備 API key、AWS 非強制；錄取信說「僅限 AWS 基礎模型」。**我方 Bedrock 兩種解讀皆合規**，方向不變；7/13 向 Mars Li 確認定案。
2. **AWS Kiro +10% 未取**：目前開發環境非 Kiro。+10% 是巨大權重，值得評估是否採 Kiro 作 AI IDE（需老闆決策；屬我方開發環境選擇，不對外揭露）。

## 總結
**方向正確、產出合規**。世界第一的推進點仍是 Tier 1 護城河深化（W1.5 語意佐證進行中 → W2 動態信譽 → W3 bridging → W4 校準）+ Tier 2 可解釋 UX。在合規前提下續推。
