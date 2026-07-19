# TrustForge → AWS Bedrock AgentCore (Workshop Studio) 整理計畫書

> 狀態：分析與目錄草案設計（尚未執行任何部署腳本、未建立 AWS 資源、未跑 `agentcore deploy`）
> 參考範本：`/Users/yinghaowang/HurricaneSoft/CustomerSupport`（同 workshop Lab 1-9 產物）
> 撰寫依據：實際讀取 CustomerSupport `agentcore.json` / `main.py` / `model/load.py` / `AGENTS.md` 與 TrustForge `web.py` / `hermes.py` / `bedrock.py` / `pipeline.py` / `pyproject.toml` / `Dockerfile` / `.github/workflows/*`，並 grep 驗證 LLM 呼叫點。

---

## A. 目標與邊界

### A.1 目標
將 TrustForge 的**分析 agent runtime 層**（即 `src/trustforge/bedrock.py` + `pipeline.py` + `agent/orchestrator.py` 這條 LLM 推理鏈）整理成可被 workshop studio 的 `agentcore deploy` 一鍵部署的 AgentCore runtime，吃 workshop 已開好的 Bedrock model access（含 Claude Sonnet 等），LLM 額度由比賽提供。

### A.2 明確覆蓋範圍（AgentCore 化只做這一段）
- 新增 `agentcore/` 宣告式配置（`agentcore.json` + `agentcore/cdk/`）。
- 新增 `app/TrustForge/` runtime 入口（`main.py` + `model/load.py`），用 `BedrockAgentCoreApp` + `strands` + `strands.models.bedrock.BedrockModel` 包住既有管線。
- `bedrock.py` 的 LLM 呼叫最小改動改走 `strands.BedrockModel`（見 §D）。
- Python 3.14 升級（見 §E）。
- `agentcore.json` 與 `aws-targets.json` 的草稿（見 §C）。

### A.3 絕對保留（AgentCore 管不到，也絕不重寫/刪除）
| 資產 | 路徑 | 理由 |
|------|------|------|
| 部署腳本 35+ | `deploy/` | EC2/NGINX/Lambda/Scheduler/DynamoDB setup/budget_guard/idempotency，非 agent runtime，AgentCore 不負責 |
| 資料管線 | `src/trustforge/`（ingestion/ledger/calibration/connector_reliability/trust/ 等） | 重資產，是 TrustForge 差異化核心 |
| 前端 | `frontend/` | React/Vite，獨立部署（EC2 nginx） |
| 排程與腳本 | `scripts/` | Hermes scheduler、historical_replay、calibration、question_bank 等 |
| 既有 web 服務 | `src/trustforge/web.py` | stdlib HTTP server，App Runner/容器就緒，見 §H 待決 |
| CI | `.github/workflows/` | 既有 App Runner/EC2 部署流程保留，AgentCore 部署另加（見 §F） |

---

## B. 目錄重組設計

### B.1 建議佈局（新增項以 `[新增]` 標示）
```
trustforge/
├── agentcore/                 [新增] AgentCore 宣告式配置
│   ├── agentcore.json         [新增] runtime/memories/gateways/policy/onlineEval
│   ├── aws-targets.json       [新增] account + region（workshop 臨時值）
│   ├── .env.local             [新增] gitignored，本地開發用
│   └── cdk/                   [新增] agentcore deploy 自動合成
├── app/                       [新增] AgentCore runtime 程式碼
│   └── TrustForge/
│       ├── main.py            [新增] BedrockAgentCoreApp 入口
│       ├── model/
│       │   └── load.py        [新增] load_model() → BedrockModel
│       └── tool/              [新增] 若有 Lambda gateway，放 tool schema
├── src/trustforge/            [保留] 管線程式（bedrock.py/pipeline.py/orchestrator.py 等）
├── deploy/                    [保留] 35+ shell
├── frontend/                  [保留]
├── scripts/                   [保留]
├── .github/workflows/         [保留 + 可能新增 agentcore-deploy.yml]
├── Dockerfile                 [修改] 見 §E（僅當決定用 Container build 時）
├── pyproject.toml             [修改] 見 §E
└── ...
```

### B.2 `app/TrustForge/main.py` 如何 import 既有的 `src/trustforge`
**關鍵原則：不複製管線邏輯，直接 import。**

AgentCore runtime 的 `codeLocation` 是 `app/TrustForge/`（CodeZip build），部署時 `src/trustforge` 必須一起被打包進 zip。兩種接法：

