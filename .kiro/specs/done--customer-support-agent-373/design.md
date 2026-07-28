# Design

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

## agentcore.json 新增

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

## main.py

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
