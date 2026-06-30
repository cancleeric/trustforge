# AWS 架構（決賽簡報用）

> 競賽硬規則：**僅限 AWS 基礎模型**。本架構全程跑在 AWS，模型走 Bedrock。
> 加分：開發採用 **AWS Kiro**（AI 整合開發環境，+10%）。

## 架構圖（文字版）

```
                         ┌──────────────────────── AWS ────────────────────────┐
   使用者 / 評審          │                                                       │
   抽題(幣種+題目) ─────▶ │  API Gateway ──▶ Lambda(入口) ──▶ Step Functions      │
        │                │                                   (15 分鐘預算編排)    │
        │                │        ┌──────────────┬───────────┴──────────┐        │
        │                │        ▼              ▼                      ▼        │
        │                │   Ingestion      Trust Layer            Agent/Report   │
        │                │   (Lambda 平行)   (Lambda)              (Lambda)        │
        │                │     │  │  │           │                    │           │
        │                │     │  │  └─ S3: OHLCV CSV(官方基準)        │           │
        │                │     │  └──── 外部公開 API(news/onchain/social)         │
        │                │     │         金鑰 ← Secrets Manager        │           │
        │                │     └──── DynamoDB: 抓取快取/去重           ▼           │
        │                │                                   Amazon Bedrock        │
        │                │                                  (基礎模型, 推理行文)   │
        │                │                                        │               │
        │                │   CloudWatch Logs ◀── Execution Log     ▼               │
        │                │   S3: report.md / evidence.json / log ◀─┘               │
        │                │        │                                                │
   Live Demo Dashboard ◀─┴── S3 + CloudFront (靜態) / Amplify                       │
                         └───────────────────────────────────────────────────────┘
   開發期：AWS Kiro（AI IDE）  ←  +10% 加分
```

## 服務對應

| 層 | AWS 服務 | 用途 |
|----|----------|------|
| 模型 | **Amazon Bedrock** | 唯一基礎模型入口（推理 / 行文）。集中於 `bedrock.py` |
| 編排 | **Step Functions** | ingestion→trust→report 階段編排，控 15 分鐘預算、失敗降級 |
| 運算 | **Lambda**（或 Fargate）| 各階段執行；ingestion 平行抓多源 |
| 基準資料 | **S3** | 官方 OHLCV CSV；交付件 report/evidence/log 存放 |
| 快取 | **DynamoDB** | 抓取結果快取與來源去重（省時間、保獨立性）|
| 金鑰 | **Secrets Manager** | news/onchain/social API key（不入版控）|
| 入口 | **API Gateway** | 接收抽題、觸發分析 |
| 紀錄 | **CloudWatch Logs** | Execution Log 落地、可觀測 |
| Demo | **S3 + CloudFront / Amplify** | Live Demo Dashboard 部署網址 |
| 開發 | **AWS Kiro** | AI 整合開發環境（加分 +10%）|

## 15 分鐘執行預算對策

- ingestion 各來源**平行**抓取（Step Functions Map / Lambda 併發）。
- DynamoDB 快取避免重複抓取；逾時來源**降級略過**並在報告標記為限制，不卡死。
- `execlog.ExecutionLog` 全程記時戳；接近預算時停止新增來源、直接進報告生成。
- 一次正式機會 → 事前在同環境壓測整條鏈的尾延遲。

## 與 HOYA BIT 產品理念對齊

HOYA BIT「AI Native Exchange OS」定位＝**不代替投資決策、明確確認機制**。
TrustForge 輸出「可查證分析 + 信心限制 + 反方證據」，同樣**不代客決策**，
可作為其 AI 入口的「市場資訊信任層」延伸。
