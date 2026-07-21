# TrustForge × AgentCore × Bedrock 架構關係

> 版本：v0.16.17+

## 架構層級

```
┌─────────────────────────────────────────────────┐
│  TrustForge Hermes（我們的應用）                   │
├─────────────────────────────────────────────────┤
│  bedrock.py — LLM 呼叫入口（唯一）                 │
│    ├─ 直連 boto3 → Bedrock（預設）                │
│    ├─ AgentCore bridge (strands) → Bedrock       │
│    └─ offline → stub placeholder                 │
├─────────────────────────────────────────────────┤
│  AgentCore（Agent 管理框架）                       │
│    ├─ Memory: SEMANTIC + SUMMARIZATION           │
│    ├─ Online Eval: GoalSuccessRate + Correctness │
│    ├─ Tool routing                               │
│    └─ Agent Inspector (localhost:8082)            │
├─────────────────────────────────────────────────┤
│  AWS Bedrock（LLM 服務）                          │
│    ├─ Claude Haiku 4.5                           │
│    ├─ Claude Sonnet 4.6                          │
│    └─ Amazon Nova Lite                           │
└─────────────────────────────────────────────────┘
```

## 關鍵觀念

- **AgentCore 不是獨立的 LLM**，它底下用的就是 Bedrock
- 走 AgentCore 或直連 boto3，最終都打同一個 Bedrock 服務
- AgentCore 的價值在**管理能力**（記憶/評測/追蹤），不在 LLM 本身

## 環境變數控制

| 變數 | 效果 |
|------|------|
| `BEDROCK_MODEL_ID` | 設了 = 能用真 LLM；沒設 = offline |
| `TRUSTFORGE_AGENTCORE=1` | 走 strands bridge（多 memory/eval）|
| `AWS_REGION` | Bedrock 的 region（預設 ap-southeast-2） |

## 競賽帳號可用模型（320566125702, us-west-2）

| Model ID | converse API | invoke_model |
|----------|-------------|--------------|
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | ✅ | ❌（不支援 us. prefix）|
| `us.anthropic.claude-sonnet-4-6` | ✅ | ❌ |
| `amazon.nova-lite-v1:0` | ✅ | ❌（body 格式不相容）|

## 兩套 Web UI

| URL | 技術 | 狀態 |
|-----|------|------|
| localhost:5173 | React + Vite | **正式 UI**（新版）|
| localhost:8799 | Python SSR (web.py) | **降為純 API**（舊版凍結）|
| localhost:8082 | AgentCore Inspector | 開發者工具 |
