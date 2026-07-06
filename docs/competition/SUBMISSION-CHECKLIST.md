# 決賽交付 Checklist（8/1–2，30 小時）

> 目的：把「投稿前轉 public 的清理」與「AWS 佈署」固化成步驟，免得現場手忙腳亂。
> 標 **🧑 = 只有真人能做**（AI 不代按：接受條款 / 建 IAM / 改權限 / 開計費）。

---

## Part A — 投稿前轉 public 清理 SOP

> 前提：命題文件**未明文**要 public/private。**先確認**（問窗口 Mars Li 或看繳交平台）。
> 若維持 private 只需加評審為 collaborator，可跳過本節。若要轉 public，**逐條照做**。

1. **決策閘** 🧑：確認「公開 repo」是繳交要求（而非私有 + 授權評審）。
2. **移除官方資料集 + 清歷史**（official OHLCV 在 private 保存 OK，公開＝重散布主辦資料，可能踩界）：
   ```bash
   cd trustforge
   git rm -r --cached data/ && echo "data/" >> .gitignore
   git commit -m "chore: untrack official dataset before public release"
   # 歷史也要清（光刪最新 commit 不夠）：
   pip install git-filter-repo
   git filter-repo --path data/ --invert-paths --force
   # filter-repo 會移除 remote，需重設並 force-push
   git remote add origin https://github.com/cancleeric/trustforge.git
   git push --force --all origin
   ```
3. **Secret 掃描**（確保從未 commit 過任何密鑰）：
   ```bash
   pip install detect-secrets   # 或用 gitleaks / trufflehog
   detect-secrets scan --all-files
   git log -p | grep -iE "ank_|AKIA|aws_secret|App Password|Bearer eyJ|BEGIN .*PRIVATE KEY" || echo "clean"
   ```
   現況：`.env` / token 全走 gitignore，未進版控；仍須掃一遍確認。
4. **擦掉內部基建references**（公開前不該外露集團內網）：
   - 搜並移除：`YINGdeMacBook-Pro.local`、Gitea 內網 URL、anemone/電話總機、CLAUDE.md 集團架構、C-Suite 代號等。
   - 受影響檔：`README.md`、`docs/competition/COMPETITION.md`、`docs/architecture/AWS-ARCHITECTURE.md`、`ROADMAP.md`。
   - 建議：公開版 README 只留競賽相關；內部備註移到不公開的 Gitea 鏡像。
5. **.gitignore 確認**涵蓋：`data/`（若移除）、`.env`、`*.secret`、`out/`、`demo/hoyabit_data/`。
6. **README 可重現性** 🧑：判斷者要能照 README 跑起來——安裝、設 `AWS_REGION`/`BEDROCK_MODEL_ID`、`--offline` demo、AWS 跑法。
7. **轉 public**：
   ```bash
   gh repo edit cancleeric/trustforge --visibility public --accept-visibility-change-consequences
   ```
8. **轉後驗證**：無痕視窗開 repo 連結，確認可讀、README 步驟可跑、無殘留密鑰。
9. **Gitea 鏡像**：維持 private 內部用，不需公開。

---

## Part B — AWS 佈署 runbook

### 勘查結論（2026-06-30 親查 console）
- 帳號 `cancleeric (795930814369)`；目前預設區 **ap-southeast-2（雪梨）**。
- **Model access 頁已退役**：serverless 基礎模型**首次調用即自動啟用**，免手動開通。
- **唯一門檻**：Anthropic 模型首次使用須提交 use case（console 橫幅「Submit use case details」，每帳號一次）。
- 雪梨可用全套最新 Claude（**Opus 4.8 / Fable 5 / Sonnet 4.6 / Haiku 4.5** …），皆「无服务器 + 跨区域推理」→ 用 `apac.anthropic.claude-*` inference profile。
- Bedrock 另有 座席(Agents)/流(Flows)/知识库(KB)/防护机制(Guardrails) 可用。

### 區域決策
- 地理延遲對 15 分鐘批次 agent 無感；**選區看模型可用性**。
- 建議主區 **ap-northeast-1（東京）**（離台近、APAC profile、模型齊）；留雪梨亦可。**全程固定一區**。
- 🧑 確認主辦是否指定區/給專屬帳號、HOYA BIT 資料有無落地要求。

### 佈署步驟
0. 🧑 **提交 Anthropic use case**（Bedrock console 橫幅）——首次調用 Claude 前必做，接受條款 AI 不代按。
1. 🧑 **建 IAM**（最小權限）：app 角色需 `bedrock:InvokeModel`(+`InvokeModelWithResponseStream`) 於選定模型、S3 讀 OHLCV、CloudWatch Logs 寫。建使用者/角色＝改權限，AI 不做。
2. **取模型 ID**（CLI，可由 AI 跑驗證但不改狀態）：
   ```bash
   aws bedrock list-inference-profiles --region ap-northeast-1 \
     --query "inferenceProfileSummaries[?contains(inferenceProfileId,'anthropic')].inferenceProfileId"
   ```
   選 workhorse＝`apac.anthropic.claude-sonnet-4-6-*`（每條主張評分/judge，便宜快）；最終報告綜合可選 `apac.anthropic.claude-opus-4-8-*`。設 `BEDROCK_MODEL_ID`。
3. **本機 smoke test**：
   ```bash
   export AWS_REGION=ap-northeast-1 BEDROCK_MODEL_ID="<apac.sonnet-4-6 profile>"
   python -m trustforge.cli analyze --coin BTC --query "smoke" --type multi_source --data-dir data/data
   # 確認真打 Bedrock 回 200（非 --offline）
   ```
4. **佈署運算 + Live Demo URL**（競賽要部署網址）：
   - ⚠️ **Lambda 上限正好 15 分鐘**＝我們的執行上限，跑滿會 timeout。**長跑用 ECS Fargate / App Runner**，Lambda 只當觸發。
   - 建議最簡路徑：**AWS App Runner** 跑一個小 HTTP 服務（包住 `trustforge.cli`）→ 直接給公開 HTTPS Live Demo URL。
   - OHLCV 與產出（report/evidence/log）放 **S3**；dashboard 由服務自身或 S3+CloudFront 提供。
   - 🧑 Reddit/Bluesky 等 API key 放 **Secrets Manager**（App Password 真人辦、不經 AI）。
5. 🧑 **成本護欄**：billing 現「數據不可用」（新帳號）→ 設 **AWS Budgets** 告警。Bedrock 按 token 計費，單次跑便宜，但開發迭代用 Sonnet 控成本。
6. **交付**：Live Demo 部署網址 + 現場執行錄影（含私有 key 流程要錄全）+ 4 交付件（report/evidence/log/code）。
7. **加分 +10%** 🧑：開發用 **AWS Kiro**（AI 整合開發環境）。

### AWS 架構（簡報用）
見 `docs/architecture/AWS-ARCHITECTURE.md`。核心：API Gateway/App Runner → 服務(ingestion→trust→agent) → Bedrock(apac Claude) → S3(artifacts) + CloudWatch(log) → Dashboard。

---

## 30 小時時間盒（建議）
1. 抽題後先鎖幣種/題型 → 跑 `--offline` 確認 pipeline 通。
2. 接真實來源（Reddit/Bluesky/news/onchain）→ 真資料跑。
3. 調信任權重 + 報告行文（Bedrock）。
4. 部署 App Runner + 出 Live Demo URL + 錄影。
5. 整理 4 交付件 + 簡報（含 AWS 架構圖）。
6. 投稿前跑 Part A（若需 public）。
