# TrustForge develop → main 併版 + 上生產計劃（Phase 4）

> 日期：2026-07-24
> 擬定：gray（CPO）
> 狀態：**待 CEO 審批。未獲審批前不 merge、不部署、不碰 DB、不動 token。**
> 範圍：**只分析與寫計劃。CPO 不執行、不部署、不碰 DB。**
> 依據：CEO 2026-07-24 三軌完成事實 + `PLAN-THREE-TRACK-REMEDIATION-2026-07-24.md`

---

## 0. 計劃定位與授權邊界

本文件是 CPO 的**併版與發版處置建議**。所有執行（merge / 建分支 / 部署 / 備份）須：
1. CEO 審批本計劃後才派工；
2. 觸及 DB／migration／secret 仍需 Eric 當次 purpose token（見 §10）；
3. 部署只走既有 release workflow，不走 GitHub Actions（TrustForge 單人 repo，CI 故意關閉）。

**全程 CPO 不執行、不部署、不碰 DB。** 本計劃交付後由 CEO 裁定派工對象與時機。

---

## 1. 現況事實（CPO 親自查證）

### 1.1 版控分叉（鐵證）

| 指標 | 值 | 來源 |
|---|---|---|
| develop 領先 main | 231 commits | `git rev-list --count main..develop` |
| main 領先 develop | 10 commits | `git rev-list --count develop..main` |
| merge-base | `a7d9ac5` | `git merge-base develop main` |
| 改動規模 | 244 檔案，+47,465 / -2,157 | CEO 事實 |
| develop VERSION | `0.17.1` | `git show develop:VERSION` |
| main VERSION | `0.17.2` | `git show main:VERSION` |
| 最新 tag | `v0.17.2` | `git tag --sort=-v:refname` |

**雙向分叉確認**：main 與 develop 各自前進，FF 不可能，須走 merge commit。

### 1.2 併版會產生的衝突（`git merge-tree --write-tree main develop` 實測）

4 個衝突，**全部集中在 ModelHub orchestration 區塊**：

