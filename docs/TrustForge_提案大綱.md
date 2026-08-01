# TrustForge Hermes — 提案大綱

## 隊伍資訊
- 隊名：中再參與
- 命題類別：智慧交易（HOYA BIT）
- 命題主題：加密市場分析 AI Agent：多源資訊的信任提煉

## 團隊成員
| 成員 | 角色 |
|------|------|
| 王英豪 | 隊長 |
| 嵋婕 | 團隊成員 |
| 林子彤 | 團隊成員 |
| 王榆翔（Nicholas） | 團隊成員 |

## 一句話描述

TrustForge 不預測幣價漲跌，而是把多源資訊先做「信任提煉」——對每條消息評估可信度、交叉佐證、偵測操縱，產出帶溯源鏈的市場分析報告，讓使用者自己判斷。

## 解題方向

加密市場資訊真假難辨（喊單、假新聞、機器人洗版）。一般做法是「多源→LLM 摘要」，問題是來源不分等級、無法察覺造假、結論無法溯源。

TrustForge 的差異化是中間的 **Trust Layer（信任層）**：
1. 從多源資料中逐條抽取主張（Claim）
2. 對每條主張計算四維信任分數（來源信譽 × 交叉佐證 × 時效 − 操縱懲罰）
3. 信任加權後才進 LLM 行文
4. 每個結論帶完整溯源鏈，可追回原始來源

## AI 技術應用

- **AWS Bedrock（Claude Haiku 4.5）**：語意立場分類、敘事生成（唯一 LLM 入口）
- **確定性信任公式**：判斷由 pipeline 產生，非 AI 生成（反作弊合規）
- **Dawid-Skene EM**：動態來源信譽學習
- **Isotonic Regression**：信心校準模型
- **操縱偵測**：短時間大量發文、多帳號同步、煽動語意偵測

## 企業數據應用

- HOYA BIT 官方 5 幣種 5 年 Daily OHLCV 作為價格真值基準
- HOYA BIT 行情（ticker + depth）作為最高信任來源之一（信譽 0.85）

## AWS 雲端技術架構

- **Amazon Bedrock**：LLM 推理（唯一模型入口）
- **EC2**：後端服務 + Hermes 自主循環
- **App Runner**：備選部署路線
- **DynamoDB**：快取、成本分類帳、限流、冪等鎖
- **SSM Parameter Store**：機密管理
- **EventBridge**：Hermes 排程

## 生成式 AI 技術應用

- Bedrock 用於三個步驟：Claim 抽取、語意 Stance 分類、帶溯源敘事生成
- 反作弊設計：LLM 只負責「行文」，市場判斷由確定性公式產生
- 15 分鐘執行預算控管，失敗降級不崩

## Live Demo

- 部署網址：EC2 固定 EIP（公開位址已去識別）
- 功能：選幣種→選題型→輸入問題→即時產出報告+證據+執行紀錄
- 離線/線上雙模式：離線可展示完整流程，線上走真 Bedrock

## Kiro 使用

- `.kiro/steering/`：4 份專案規範（project / trust-layer / competition / pr-review-gate）
- `.kiro/hooks/`：5 個自動化品質控管（lint / test / direction-distribution / competition-constraints / core-file-guard）
- `.kiro/specs/`：30+ 份功能規格文件（含完成與進行中）

## 交付件

| # | 交付件 | 格式 |
|---|--------|------|
| 1 | 分析報告 | Markdown（結論→依據→信心→限制→反方） |
| 2 | 證據清單 | JSON（source / fetched_at / trust / content_reference / related_claim） |
| 3 | 執行紀錄 | JSONL（五節點時戳 + 工具呼叫 + 預算追蹤） |
| 4 | 程式碼與配置 | GitHub repo |

## GitHub

https://github.com/cancleeric/trustforge
