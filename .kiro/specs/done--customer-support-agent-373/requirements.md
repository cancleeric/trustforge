# Spec：擴充 AgentCore CustomerSupport (#373)

> Issue: #373

---

## Requirements

### R1: CustomerSupport Agent
- agentcore.json 新增 runtime `CustomerSupport`
- entrypoint: `app/CustomerSupport/main.py`
- 功能：接收 prompt → LLM 產出程式碼 → 回傳

### R2: 對外 API
- AgentCore 自動提供 HTTP endpoint
- POST /invoke（或 AgentCore 標準 protocol）
- Request: `{"prompt": "寫一個 Python function 計算費氏數列"}`
- Response: `{"code": "def fib(n): ...", "language": "python", "explanation": "..."}`

### R3: 認證
- AgentCore API key（`/api/admin/api-keys/` 已有）
- 或 Cognito（後續）