**方案 B.2-a（推薦）：把 `src/` 納入 CodeZip 打包範圍**
- `agentcore deploy` 的 CodeZip 預設只打包 `codeLocation` 目錄。需在 `agentcore.json` runtime 的 `codeLocation` 設為 repo 根或調整 build 設定，讓 `src/` 被包含。
- `app/TrustForge/main.py` 頂部把 `src` 加入 `sys.path`：
  ```python
  import sys
  from pathlib import Path
  sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
  from trustforge.pipeline import run, run_comparison
  from trustforge.schema import QuestionType
  ```
- 這樣 `main.py` 呼叫 `run(coin, query, qtype, data_mode=..., llm_mode=...)`，直接吃到既有管線 + 既有的 `$0` 模式（sample=1 / llm_mode=off）。

**方案 B.2-b：把 `src/trustforge` 當成可安裝套件一併裝進 runtime**
- 在 `app/TrustForge/` 放一個 `pyproject.toml` 或 `requirements.txt` 指向 `src/`（或 `pip install -e ../../`），讓 `import trustforge` 可用。
- 風險：AgentCore CodeZip build 對本地 editable install 支援需驗證（建議先走 B.2-a）。

### B.3 main.py 與既有管線的對接語意
- 既有 `pipeline.run()` 已經解耦 `data_mode` / `llm_mode`（見 `pipeline.py` L25-49），`main.py` 只需把 AgentCore 收到的 `prompt` 解析成 `(coin, query, qtype, data_mode, llm_mode)` 轉呼叫 `run()`。
- 既有 `BedrockClient(offline=..., stance_offline=...)` 由 `pipeline.run()` 內部根據 `llm_mode` 自行建構（pipeline.py L203），`main.py` **不需要**自己 new `BedrockClient`。
- `$0` 模式保護：當 `llm_mode="off"` 或 `data_mode="sample"` 時，`pipeline.run()` 本就走離線（不需 AWS 憑證、不需 Bedrock 額度），AgentCore runtime 也能跑——這是比賽零成本 demo 的關鍵。

---

## C. agentcore.json 草案

仿 CustomerSupport 結構。以下為 TrustForge 版草案，**標 ⚠️ 者為不確定欄位，需 CEO 確認**。

```json
{
  "$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
  "name": "TrustForge",
  "version": 1,
  "managedBy": "CDK",
  "tags": {
    "agentcore:created-by": "agentcore-cli",
    "agentcore:project-name": "TrustForge"
  },
  "runtimes": [
    {
      "name": "TrustForge",
      "build": "CodeZip",
      "entrypoint": "main.py",
      "codeLocation": "app/TrustForge/",
      "runtimeVersion": "PYTHON_3_14",
      "networkMode": "PUBLIC",
      "protocol": "HTTP",
      "requestHeaderAllowlist": [
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id",
        "Authorization"
      ],
      "authorizerType": "CUSTOM_JWT",
      "authorizerConfiguration": {
        "customJwtAuthorizer": {
          "discoveryUrl": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_XXXXXXXXX/.well-known/openid-configuration",
          "allowedClients": ["<workshop-client-1>", "<workshop-client-2>"]
        }
      }
    }
  ],
  "memories": [
    {
      "name": "TrustForgeMemory",
      "eventExpiryDuration": 30,
      "strategies": [
        { "type": "SEMANTIC", "namespaceTemplates": ["/users/{actorId}/facts"] },
        { "type": "SUMMARIZATION", "namespaceTemplates": ["/summaries/{actorId}/{sessionId}"] }
      ]
    }
  ],
  "knowledgeBases": [],
  "credentials": [],
  "evaluators": [],
  "onlineEvalConfigs": [
    {
      "name": "TrustForgeQualityMonitor",
      "agent": "TrustForge",
      "evaluators": ["Builtin.GoalSuccessRate", "Builtin.Correctness"],
      "samplingRate": 100,
      "enableOnCreate": true
    }
  ],
  "agentCoreGateways": [
    {
      "name": "trustforge-gateway",
      "protocolType": "None",
      "targets": [
        {
          "name": "RefreshSources",
          "targetType": "lambdaFunctionArn",
          "lambdaFunctionArn": {
            "lambdaArn": "arn:aws:lambda:us-west-2:<workshop-account>:function:workshop-trustforge-refresh",
            "toolSchemaFile": "app/TrustForge/tool/refresh_schema.json"
          }
        }
      ],
      "authorizerType": "CUSTOM_JWT",
      "authorizerConfiguration": {
        "customJwtAuthorizer": {
          "discoveryUrl": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_XXXXXXXXX/.well-known/openid-configuration",
          "allowedClients": ["<workshop-client-1>", "<workshop-client-2>"]
        }
      },
      "enableSemanticSearch": true,
      "exceptionLevel": "NONE",
      "policyEngineConfiguration": {
        "policyEngineName": "TrustForgePolicyEngine",
        "mode": "ENFORCE"
      }
    }
  ],
  "policyEngines": [
    {
      "name": "TrustForgePolicyEngine",
      "description": "Governs TrustForge analysis tool access",
      "policies": [
        {
          "name": "refresh_rate_policy",
          "description": "Limit source refresh frequency",
          "statement": "permit(principal, action == AgentCore::Action::\"RefreshSources___refresh_sources\", resource == AgentCore::Gateway::\"arn:aws:bedrock-agentcore:us-west-2:<workshop-account>:gateway/trustforge-trustforge-gateway-xxxx\") when { true };",
          "validationMode": "IGNORE_ALL_FINDINGS",
          "enforcementMode": "ACTIVE",
          "authorizationPhase": "INITIATE"
        }
      ]
    }
  ],
  "configBundles": [],
  "abTests": [],
  "harnesses": [],
  "datasets": [],
  "payments": []
}
```