| 檔案 | 衝突類型 | 根因 |
|---|---|---|
| `docs/README.md` | content | 兩邊都改 ModelHub 段落 |
| `src/trustforge/cli.py` | content | 兩邊都加 ModelHub CLI 子命令 |
| `src/trustforge/safe_fs.py` | **add/add** | main(#351) 與 develop(#456) 各自從零建立 |
| `tests/test_safe_fs.py` | **add/add** | 同上 |

其餘 240 檔案 auto-merge 乾淨。

---

## 2. main 獨有 10 commit 處置判定（逐項）

CPO 對每個 main-only commit 做「內容是否已在 develop」查證，分三類處置。

### 2.1 分類 A — 已在 develop 重新實作（develop 為嚴格超集）→ 衝突取 develop

| main commit | 內容 | 查證 | 判定 |
|---|---|---|---|
| `3c9614d` feat: ModelHub API client (#440) | `modelhub_client.py` | develop 有（#456 帶入、#647 強化 read-only probe） | **取 develop 版** |
| `d9e7ca3` feat: ModelHub calibrator proposals (#351) | `modelhub_submit.py`/`training.py`/`safe_fs.py`/`cli.py` | develop 有；`safe_fs.py` develop 244 行/20 個 fail-closed 關鍵字 > main 164 行/13 個 | **取 develop 版**（更強化的超集） |
| `318956c` docs: ModelHub handoff | `.kiro/specs/modelhub-integration-351.md` | develop 有（重做） | **取 develop 版** |
| `ade1e08` fix(deploy): portable scheduler paths (#536) | `deploy/install_local_scheduler.sh` 等 | develop 有（重做） | **取 develop 版** |
| `37840d1` docs: scheduler incident report (#538) | `docs/reports/REPORT-2026-07-23-...` | develop 有 | **取 develop 版** |

> ⚠️ `safe_fs.py` 的 add/add 是本併版最關鍵決策點：兩邊各自實作 fail-closed 檔案處理。develop 版行數更多、fail-closed/atomic/fsync/os.replace 關鍵字密度更高（20 vs 13），且帶 read-only probe 強化（#647）。**develop 為嚴格超集，衝突全取 develop，不丟失 main 任何能力。** 但 §7 整合驗證須把 `test_safe_fs.py` 列為必跑紅綠項，確認超集判定為真（非行數幻覺）。

### 2.2 分類 B — develop 真實缺失 → 必須保留（merge 自動帶入，無衝突）

| main commit | 內容 | 查證 | 判定 |
|---|---|---|---|
| `0ab5387` fix(data): OHLCV integrity audit (#478) | `src/trustforge/data_integrity.py` + `data/ohlcv_checksums.json` + `scripts/audit_data_integrity.py` + `tests/test_data_integrity.py` + `docs/qa/M1-DATA-INTEGRITY-2026-07-22.md` | develop **全部缺失**（`git ls-tree` 0 命中） | **保留**（merge 自動帶入，無衝突） |

> 這是本併版最容易被「以為是 ModelHub 一部分就丟掉」的陷阱。OHLCV integrity audit 是獨立的資料可信稽核模組，與三軌「資料可信基座」精神一致（補強 main 已有的資料完整性防線），**務必保留**。帶入後 pre-push gate 的 backend pytest 會自動包含 `test_data_integrity.py`，須全綠。

### 2.3 分類 C — 良性附帶，merge 自然帶入

| main commit | 內容 | 判定 |
|---|---|---|
| `27fe4c5` v0.17.2 release tag | VERSION=0.17.2 | **被 v0.18.0 取代**（見 §4 版號策略）；tag 本身保留為歷史 |
| `8906ada` gitignore `.playwright-mcp/`+`.kiro/settings/` | 本機配置排除 | **保留**（merge auto-merge，add-only） |
| `dc85b51`+`d4583f7` TEAM.html（root，四隊分工） | 比賽交付物 | **保留**（root `TEAM.html`；注意 develop 另有 `docs/competition/TEAM.html` 是不同檔、不同內容，兩者共存不衝突） |

---

## 3. 併版策略

### 3.1 策略選定：merge commit + release branch（非 FF、非 squash）

| 選項 | 判定 |
|---|---|
| Fast-forward | ❌ 不可能（main 雙向分叉） |
| Squash merge | ❌ 不採。231 commits 含三軌 8 個 PR + glossary/context/wrapper 等獨立里程碑，squash 會抹掉可追溯的 PR/review/CISO 雙審歷史，違反 TrustForge「commit-bound evidence」規範 |
| **Merge commit（develop → main）** | ✅ **採用**。保留雙方歷史、保留 main 的 OHLCV/gitignore/TEAM.html、4 個 ModelHub 衝突取 develop |

### 3.2 release branch 隔離整合驗證

**不直接在 main 上 merge**。流程：

```
release/v0.18.0  ← 從 main 建立
       ↓
  merge develop 進來（在此解 4 衝突）
       ↓
  全套件整合驗證（§7）全綠
       ↓
  merge release/v0.18.0 → main（merge commit）
       ↓
  tag v0.18.0
       ↓
  部署（§8）
```

**理由**：develop 持續在動，直接在 main merge 等於把「未驗證的 merge 結果」焊死到 main。release branch 隔離後，整合驗證（含 OHLCV 測試帶入、safe_fs 超集紅綠、三軌 E2E 在合併樹重跑）都在穩定樹上完成，通過才進 main。

### 3.3 衝突解決規則（CEO 派工時照此執行）

| 衝突檔 | 解法 | 驗證 |
|---|---|---|
| `src/trustforge/safe_fs.py` | 全取 develop（`theirs`） | `test_safe_fs.py` 全綠 + fail-closed 行為親驗 |
| `tests/test_safe_fs.py` | 全取 develop（`theirs`） | 同上 |
| `src/trustforge/cli.py` | 取 develop 的 ModelHub 子命令區塊；保留 main 端非 ModelHub 改動（若有） | `test_modelhub_cli.py` 全綠 |
| `docs/README.md` | 手動 reconcile：保留 develop 的較新 ModelHub 段落，補 main 的 OHLCV/scheduler 段落索引 | 目視 + 連結完整性 |

---

## 4. 版號策略

**v0.18.0**（minor bump）。

| 候選 | 判定 |
|---|---|
| v0.17.3 (patch) | ❌ 低估。本次帶入三軌統一學習架構（analysis-quality event、delayed outcome、calibration dataset、anomaly baseline、wrapper activation、RAG gold set）+ glossary/annotated-text/asset-context，是功能性大版本 |
| **v0.18.0 (minor)** | ✅ 採用。符合語意化版號「新增向後相容功能」 |
| v1.0.0 (major) | ❌ 過早。無破壞性 API 變更（三軌 feature flag 預設 off、既有 production 行為不變） |

VERSION 檔：`0.17.1`（develop）→ `0.18.0`，在 release branch 上 commit。

---

## 5. 三軌 feature flag 與 production 安全性（併版不污染生產的依據）

### 5.1 flag / 門檻現況（CPO 查證）

| 模組 | 預設 | 機制 | 對既有 production 影響 |
|---|---|---|---|
| calibration model holdout | **OFF** | `TRUSTFORGE_ENABLE_CALIBRATION_MODEL=1` 才啟用（`trust/scoring.py:1914`） | 零（holdout 候選不進評分） |
| wrapper artifact activation | **OFF**（需人工） | 8-state FSM，`human_activation` 是硬邊界——wrapper 永不自行啟用，須 typed human activation；離線 rollback 支援 | 零（候選停在 sandbox/review，不指派） |
| analysis-quality emission | ON（emit-only） | 寫入**新建** file event store（`learning_event_store`），既有 prod 讀者不消費 | 零（只增不變） |
| delayed outcome labeler | ON（label-only） | T+1/T+7/T+14 標記寫入新 store | 零（不改既有分析輸出） |
| anomaly baseline | ON（diagnostic-only） | 只產診斷基線，不阻擋/改寫分析 | 零 |
| RAG gold set provenance | ON（provenance-only） | 記錄 gold set 來源鏈 | 零 |
| ModelHub 唯讀 probe | ON（唯讀） | 合約未到位前回 `unverified`，不送訓練 | 零 |

**結論**：三軌所有「會改變對外行為」的開關（calibration model、wrapper activation）預設 OFF；其餘都是 emit-only / diagnostic-only，寫入**新建** store，既有 production 讀者不消費。**併版上生產 = 零行為變更**，這是本計劃「可安全上生產」的核心論證。

### 5.2 即時 kill switch

- 任何三軌模組出問題 → 設 `TRUSTFORGE_ENABLE_CALIBRATION_MODEL` 保持 0（calibration）/ wrapper 停在 review 不 activation（wrapper）/ 其餘 emit-only 模組寫入新 store 不影響讀者（天然隔離）。
- wrapper 有獨立離線 rollback 路徑（`wrapper_artifact_control.py`），不依賴 ModelHub probe。

---

## 6. 備份策略（部署前強制）

TrustForge 無 PostgreSQL；狀態散落在 DynamoDB / 本機 SQLite / file event store / nginx+React 靜態檔。備份按組件分。

### 6.1 備份清單

| 組件 | 備份方式 | 還原驗證 | 必要性 |
|---|---|---|---|
| EC2 後端程式碼 | `tar -czf` 現有 `/opt/trustforge`（src/data/demo/scripts/skills/deploy）→ 下載本機 | 解壓抽檔比對 SHA | 強制 |
| 本機 SQLite stores | `tar -czf` 含 `feature_store`/`ledger`/`rate_limit_store`/`telemetry_store`/`learning_event_store` 目錄 | 解壓 + `sqlite3 .schema` 抽查 | 強制（含三軌新 store） |
| DynamoDB（budget_guard / idempotency_lease） | 確認 PITR 已啟用（`deploy/verify_cost_ledger_pitr.sh`）；必要時 point-in-time 備份 | PITR 狀態 = ENABLED | 強制 |
| nginx conf + React dist | `cp` 現行 `/etc/nginx/sites-*` + React build 目錄到備份目錄 | `nginx -t` 對備份 conf 驗證 | 強制 |
| Lambda | 現版 function ARN + version 記錄（Lambda 自帶 versioning） | 知道上一版 $LATEST SHA | 註記 |
| git rollback point | `main` 併版前 HEAD SHA 記錄（= `git rev-parse main`） | `git revert`/`reset` 回此 SHA | 強制 |

### 6.2 備份鐵律

- **備份須先驗證可還原才部署**（集團鐵律）。
- DynamoDB 不在本計劃異動範圍（三軌 no-DB），但部署若觸及 budget_guard/idempotency_lease schema 變更須另拿 Eric token（本計劃不觸及）。

---

## 7. 整合驗證計劃（在 release/v0.18.0 上，merge 進 main 前的硬門檻）

### 7.1 全套件測試（必須全綠）

| 套件 | 指令 | 預期 | 關注點 |
|---|---|---|---|
| backend pytest | `env PYTHONPATH=src python3 -m pytest -q` | 全綠（CEO 稱 ~4111 tests） | **含帶入的 `test_data_integrity.py`（#478）、`test_safe_fs.py`（develop 超集）、三軌 16 個 real AnalysisFlow E2E** |
| data contracts | `scripts/check_data_contracts.py` | 全綠 | 新 AssetContext 欄位（settlement_chain/gas_token/risk_note）契約一致 |
| source stub scan | `scripts/scan_source_stubs.py` | 無真實 stub 洩漏 | |
| competition QA | `scripts/run_question_bank.py --limit 24` | 全綠 | bedrock cap=0 |
| frontend vitest | `npm --prefix frontend test -- --run` | 全綠（CEO 稱 ~308 tests） | AnnotatedText/GlossaryTerm/SnapshotModal/TrainingStatusCard 新測 |
| frontend lint + build | `npm run lint && npm run build` | 全綠 | |
| diff check | `git diff --check` | 乾淨 | |

> 上述 = `.githooks/pre-push` gate 全集。**release branch 上必須親跑一次完整 gate 並 commit-bound 證據**（非靠 push 觸發），因為 release branch 的 merge 是手工的，不保證 push 時 hook 觸發。

### 7.2 三軌在合併樹重跑（關鍵回歸）

main 上原無三軌模組。merge 後三軌首次出現在 main 血脈，**必須在 release/v0.18.0 樹上重跑三軌 E2E**（16 個 real AnalysisFlow tests），確認 merge 解衝突未破壞三軌。

### 7.3 feature flag off 行為不變驗證

- 確認 `TRUSTFORGE_ENABLE_CALIBRATION_MODEL` 未設（= OFF）跑 backend → calibration holdout 不啟用。
- 確認 wrapper 無 `human_activation` 呼叫 → 候選不指派。
- 跑既有 production 分析路徑 smoke → 輸出與 v0.17.2 行為一致（差異只允許 emit-only 新 store 多出檔案）。

### 7.4 OHLCV audit 帶入後跑通

- `test_data_integrity.py` 在合併樹全綠（#478 自帶 230 行測試）。
- `scripts/audit_data_integrity.py --check` 對 `data/ohlcv_checksums.json` 通過。

---

## 8. 部署策略（多組件，CEO 審批後執行）

### 8.1 部署順序（由後向前，可獨立驗證）

| 順序 | 組件 | 腳本 | zero-downtime | 驗證 |
|---|---|---|---|---|
| 1 | EC2 後端 | `deploy/deploy_ec2.sh` + `deploy/zero_downtime_restart.sh` | ✅ canary:8081 健康→nginx failover→restart primary:8080→stop canary | `/api/health` 200 + 分析 smoke |
| 2 | Lambda | `deploy/deploy_lambda.sh` | Lambda 自帶 version（可瞬間切回） | Function URL smoke |
| 3 | nginx + React 前端 | `deploy/deploy_frontend_nginx.sh` + `deploy/cutover_switch.sh` | guarded transaction（候選 conf `nginx -t` 驗證才切 symlink） | 前端頁面載入 + glossary/annotated-text 渲染 |
| 4 | Schedulers | `deploy/install_local_scheduler.sh`（local launchd）/ fetch / hermes | best-effort 重啟 | 排程日誌 |

**理由**：後端先上（zero-downtime canary 保護），確認健康再上前端（前端打後端 API，後端壞了前端也壞）。scheduler 最後（best-effort，非部署 gate）。

### 8.2 zero-downtime 可行性

- 後端：`zero_downtime_restart.sh` 已實作 canary-on-backup-port + nginx `max_fails=1 fail_timeout=1s` failover，重啟期間 backup 吸收流量。✅ 可行。
- 前端：`cutover_switch.sh` 是 guarded transaction（候選 conf `nginx -t` 驗證、不碰 live symlink 直到驗證過），切換秒級。✅ 可行（切換瞬間）。
- Lambda：version 切換瞬間。✅ 可行。

### 8.3 三軌上線狀態

- **本次部署三軌 feature flag 全部維持 OFF / emit-only**（§5.1）。
- wrapper 不做 `human_activation`（候選留 sandbox/review）。
- calibration model holdout 不啟用（`TRUSTFORGE_ENABLE_CALIBRATION_MODEL` 不設）。
- 即「程式碼上去了，但對外行為與 v0.17.2 完全一致」。三軌實際啟用是**後續獨立輪次**（需 CEO 另行派工 + 可能的 token）。

---

## 9. 回滾策略

### 9.1 三層回滾（由快到慢）

| 層級 | 觸發 | 動作 | 速度 |
|---|---|---|---|
| L1 feature flag kill | 三軌某模組異常 | 確認 flag OFF / wrapper 停 review / emit-only 天然隔離 | 即時（多數情況已隔離） |
| L2 cutover 回滾 | 前端壞 | `deploy/cutover_switch.sh legacy`（秒切回 SSR） | 秒級 |
| L3 git + 重部署 | 後端/整體壞 | `git revert` 併版 merge commit（或 reset main 到 §6.1 記錄的 pre-merge SHA）→ 重跑 deploy | 分鐘級 |

### 9.2 回滾鐵律

- pre-merge main SHA（§6.1）是黃金回滾點，部署前必須記錄。
- 備份（§6）須在回滾前已驗證可還原。
- DynamoDB PITR 為 DynamoDB 資料的最後防線（本計劃不觸及 DynamoDB schema）。

---

## 10. DB / token 授權邊界（鐵律宣告）

| 動作 | 是否需 Eric token |
|---|---|
| git merge / 建分支 / tag | ❌ 否（純版控） |
| 跑 backend pytest（含 `test_data_integrity.py`） | ❌ 否（讀 fixture/checksum，不寫 DB） |
| 跑三軌 E2E（file event store） | ❌ 否（file store，不觸網） |
| 部署 EC2/Lambda/nginx | ❌ 否（部署本身，非 DB schema） |
| DynamoDB schema 異動 | ✅ **需 Eric 當次 token**（本計劃不觸及，若執行中發現需動則停手） |
| SQLite migration（`scripts/migrate_json_*_to_sqlite.py`） | ✅ **需 Eric 當次 token**（本計劃不觸及） |
| secret / token rotation | ✅ **需 Eric 主對話親授權** |

> 三軌是 no-DB（file event store），本計劃全程不觸網。若執行中發現任何需寫 DB schema 的步驟，**副手停手回報，CPO 不授權**。

---

## 11. CEO 親驗清單（部署後，親自驗證才算完成）

### 11.1 基礎健康
- [ ] EC2 `/api/health` 200
- [ ] Lambda Function URL 回應正常
- [ ] 前端首頁載入（glossary popover / annotated-text 渲染正常）
- [ ] scheduler 日誌無 crash

### 11.2 feature flag OFF 行為不變（v0.17.2 → v0.18.0 回歸）
- [ ] 既有分析路徑輸出與 v0.17.2 一致（差異僅 emit-only 新 store 多檔案）
- [ ] calibration holdout 未啟用（`TRUSTFORGE_ENABLE_CALIBRATION_MODEL` 未設）
- [ ] wrapper 候選未指派（無 `human_activation`）
- [ ] ModelHub probe 回 `unverified`（合約未到位）

### 11.3 帶入功能驗證
- [ ] OHLCV integrity audit 可跑：`scripts/audit_data_integrity.py --check` 通過
- [ ] glossary risk_note popover 顯示 ⚠️ 區塊
- [ ] annotated glossary text 在報告內可互動
- [ ] AssetContext 新欄位（settlement_chain/gas_token/risk_note）在契約與前端一致

### 11.4 三軌（emit-only，不啟用對外行為）
- [ ] file event store 有寫入（analysis-quality event、delayed outcome label）
- [ ] anomaly baseline 產診斷基線（不改分析）
- [ ] RAG gold set provenance 記錄鏈完整
- [ ] **三軌 E2E 16 測試在生產後端可重跑通過**（若環境允許）

### 11.5 安全（CISO 雙審已 PASS，部署後抽驗）
- [ ] wrapper 8-state FSM 無非法狀態轉移
- [ ] 無 wrapper 自行 activation（人工邊界有效）
- [ ] 無 secret 洩漏至 log / 靜態檔

---

## 12. 風險登記與處置

| 風險 | 機率 | 影響 | 處置 |
|---|---|---|---|
| `safe_fs.py` 取 develop 版後丟失 main 的 fail-closed 路徑 | 低 | 中 | §7.1 `test_safe_fs.py` 紅綠 + 關鍵字密度已證超集 |
| OHLCV audit 帶入後 checksum 對不上（data 檔漂移） | 低 | 中 | §7.4 audit 腳本驗；對不上則修 checksum（非阻擋，資料完整性告警） |
| merge 解衝突誤刪 main 端 cli.py 非 ModelHub 改動 | 中 | 中 | §3.3 cli.py 手動 reconcile + 全套件測試護航 |
| 三軌 emit-only 模組在生產寫入量過大撐爆磁碟 | 低 | 中 | 部署後監控 file event store 增長；異常即 flag kill |
| DynamoDB 被誤觸 schema 異動 | 極低 | 高 | §10 鐵律：副手停手，需 Eric token |
| cutover 後 TLS/ACME 異常 | 低 | 高 | §8 L2 `cutover_switch.sh legacy` 秒回滾 |

---

## 13. 交付摘要（供 CEO 裁定）

**併版策略**：merge commit（develop → main），非 FF 非 squash；建 `release/v0.18.0` 隔離整合驗證；4 個 ModelHub 衝突全取 develop（嚴格超集，已證）；OHLCV audit(#478) 真實缺失須保留（merge 自動帶入）；版號 **v0.18.0**。

**驗收門檻**：release branch 上全套件 gate（backend ~4111 + frontend ~308 + contracts + stub + QA + lint + build + diff check）全綠、三軌 16 E2E 在合併樹重跑、feature flag OFF 行為不變、OHLCV audit 跑通、safe_fs 超集紅綠。commit-bound 證據。

**部署順序**：EC2 後端（zero-downtime canary）→ Lambda → nginx+React 前端（guarded cutover）→ schedulers。三軌 flag 全 OFF，對外行為與 v0.17.2 一致。

**回滾**：L1 flag kill（即時）→ L2 cutover legacy（秒級）→ L3 git revert/reset 到 pre-merge SHA（分鐘級）。pre-merge main SHA 黃金回滾點部署前記錄。

**CEO 親驗**：§11 五大類（健康 / flag-off 回歸 / 帶入功能 / 三軌 emit / 安全抽驗），親自驗證才算完成。

**token 邊界**：全程 no-DB（三軌 file event store）。任何 DynamoDB/SQLite schema 異動停手要 Eric token。

---

> ⛔ CPO 計劃止步於此。**不 merge、不部署、不碰 DB、不動 token。** 待 CEO 審批。
