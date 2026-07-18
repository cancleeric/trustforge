# TrustForge 專案規範

## 專案概述

TrustForge Hermes 是加密市場分析 AI Agent，核心差異化在於**信任提煉層（Trust Layer）**——對多源資訊逐條評估可信度、做交叉佐證、保留溯源軌跡，輸出信任加權的市場分析。

- 團隊：HurricaneSoft（颶風軟體），4 人黑客組
- 競賽：2026 雲湧智生 台灣生成式 AI 應用黑客松
- 命題：【智慧金融：HOYA BIT】加密市場分析 AI Agent：多源資訊的信任提煉

## 三層架構

```
Layer 1 — Ingestion（多源輸入）
  src/trustforge/ingestion/   連接器：prices/news/social/onchain/hoyabit/regulatory/coingecko

Layer 2 — Trust（信任提煉 ★ 核心）
  src/trustforge/trust/       TrustScore = 信譽×0.5 + 佐證×0.25 + 時效×0.15 − 操縱×0.40

Layer 3 — Agent（編排 + 溯源生成）
  src/trustforge/agent/       4 步推理：Claim 抽取 → 信任評分 → 帶溯源行文 → 限制複審
```

## 技術棧

- 後端：Python 3.11+，純 stdlib HTTP server，零第三方 runtime 依賴（僅 boto3）
- 前端：React + Vite + TypeScript + Tailwind CSS
- LLM：AWS Bedrock（唯一模型入口，集中在 bedrock.py）
- 部署：App Runner / Lambda Function URL / Docker / EC2
- 儲存：SQLite（本機快取）、DynamoDB（生產快取/預算/設定）

## 開發慣例

- 所有 Python 程式碼置於 `src/trustforge/`
- 測試放 `tests/`，覆蓋率要求 ≥75%（CI 閘門）
- commit message 使用繁體中文或英文，格式：`feat/fix/refactor: 簡述`
- 不引入額外第三方依賴（純 stdlib + boto3 原則）
- 環境變數控制行為切換，不寫死

## 幣種池

BTC / ETH / SOL / BNB / XRP

## 關鍵檔案索引

- #[[file:README.md]] — 專案總覽
- #[[file:docs/architecture/ARCHITECTURE.md]] — 架構設計
- #[[file:docs/competition/COMPETITION.md]] — 競賽規格
- #[[file:src/trustforge/trust/scoring.py]] — 信任評分引擎
- #[[file:src/trustforge/agent/orchestrator.py]] — Agent 編排
- #[[file:src/trustforge/pipeline.py]] — 共用管線入口
- #[[file:src/trustforge/bedrock.py]] — Bedrock 封裝
- #[[file:src/trustforge/budget_guard.py]] — 預算護欄