### C.1 不確定欄位（⚠️ 需 CEO 確認）
| 欄位 | 現狀 | 待確認 |
|------|------|--------|
| `runtimes[].authorizerConfiguration.customJwtAuthorizer.discoveryUrl` | 照抄 CustomerSupport 的 `us-west-2_XXXXXXXXX` | ⚠️ workshop 實際 Cognito user pool ID（比賽現場公告或 `agentcore` CLI 初始化時產生） |
| `allowedClients` | 佔位 | ⚠️ workshop 提供的 client id 清單 |
| `agentCoreGateways[].targets[].lambdaFunctionArn.lambdaArn` | 佔位 `workshop-trustforge-refresh` | ⚠️ 是否要接 Lambda tool？對照 `deploy/deploy_lambda.sh` 部署的 Lambda（見 §H） |
| `agentCoreGateways[].targets` 內容 | 草案只放一個 RefreshSources 示意 | ⚠️ 實際要接哪些 Lambda（若決定不接 gateway，整段 `agentCoreGateways` + `policyEngines` 可刪） |
| `memories` | 草案放了 SharedMemory 式結構 | ⚠️ 是否要用 memory？（TrustForge 分析是 run-isolated、formal-run 不跨 run 記憶，見 `hermes.py` manifest；memory 可能 unnecessary） |
| `onlineEvalConfigs.evaluators` | 只放 2 個 Builtin | ⚠️ 是否要加 `Builtin.ToolSelectionAccuracy`（若有 gateway） |
| `region` | 全用 `us-west-2`（照 CustomerSupport） | ⚠️ workshop 實際 region（TrustForge 現有部署在 `ap-southeast-2`，但 workshop studio 可能是 `us-west-2`） |

---

## D. LLM 呼叫改造

### D.1 現況（grep 實證）
- **唯一真實 AWS LLM 呼叫點**：`src/trustforge/bedrock.py`
  - `BedrockClient.complete()` → `self._runtime().invoke_model(...)`（L228，主敘事模型）
  - `BedrockClient._classify_stance_impl()` → `self._stance_runtime().converse(...)`（L336，stance 子分類器）
  - 共 **2 個** AWS 呼叫點。
- `BEDROCK_MODEL_ID` 參照點：6 個檔案（`bedrock.py` / `pipeline.py` / `web.py` / `budget_guard.py` / `lambda_handler.py` / `historical_replay.py` 經由 import）。
- `BEDROCK_HAIKU_MODEL_ID`（stance 用）預設 `au.anthropic.claude-haiku-4-5-20251001-v1:0`（au. profile，需 ap-southeast-2/4/6）。
- `pipeline.py` / `orchestrator.py` / `analysis_flow.py` / `historical_replay.py` 都只 `from .bedrock import BedrockClient`，不直接碰 boto3。

### D.2 改造方案：最小改動
**核心思路**：新增一個 `BedrockClient` 的 adapter 分支，當環境變數 `TRUSTFORGE_AGENTCORE=1`（或檢測到 AgentCore runtime 環境）時，改用 `strands.models.bedrock.BedrockModel` 作為 backend；否則維持既有 boto3 路徑（保留作降級 fallback）。

