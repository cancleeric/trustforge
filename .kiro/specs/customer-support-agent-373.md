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

---

## Design

```
agentcore/
├── agentcore.json        # 兩個 runtimes: TrustForge + CustomerSupport
├── aws-targets.json
└── cdk/

app/
├── TrustForge/           # 現有
└── CustomerSupport/      # 新增
    ├── main.py           # AgentCore entrypoint
    ├── tools.py          # code_generation tool
    └── pyproject.toml
```

### agentcore.json 新增

```json
{
  "runtimes": [
    { "name": "TrustForge", ... },
    {
      "name": "CustomerSupport",
      "build": "CodeZip",
      "entrypoint": "main.py",
      "codeLocation": "app/CustomerSupport/",
      "runtimeVersion": "PYTHON_3_14",
      "networkMode": "PUBLIC",
      "protocol": "HTTP"
    }
  ]
}
```

### main.py

```python
"""CustomerSupport Agent: 接收程式碼需求，用 LLM 產出程式碼。"""
from strands import Agent, tool

@tool
def generate_code(request: str, language: str = "python") -> dict:
    """根據需求產出程式碼。"""
    # 用 Bedrock Claude 生成
    ...
    return {"code": code, "language": language, "explanation": explanation}

agent = Agent(
    model="us.anthropic.claude-sonnet-4-6",
    tools=[generate_code],
    system_prompt="你是程式碼助手。根據使用者需求產出高品質程式碼。"
)
```

---

## Tasks
- [ ] 建立 app/CustomerSupport/ 目錄結構
- [ ] 寫 main.py + tools.py
- [ ] 更新 agentcore.json
- [ ] agentcore dev 測試
- [ ] 對外 API 測試（curl）
