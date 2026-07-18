# 競賽約束規範（Always Included）

本文件確保 Kiro 在所有互動中都遵守競賽硬約束。

## 硬約束（違反 = 失格）

1. **僅限 AWS Bedrock 作為 LLM 入口** — 不走 OpenAI / Anthropic API / 集團內部閘道。所有 LLM 呼叫必須經過 `src/trustforge/bedrock.py`。
2. **反作弊鐵則** — 市場判斷、證據整合、信任評分由本 pipeline 產生。Bedrock 只負責「行文」，不得把第三方現成結論當主要結果。
3. **執行時間 ≤ 15 分鐘** — pipeline 必須有時間預算控管、平行抓取、可快取、失敗降級不崩。
4. **使用 HOYA BIT 提供之企業數據** — 5 幣種 5 年 Daily OHLCV（`data/` 目錄）。

## 交付物（4 件，缺一扣分）

| 交付物 | 格式 | 產出位置 |
|--------|------|----------|
| 分析報告 Final Report | MD | `out/<coin>/report.md` |
| 證據清單 Evidence List | JSON | `out/<coin>/evidence.json` |
| 執行紀錄 Execution Log | JSONL | `out/<coin>/execution_log.jsonl` |
| 程式碼與配置 | — | 本 repo |

## 評分權重

| 權重 | 項目 |
|------|------|
| 30% | 主題切合度（多源整合/證據回溯/矛盾處理/信心校準） |
| 25% | 技術可行性（可運行 Agent 架構 + AWS 架構合理） |
| 20% | 商業應用性（可讀/可採信/提升理解效率） |
| 15% | 創意度（原創方法/非表面摘要的洞察） |
| 10% | 完成度（輸入即跑完整流程） |
| +10% | **加分：採用 AWS Kiro** |

## Kiro 使用策略（爭取 +10%）

- 全程在 Kiro 中開發：需求分析 → 設計 → 實作 → 驗證
- 使用 Steering 管理專案規範與競賽約束
- 使用 Hooks 自動化品質控管（lint/test）
- 保留 Kiro session 歷史作為開發過程證據

## 設計決策原則

- 資訊完整度優先於結論果斷性（不硬給結論）
- 溯源鏈完整性：每個結論 → claim_id → 原始 Document → source URL/timestamp
- 反方證據主動呈現（不只給支撐方）
- 不提供投資建議（HOYA BIT 合規要求）