實作草圖（`bedrock.py` 內）：
```python
def _make_model(self):
    if os.getenv("TRUSTFORGE_AGENTCORE") == "1":
        from strands.models.bedrock import BedrockModel
        return BedrockModel(model_id=self.config.model_id)
    # 既有 boto3 路徑（fallback / 非 AgentCore 環境）
    return self._runtime()
```
- `complete()` 在 AgentCore 模式下改呼叫 `BedrockModel` 的 `messages` / `invoke` API，並從回應解析 text + usage（strands 會自動用 workshop IAM 憑證 + model access，免自管 `BEDROCK_MODEL_ID`）。
- `classify_stance` 同理：AgentCore 模式下用 `BedrockModel` 的 converse/tool-use 能力。
- **`llm_mode=off` / `sample=1` 模式完全不受影響**：這兩個模式在 `pipeline.run()` 層就決定 `offline=True`，`BedrockClient` 直接回佔位、`invoke_model` 根本不被呼叫（bedrock.py L213-215）。AgentCore 化不改這條路徑。

### D.3 取捨：保留 bedrock.py 作降級 fallback vs 直接移除
**建議：保留（不移除）。**
- 理由 1：`web.py`（App Runner/EC2 部署）與 Lambda（`lambda_handler.py`）仍走既有 boto3 路徑，移除會破壞這兩條生產路徑。
- 理由 2：離線測試 / CI（`ci.yml` 跑 `pytest -q` + offline smoke）依賴 `BedrockClient(offline=True)`，移除會炸 CI。
- 理由 3：商品化遷出 workshop 後，若自有 AWS 帳號也想用 boto3 直連（而非 strands），既有路徑仍是合法選項。
- 代價：程式碼多一層 if 分支，但邊界清晰、風險低。

### D.4 strands 依賴加入
- `pyproject.toml` `[project].dependencies` 需加 `strands-agents`（及其 bedrock extra），但**只給 AgentCore runtime 用**。
- 若用 B.2-a（sys.path 接 src），`app/TrustForge/` 需有自己的 `requirements.txt` 含 `strands-agents` + `bedrock-agentcore-runtime`，而 `src/trustforge` 本身維持零 strands 依賴（保持既有 CI 不變）。

---

## E. Python 3.14 升級清單

### E.1 現況
- `pyproject.toml`：`requires-python = ">=3.11"`
- `Dockerfile`：`FROM python:3.12-slim`
- `.venv`：目前已是 3.14（確認無誤）
- CI：`ci.yml` matrix `["3.11", "3.12"]`、`deploy-production.yml` 用 `3.12`

### E.2 升級動作清單
| 項目 | 改動 | 注意 |
|------|------|------|
| `pyproject.toml` | `requires-python = ">=3.14"`（或 `>=3.12` 以相容舊 CI，但 workshop runtime 鎖 3.14，建議直接 `>=3.14`） | 若改 `>=3.14` 會讓 CI matrix 的 3.11/3.12 失效，需同步改 CI |
| `Dockerfile` | `FROM python:3.14-slim` | 僅當決定用 Container build 才需要；若用 CodeZip（推薦），Dockerfile 不影響 AgentCore runtime |
| `.venv` | 已是 3.14，無需重建；但建議 `rm -rf .venv && python3.14 -m venv .venv && pip install -e ".[dev]"` 確保乾淨 | — |
| `ci.yml` | matrix 改 `["3.14"]`（或 `["3.12", "3.14"]` 保留向後相容） | workshop 鎖 3.14，正式比賽部署應以 3.14 為準 |
| `deploy-production.yml` | `python-version: "3.14"` | 與 CI 一致 |

### E.3 潛在風險（需 grep 驗證）
- **`__getitem__` / 舊式 API 移除**：3.14 移除部分 deprecated（如 `importlib.resources` 舊 API、`configparser` 等）。需 grep `importlib.resources` / `collections.Mapping` / `typing` 舊用法。
- **`csv` / `argparse` 行為變更**：低風險，但 `scripts/` 大量用 stdlib，建議跑一次全 `pytest` + `scripts/run_question_bank.py` 在 3.14 venv 確認。
- **boto3 相容性**：boto3>=1.34 在 3.14 應可用，但需在 3.14 venv 實際 `import boto3` 驗證（workshop runtime 自帶 boto3/botocore）。
- **`from __future__ import annotations`**：TrustForge 全碼都用，3.14 下無影響（該 future import 在 3.13+ 已 no-op，但保留無害）。
- **`match` / `walrus` 等新語法**：既有程式碼未用 3.14-only 語法，升級是單向相容（舊碼跑新解譯器），風險主要在第三方套件而非自身碼。

---

## F. 部署流程切分

### F.1 兩條獨立部署路徑
| 路徑 | 工具 | 涵蓋 | 觸發 |
|------|------|------|------|
| **AgentCore runtime** | `agentcore deploy`（CLI→CDK） | `app/TrustForge/` + `src/trustforge`（打包進 zip）的分析 agent | 比賽交付 / 分析後端 |
| **基礎設施** | `deploy/*.sh` + `scripts/` + `.github/workflows/deploy-*.yml` | EC2/NGINX/Lambda/Scheduler/DynamoDB/budget_guard/idempotency + frontend | 既有生產流程保留 |

### F.2 共存與順序依賴
- **無強依賴**：AgentCore runtime 是獨立託管服務，不依賴 EC2/NGINX 先起。
- **可選依賴**：若 `agentcore.json` 接了 Lambda gateway（§C.1），則對應 Lambda 需先由 `deploy/deploy_lambda.sh` 部署、ARN 填進 `agentcore.json`，再 `agentcore deploy`。這是唯一的跨路徑順序依賴。
- **前端**：`frontend/` 仍走 EC2 nginx（deploy-frontend-nginx.sh），若要讓前端呼叫 AgentCore runtime，需在前端 env 配 AgentCore runtime endpoint（賽後商品化再接，比賽交付可先獨立 demo）。
- **CI 擴充**：建議新增 `.github/workflows/agentcore-deploy.yml`（tag `v*` 或手動觸發），跑 `agentcore validate` + `agentcore deploy`；不動既有 `ci.yml` / `deploy-production.yml`。

---

## G. 商品化遷出路徑提示

workshop 帳號是比賽臨時的，賽後商品化需從 workshop 遷出到自有 AWS 帳號。

### G.1 會綁死 workshop 的項目
| 綁定項 | 位置 | 遷出時要換 |
|--------|------|-----------|
| `account` / `region` | `agentcore/aws-targets.json` | 換成自有帳號 + 目標 region（TrustForge 慣用 `ap-southeast-2`） |
| Cognito user pool | `agentcore.json` 的 `discoveryUrl` + `allowedClients` | 換成自有 Cognito / 或改 `authorizerType`（如 API Gateway auth） |
| IAM 角色 | AgentCore runtime 執行角色（workshop 自動建） | 換成自有 IAM role，權限需含 `bedrock:InvokeModel` + 自建 DynamoDB/Lambda 權限 |
| Lambda ARN | `agentCoreGateways[].targets[].lambdaArn` | 換成自有帳號的 Lambda（或決定不接 gateway） |
| Bedrock model access | workshop 已開（含 Claude Sonnet） | 自有帳號需重新申請 model access（Claude Sonnet / Haiku） |
| `au.` profile 限制 | `bedrock.py` 的 `BEDROCK_HAIKU_MODEL_ID` 預設 `au.anthropic.claude-haiku-4-5` | 遷到 `ap-southeast-2` 自有帳號可沿用；若換 region 需確認 model profile 可用性 |

### G.2 不綁死 workshop 的項目（可無痛遷出）
- `src/trustforge/` 全部管線邏輯（純 Python，無 AWS 硬綁）
- `deploy/` 腳本（參數化 region/account，本就為多環境設計）
- `frontend/`、`scripts/`、`pipeline.py` 的 `data_mode`/`llm_mode` 解耦設計

### G.1.1 ⚠️ 已存在版本/region 分裂（CEO親驗確認）
TrustForge 現有部署與 workshop AgentCore runtime **本就不同調**，遷出時這個分裂會被放大：
- `deploy/deploy_lambda.sh`：`RUNTIME="python3.12"` + `REGION="${REGION:-ap-southeast-2}"`
- workshop AgentCore runtime：`runtimeVersion=PYTHON_3_14` + region 多半 `us-west-2`

也就是 **Lambda 走 3.12/ap-southeast-2，AgentCore runtime 走 3.14/us-west-2**。AgentCore 化後短期可接受（比賽期間兩邊獨立跑），但商品化遷出時必須統一：建議以 `ap-southeast-2` + `python3.14` 為目標基線，Lambda 的 `RUNTIME` 也要從 3.12 升 3.14，否則同一份 `src/trustforge` 在兩邊行為可能分歧（3.14-only 語法在 Lambda 3.12 會炸）。

### G.3 遷出建議（提前預留）
- `agentcore.json` 所有 account/region 相關值用變數或獨立 `aws-targets.json` 管理，不要寫死在 runtime 邏輯裡。
- `BedrockClient` 的 model_id 維持 env 可配（現狀已做到），遷出時只換 env 不換碼。
- 比賽期間若用 workshop Cognito，商品化時評估是否改為自家 auth（如 LIDS SSO，見集團架構）。

---

## H. 風險與未決問題（需 CEO 拍板）

### H.1 架構邊界（最重要）
**`web.py` 的對外 HTTP 服務是否要整個被 AgentCore runtime 取代，還是 AgentCore 只做「分析後端」、`web.py` 繼續當 frontend 層？**
- 選項 A：AgentCore 只做分析後端（推荐）。`web.py` 繼續當 App Runner/EC2 的 HTTP 服務，內部呼叫 AgentCore runtime endpoint 做 LLM 分析。好處：前端/topology/限流/Healthcheck 全部不動，比賽交付風險最低。
- 選項 B：AgentCore 完全取代 `web.py`，前端直接打 AgentCore runtime。好處：架構最「純 AgentCore」；壞處：需重寫 routing/healthz/CORS/限流/多模式（live/real/sample）全部進 `main.py`，且 `$0` 模式與 AgentCore 的 JWT auth 模型需重新對齊，風險高。

### H.2 Gateway / Lambda 接線
- 是否要接 `agentCoreGateways`？對照 `deploy/deploy_lambda.sh` 部署的 Lambda 功能（需讀該腳本確認 Lambda 職責）。若 Lambda 只是「來源刷新」且管線內 `collect()` 已處理，可能不需要 gateway。
- 若決定不接：§C 的 `agentCoreGateways` + `policyEngines` 整段刪除，配置大幅簡化。

### H.3 Memory 是否要用
- `hermes.py` manifest 明寫 `formal_run_rule: select only snapshots... at or before run_started_at`，formal 結論是 run-isolated、不跨 run 記憶。
- AgentCore `memories`（SEMANTIC/SUMMARIZATION）主要服務「對話式連續agent」。TrustForge 分析是「一次性請求→報告」模型，memory 可能無用甚至干擾（違反 run-isolated 審計原則）。
- 建議：比賽交付先**不接 memory**（§C 的 `memories` 可刪或留空 array），商品化再評估是否需要對話歷史。

### H.4 region 選擇
- workshop studio 用 `us-west-2`（CustomerSupport 範本）；TrustForge 現有部署 `ap-southeast-2`。
- 比賽期間跟 workshop（us-west-2）還是跟既有（ap-southeast-2）？影響 `agentcore.json` region 與 `au.` profile 可用性。

### H.5 strands 依賴注入方式
- B.2-a（sys.path）vs B.2-b（editable install）需實測 AgentCore CodeZip build 哪種可行。建議先 B.2-a。

### H.6 其他
- `onlineEvalConfigs` 的 `samplingRate: 100` 在比賽 LLM 額度下是否過高？需確認比賽額度上限。
- AgentCore runtime 的 `networkMode: PUBLIC` vs `VPC`：TrustForge 管線需連外部連接器（CoinGecko 等），PUBLIC 較合理，但需確認 workshop 是否允許 runtime 出網。

---

## 附錄：驗證記錄
- 已讀 CustomerSupport：`agentcore/agentcore.json`、`app/CustomerSupport/main.py`、`app/CustomerSupport/model/load.py`、`AGENTS.md`。
- 已讀 TrustForge：`src/trustforge/web.py`、`hermes.py`、`bedrock.py`、`pipeline.py`、`pyproject.toml`、`Dockerfile`、`.github/workflows/ci.yml`、`deploy-production.yml`、`agent/orchestrator.py`（頭 40 行）、`analysis_flow.py`（頭 40 行）。
- grep 確認 LLM 呼叫點：`bedrock.py` 內 `invoke_model`（L228）+ `converse`（L336）共 **2 個**真實 AWS 呼叫；`BEDROCK_MODEL_ID` 參照於 6 檔案；`BEDROCK_HAIKU_MODEL_ID` 參照於 4 檔案。
- 確認 `.venv` 為 3.14、`deploy/` 有 `deploy_lambda.sh`（Lambda 部署腳本存在，但內容未讀，gateway 接線待 H.2 決策）。
