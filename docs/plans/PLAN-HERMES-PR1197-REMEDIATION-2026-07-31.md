# Plan：Hermes Production Audit PR #1197 — 四項待修範圍實作計劃

- 狀態：CEO 已裁決，待執行
- 分支：`feat/hermes-production-audit`（核對基準 commit `5e24a835`）
- PR：#1197（draft）
- 來源分析：CTO 技術分析（一輪）、CISO/harper 分析（一輪）、CEO 裁決（本文件即裁決的可執行版本）
- 本文件只寫計劃，不含程式碼；所有程式碼異動留待各 Phase 實作時另行提交並走既有測試/審查流程。

---

## 0. 摘要

PR #1197 的 Hermes production audit 工具（`scripts/hermes_production_audit.py` +
`src/trustforge/hermes_audit.py` + `hermes_audit_contracts.py`）在 Task 5/6 卡在
codex 對抗審與 harper 專審找出的 4 項待修範圍，CEO 核定範圍與作法如下（不重新論證，直接執行）：

| # | 範圍 | 現況問題 | 裁決作法 |
|---|------|----------|----------|
| a | 核准紀錄防偽造 | `_validate_approval_record()` 只驗 JSON 欄位格式，任何人可自寫 JSON 通過 | 綁定 digest/expected_release/output_dir、加 Ed25519 簽章、加一次性 nonce 防重放 |
| b | evidence bundle 防竄改 | `write_evidence_bundle()` 只做自洽 SHA-256（操作者自己能重算），design.md 用詞誤導 | 加 Ed25519 簽章，涵蓋全部 bundle 狀態，修正 design.md 用詞 |
| c | dead-letter 接 SQLite | `durable-dead-letter` 永遠回 `INSUFFICIENT_EVIDENCE`，從未讀取生產 EC2 本機 SQLite | 透過 SSM RunCommand 唯讀查詢 `analysis_dead_letters` |
| d | table 名稱依有效設定解析 | table 名稱永遠讀寫死字典，環境變數只壓成布林 | SSM snapshot 帶回實際值，走 allowlist 驗證 |

裁決重點：(a)(b) 複用既有 Ed25519 簽章與 nonce 原語（不新建 AWS KMS/IAM）；(c)(d) 合併成一次
`STATIC_SSM_COMMAND` 修訂（同一份新 digest）；四階段交付順序 Phase 1→2→3→4，Phase 4 為
harper + codex 雙審後才能轉正 merge；`DeadLetterSummary.release_identity` 持續為 `None`
列為已知限制而非阻斷項。

**核對結果總覽**（詳見各 Phase 與第 5 節）：裁決的整體方向與四項範圍描述在目前程式碼下**全部成立**，
以下 5 點細節與裁決文字的字面假設有落差，本計劃已依裁決精神（複用既有簽章原語、不新建 AWS 資源、
(c)+(d) 合併、Phase 1-4 順序）自行調整，不影響裁決方向：

1. `evidence_action_intent.py` 是**未接線的純 schema 契約**（docstring 明載「不驗證信任錨、不消費 nonce」），
   repo 裡實際執行 Ed25519 驗章的程式碼在 `verified_receipt_release_gate.py:368-416`
   （`_verify_ceo`，用的是另一組 dataclass `VerifiedReceiptCEOAuthorization`／`CEO_AUTHORIZATION_DOMAIN`，
   欄位是 release rollout 專用，非本次可直接套用的 schema）。本計劃複用的是**驗證模式**
   （domain-separated Ed25519 + canonical JSON + 雙角色防自我核准），比照 `_verify_ceo` 寫法，
   而非直接呼叫某個已存在的「驗證核准紀錄」函式（沒有這個函式，需要新寫，只是遵循既有慣例）。
2. `AuthenticatedLedger`（`authenticated_ledger.py`）目前**零生產呼叫者**，只在
   `tests/test_authenticated_ledger.py` 被實例化；`SignedEventLedger` 同樣只有唯一生產呼叫者
   `release_router_runtime.py`（與 release 控制面深度耦合，不適合直接借用）。本次會是
   `AuthenticatedLedger` 的**第一個生產呼叫者**，這是真正的整合工作，不是「flip a switch」。
3. `secure_keyring.py`（`read_private_keyring`/`read_public_keyring`）是更貼切的既有原語，
   已有生產呼叫者 `scripts/run_verified_receipt_release_gate.py`／
   `scripts/provision_verified_release_gate_audit.py`，CLI 用 `--xxx-public-keyring`／
   `--xxx-keyring` 檔案路徑慣例。裁決文字沒提到這個模組，但它正是「金鑰材料從哪裡讀」這一半
   缺失拼圖，本計劃納入複用（仍是既有原語，不違反「不新建」原則）。
4. CEO 裁決引用 `AuditLimits.DEFAULT_READ_BUDGETS["local-io"]`；實際上 `DEFAULT_READ_BUDGETS`
   是 `hermes_audit_contracts.py:330` 的**模組層級常數**，不是 `AuditLimits` 的類別屬性
   （`AuditLimits.defaults()` 內部引用它）。純命名路徑差異，budget 本身（`local-io`：
   request_limit=12, item_limit=32, byte_limit=1MiB, timeout=5s, retry=1）與零用量事實一致，已核實。
5. `analysis_dead_letters` SQLite 表（`analysis_flow.py:755-759`）欄位是
   `job_id, stage, coin, mode, question, snapshot_id, attempts, error, failed_at`——
   與 `hermes_audit.py:876-886`（`_dead_letters()`，服務一個從未實際觸發的假想 DynamoDB 形狀）
   期待的 `job_id, coin, stage, attempt, error_class, retry_state, release_identity` **完全不同名**。
   `release_identity` 缺欄位確認符合裁決預期的已知限制；但 `error_class`／`retry_state` 也不存在，
   需要在 Phase 2 額外設計「安全欄位映射」（見 Phase 2 細節），這比裁決文字暗示的「直接讀取即可」
   多一步映射設計工作，仍在 (c)+(d) 合併範圍內，不需要拆成第三個 phase。

---

## 1. Phase 1 — 共用簽章基礎設施 + evidence bundle 防竄改（範圍 b）

### 1.1 目標

建立本次修復共用的簽章／nonce 基礎設施，並優先套用到範圍 (b)：讓
`write_evidence_bundle()` 產出的 bundle 具備第三方無法偽造的完整性證明，
涵蓋 `complete`/`blocked`/`partial`/`insufficient-evidence`/`integrity-failure`
**所有** `AuditStatus`，並修正 design.md 對 SHA-256 用詞的誤導敘述。此 Phase
**不依賴** Phase 2 的 SSM/table 修訂，可獨立交付。

### 1.2 新增/修改檔案與函式

**新增** `src/trustforge/hermes_audit_signing.py`（沿用 `evidence_action_intent.py`
的 domain-separation 慣例、`verified_receipt_release_gate.py:368-416 _verify_ceo`
的 Ed25519 驗章寫法、`secure_keyring.py` 的金鑰檔案載入）：

- 常數：`EVIDENCE_BUNDLE_SCHEMA = "trustforge.hermes-audit-evidence-attestation/v1"`、
  `EVIDENCE_BUNDLE_SIGNING_DOMAIN = b"trustforge.hermes-audit-evidence-attestation.v1\x00"`。
- `@dataclass(frozen=True, slots=True) class EvidenceAttestationV1`：
  `schema, audit_id, canonical_payload_sha256, overall_status, target_region,
  target_instance_id, captured_at, actor, key_id, issued_at, nonce, signature`。
  `__post_init__` 驗證欄位格式（沿用 `hermes_audit_contracts._require_*` 系列，
  可從該模組 import，不重複實作）。
- `build_unsigned_attestation(*, bundle: AuditBundle, actor: str, key_id: str,
  issued_at: str, nonce: str) -> dict[str, Any]`：由 `AuditBundle` 衍生待簽 payload，
  刻意排除 `signature` 欄位。
- `attestation_signing_bytes(unsigned: Mapping[str, Any]) -> bytes`：回傳
  `EVIDENCE_BUNDLE_SIGNING_DOMAIN + canonical_json(unsigned)`（`canonical_json` 直接
  reuse `hermes_audit_contracts.canonical_json`，不重寫序列化）。
- `sign_evidence_bundle(bundle, *, private_key: bytes, key_id: str, actor: str,
  nonce_store: AuthenticatedLedger) -> EvidenceAttestationV1`：內部呼叫
  `nonce_store.append(...)`（消費 nonce，`NonceAlreadyConsumed` 直接往外拋，
  由呼叫端決定要不要重試新 nonce），再用 `Ed25519PrivateKey.from_private_bytes`
  簽章。
- `verify_evidence_bundle_attestation(attestation: EvidenceAttestationV1,
  bundle: AuditBundle, *, verification_keys: Mapping[str, bytes]) -> None`：
  驗證 `canonical_payload_sha256`／`overall_status`／`target` 與傳入 bundle 一致、
  `key_id` 存在於 `verification_keys`、Ed25519 簽章合法、`issued_at` 在合理視窗內。
  驗證失敗一律拋 `AuditContractError`（沿用既有例外型別，不新增例外階層）。

**修改** `src/trustforge/hermes_audit.py`：

- `write_evidence_bundle()`（現行 968-991 行）新增簽章輸出：在既有
  `evidence.json`/`summary.md`/`SHA256SUMS` 之後，寫入第四個檔案
  `ATTESTATION.json`（`hermes_audit_signing.EvidenceAttestationV1` 的 JSON 序列化），
  且 `SHA256SUMS` 要把 `ATTESTATION.json` 一併納入 checksum 清單。函式簽名擴充為
  `write_evidence_bundle(bundle, output_dir, *, signer)`，其中 `signer` 是一個
  最小介面（例如 `Callable[[AuditBundle], EvidenceAttestationV1]`），由呼叫端
  （CLI）決定要不要真的簽（dry-run 完全不呼叫這條路徑，維持現狀不變）。
  **不**特判 `overall_status`——目前函式本來就對任何狀態一視同仁地寫檔，維持這個
  結構即可自然涵蓋 `complete`/`blocked`/`partial`/`integrity-failure`，不需要
  额外分支。
- `run_audit()`／`build_blocked_bundle()`：不需改動邏輯，只需確認回傳的
  `AuditBundle` 在所有分支下都可被 `write_evidence_bundle()` 簽署（已經如此，
  因為所有分支都回傳同一個 `AuditBundle` 型別）。

**修改** `scripts/hermes_production_audit.py`：

- `main()`（73-105 行）新增 CLI 參數 `--signing-keyring`（`type=Path`，簽章私鑰檔，
  格式比照 `secure_keyring.read_private_keyring`）與 `--nonce-ledger-dir`
  （`type=Path`，預設 `out/audit-nonce-ledger`，交給 `AuthenticatedLedger` 的
  `test_directory_override=` 參數——沿用既有建構子介面本身即支援任意目錄，
  不需修改 `authenticated_ledger.py`；此參數名稱歷史上是為測試取的，命名本身
  易誤導，本計劃在第 5 節「風險」記錄為非阻斷 nit，不在本次範圍內重新命名）。
  非 dry-run 時載入私鑰、建立/開啟 `AuthenticatedLedger`，組成 `signer` 傳給
  `write_evidence_bundle()`。
- 新增輔助函式 `_load_signer(keyring_path, nonce_ledger_dir) -> Callable[[AuditBundle], EvidenceAttestationV1]`
  （封裝金鑰載入 + nonce 消費 + 簽章，供 `main()` 呼叫，也供測試直接單元測試）。

**修改** `.kiro/specs/hermes-production-audit/design.md`：

- 第 68 行「canonical JSON 使用排序 key 與 UTC；SHA-256 可驗證 bundle」
  改為明確區分兩層：「canonical JSON + SHA-256 提供內容自洽性摘要；
  `ATTESTATION.json` 內的 Ed25519 簽章才是防竄改依據，驗證需要對應的
  verification key」。同段補一句：涵蓋 `complete`/`blocked`/`partial`/
  `integrity-failure` 全狀態簽署，不只對「好消息」簽章。
- Evidence model 區塊（38-46 行）在檔案清單加入 `ATTESTATION.json`。

### 1.3 新增測試案例

新增 `tests/test_hermes_audit_signing.py`：

正向：
1. 合法私鑰簽署 → `verify_evidence_bundle_attestation` 通過。
2. 對 `complete`/`blocked`/`partial`/`insufficient-evidence`/`integrity-failure`
   五種 `overall_status` 各跑一次 `write_evidence_bundle`，斷言 `ATTESTATION.json`
   都存在且都能驗證通過（鎖死「不只簽好消息」這個不變式）。
3. `SHA256SUMS` 內含 `ATTESTATION.json` 一行。

負向（偽造）：
4. 竄改 `evidence.json` 內容後，用原 `ATTESTATION.json` 驗證 → 必須失敗
   （`canonical_payload_sha256` 不匹配)。
5. 用**別的** key_id 對應的簽章去驗證（key_id 不在 `verification_keys`）→ 失敗。
6. 隨機翻轉簽章 bytes 中 1 bit → `InvalidSignature`／`AuditContractError`。
7. `attestation.target_region`／`target_instance_id` 與傳入 `bundle.target` 不符
   （例如把某次 audit 的簽章貼到另一次 audit 的 evidence 上）→ 失敗。

負向（重放）：
8. 同一個 nonce 對同一個 `AuthenticatedLedger` 目錄呼叫 `sign_evidence_bundle`
   兩次 → 第二次拋 `NonceAlreadyConsumed`。
9. 兩個不同 audit_id 但共用同一個 nonce → 同樣拋 `NonceAlreadyConsumed`
   （nonce 是 ledger 全域唯一，不是 per-audit 唯一，鎖死「不能靠只改 audit_id
   繞過重放檢查」）。

負向（竄改/schema drift）：
10. `ATTESTATION.json` 缺欄位／多欄位／型別錯誤 → load 階段直接拒絕
    （比照 `hermes_audit_contracts._require_exact_keys` 風格）。
11. `issued_at` 過期或未來時間超出容許 skew → 拒絕。
12. 私鑰檔案為 symlink／權限過寬（比照 `secure_keyring.read_protected_json`
    既有的 `os.O_NOFOLLOW` + 權限檢查，這條路徑已經有保護，測試只需確認
    `hermes_audit_signing` 有正確呼叫到它、沒有繞過）。

CLI 層（併入 `tests/test_hermes_audit.py` 或新檔 `tests/test_hermes_production_audit_cli.py`
——見 Phase 3 對這支腳本測試覆蓋的整體收斂）：
13. 非 dry-run 且缺 `--signing-keyring` → 明確錯誤，不得默默略過簽章。
14. dry-run 模式完全不觸碰簽章/nonce 路徑（不得意外建立 ledger 目錄）。

### 1.4 驗收標準

- 上述測試全數通過，且 `write_evidence_bundle` 對五種狀態都輸出可驗證簽章。
- design.md 用詞修正完成，不再出現「SHA-256 可驗證 bundle」這種暗示自證即防竄改的敘述。
- 既有 32 個 `test_hermes_audit.py` + `test_hermes_audit_contracts.py` 測試維持全綠
  （baseline：本次核對時於 `feat/hermes-production-audit` 分支實測
  `32 passed, 1 warning`）。
- Phase 1 完成不代表可以拿掉 dry-run 閘門或進行真實 production read（Task 6
  的批准/雙審仍是必要條件，本 Phase 只解決 evidence 完整性這一項）。

---

## 2. Phase 2 — `STATIC_SSM_COMMAND` 合併修訂：dead-letter SQLite + table 名稱解析（範圍 c+d）

### 2.1 目標

一次改完 `STATIC_SSM_COMMAND`（`hermes_audit.py:73-177`），同時交付：
(c) 新增唯讀讀取生產 EC2 本機 `analysis_dead_letters` SQLite 表的區塊；
(d) 把三個 table 名稱環境變數的**實際值**（經 allowlist 驗證）帶回，取代目前的
tri-state `configured`/`absent`（`hermes_audit.py:175`）。兩者共用同一次
digest 變更、同一輪 harper 專審，避免兩個版本的 `STATIC_SSM_COMMAND_DIGEST`。

### 2.2 新增/修改檔案與函式

**修改** `src/trustforge/hermes_audit.py`：

- `STATIC_SSM_COMMAND`（73-177 行）heredoc python 腳本新增：
  - `FLAGS` 常量（76 行）維持不變 or 視需要新增
    `TRUSTFORGE_SHARED_ANALYSIS_DB_PATH`（目前在 `analysis_flow.py:1459` 一帶用於
    決定共享 SQLite 路徑，但**不在**目前 `FLAGS`/`_CONFIG_ALLOWLIST` 內——需要
    新增這一項才能正確定位生產 EC2 上實際的 dead-letter DB 檔案；若未設定則回退
    `<TRUSTFORGE_HOME>/out/trustforge.sqlite3`，與 `analysis_flow._db_path()`
    的預設邏輯一致）。
  - 新函式 `dead_letters(path)`：用 `sqlite3.connect(f"file:{path}?mode=ro",
    uri=True)` 開唯讀連線、額外下 `PRAGMA query_only=1`（belt-and-suspenders，
    `analysis_flow.py:603` 已有 `mode=ro` 前例，這裡疊加防禦）、
    `SELECT job_id, stage, coin, mode, question, snapshot_id, attempts, error,
    failed_at FROM analysis_dead_letters ORDER BY failed_at DESC LIMIT ?`
    （`?` 綁 `local-io` budget 的 `item_limit`，即 32，對齊裁決引用的
    `AuditLimits`/`DEFAULT_READ_BUDGETS["local-io"]` 首次啟用）；檔案不存在、
    表不存在、逾時、鎖等任何例外一律回傳 `None`（fail-closed，比照既有
    `call()`/`digest()` helper 的防禦寫法，不 raise）。
  - 對每一列做**安全欄位映射**（因為來源欄位跟 `DeadLetterSummary` 期待欄位
    不同名，見核對結果第 5 點）：
    - `job_id` → 交給 Python 端 `sha256_digest({"job_id": job_id})`（沿用既有
      `_dead_letters()` 的雜湊策略，SSM 端只需回傳原始 `job_id`，不落地）。
    - `coin` → 直接回傳，Python 端沿用既有 `_COIN_RE` 驗證。
    - `stage` → 直接回傳，Python 端沿用既有 `_require_safe_identifier`。
    - `attempts` → 對應既有 `attempt` 欄位（整數）。
    - `error` → **不可直接**當成 `error_class`（自由文字，可能含堆疊/密碼/PII，
      不符合 `_SAFE_IDENTIFIER_RE`）。SSM 腳本內建一個**有界分類函式**
      `classify_error(text)`：只取 `text` 第一個 `:` 或空白前的 token，
      且限制在既有已知例外類名的 allowlist 內（例如
      `TimeoutError, ConnectionError, ValueError, RuntimeError,
      BedrockThrottling, ...`——實際 allowlist 內容由 Phase 2 實作時盤點
      `analysis_flow.py` 中會寫入 `analysis_dead_letters.error` 的實際
      異常類型決定），不在 allowlist 內一律回傳固定哨兵字串
      `"unclassified-error"`。**原始 `error` 全文絕不離開 SSM 腳本行程**，
      這點對齊現有 STATIC_SSM_COMMAND 的既有原則（71 行註解：
      「Environment 只在行程內用於降階，全文不落地/不印出」）。
    - `retry_state`：來源表沒有這個欄位。既然一列存在於 `analysis_dead_letters`
      本身就代表「已達最終重試上限、進入死信」（`analysis_flow.py:3052`：
      `retry >= 3` 才會 INSERT），固定回傳常數字串 `"dead-lettered"`，
      **不是灌造資料**，而是由表的存在語意本身推得的正確衍生值，需在
      docstring／runbook 明確記錄這是衍生常數而非直接讀值。
    - `release_identity`：來源表沒有此欄位，固定回傳 `null`
      （裁決已列為已知限制，見第 4 節）。
  - `_validate_ssm_snapshot()`（418-478 行）新增 `dead_letters` 鍵的 schema
    驗證：型別、上限筆數（≤32）、每列欄位型別、`error_class` 必須符合
    `_SAFE_IDENTIFIER_RE` 或等於哨兵值、`job_id`／`coin`／`stage` 通過既有
    safe-text 檢查——複用 `hermes_audit_contracts._require_safe_identifier`
    等既有 helper，不重寫驗證邏輯。
  - `config` 鍵的驗證（既有 462-468 行）從「`configured`/`absent`」二值
    擴充為：允許值集合改成「合法 table 名稱字串（符合 `TABLE_NAME_RE`，
    50 行既有 `[A-Za-z0-9_.-]{3,255}`）或 `"absent"`」。

  對應地，`STATIC_SSM_COMMAND` heredoc 內 `config` 產生邏輯
  （175 行：`"config":{name:"configured" if service_env.get(name, "") else
  "absent" for name in FLAGS}`）改為：對 `TRUSTFORGE_CACHE_TABLE`／
  `TRUSTFORGE_SCHEDULER_RUN_TABLE`／`TRUSTFORGE_COST_LEDGER_TABLE` 三個
  table-name 類旗標回傳**經過安全字元集正則驗證**（`re.fullmatch` 同
  `TABLE_NAME_RE` 的 pattern，內嵌在 heredoc 內避免 import 額外模組）的
  實際值，不合法字元一律視為 `absent`（fail-closed，不把不合法字元帶出
  行程）；其餘布林類旗標（`TRUSTFORGE_HERMES_AUTONOMY_ENABLED` 等）維持
  現有 `configured`/`absent` tri-state不變。

- `_APPROVED_TABLE_NAMES`（588-593 行）與 `TableBinding.configured()`
  （672-679 行）改為：
  - `TableBinding.configured()` 簽名擴充為
    `configured(cls, remote_config: Mapping[str, str] | None = None)`。
  - 若 `remote_config` 提供且其中的 table-name 值通過
    `_APPROVED_TABLE_NAMES.values()` 的 allowlist 比對（即遠端回傳值必須
    等於某個已知合法 table 名稱，不是「任意字串都放行」——這裡刻意採用
    **allowlist 交集**而非「相信遠端」，維持 fail-closed），採用該值；
    否則 fall back 現有的 `_APPROVED_TABLE_NAMES` 靜態表。
  - **本機環境變數**（`os.environ`）**永遠不參與**這個決策——現有
    `test_local_table_environment_cannot_redirect_approved_bindings`
    （`tests/test_hermes_audit.py:447-455`）鎖死的不變式必須維持，只是
    「可以改變 binding 的來源」從「完全不可能」放寬為「只有**已通過
    SSM snapshot schema 驗證的遠端值**可以」，本機 shell 環境變數依舊
    無效。
  - `DynamoAuditReader.__init__`（685-701 行）呼叫端把
    `_bounded_ssm_snapshot()` 回傳的 `snapshot["config"]` 傳入
    `TableBinding.configured(remote_config=...)`，取代目前建構子預設
    直接呼叫 `TableBinding.configured()` 不帶參數的寫法；`run_audit()`
    （898-965 行）中 `DynamoAuditReader(...)` 的呼叫順序要確認
    `_bounded_ssm_snapshot` 先跑完才能建立 reader（目前程式碼順序
    936-941 行已經是先 SSM 再 Dynamo，符合需求，不需調整執行順序）。

**修改** `docs/runbooks/HERMES_PRODUCTION_AUDIT.md`：

- 第 40-46 行關於「durable analysis failure table 目前不猜測/不讀 SQLite」
  的敘述整段改寫，說明新的唯讀 SQLite 讀取方式、budget、`release_identity`
  已知限制（呼應第 4 節）。
- Required access 區塊（21-34 行）確認新增內容仍是 SSM 既有三個 API
  （不需要新 IAM 權限，SQLite 讀取是 SSM RunCommand payload 內部行為）。

### 2.3 新增測試案例

延伸 `tests/test_hermes_audit.py`（`DynamoAuditReader.collect()` 與
`_validate_ssm_snapshot` 相關區段）：

dead-letter 正向：
1. SQLite 檔案存在且有列 → `collect()` 回傳的 `DeadLetterSummary` 內容正確
   （`job_id_sha256`／`coin`／`stage`／`attempt`／`retry_state="dead-lettered"`／
   `release_identity is None`）。
2. 空表（0 列）→ 正確回傳空 tuple，不是 `INSUFFICIENT_EVIDENCE`（區分「表存在
   但沒資料」跟「讀不到表」，對齊 R4「空資料不是健康」但也不能誤判成失敗）。

dead-letter 負向：
3. SQLite 檔案不存在 → `insufficient-evidence`，不 crash。
4. `analysis_dead_letters` 表不存在（schema drift）→ `insufficient-evidence`。
5. `error` 欄位含有一段看似 API key／密碼的字串 → 分類結果必須是
   `"unclassified-error"` 或 allowlist 內名稱，**原文一律不得出現在**
   `bundle.to_dict()` 序列化結果中（比照既有
   `has_secret_like_value`／`redact_or_reject_remote_payload` 測試風格）。
6. `error` 欄位是已知 allowlist 內例外類名前綴（例如 `"TimeoutError: ..."`)
   → 分類正確取出 `"TimeoutError"`。
7. 超過 `local-io` item_limit（32）列的表 → 只取前 32 列（依 `failed_at DESC`），
   標記 `truncated`（沿用既有 `TableAudit.truncated` 語意，若
   `DeadLetterSummary`/`AggregateSummary` 型別需要擴充 truncated 旗標，
   於 Phase 2 一併調整 contracts，並補對應 contract 測試）。
8. 單列 `attempts` 欄位是負數或非整數字串（毀損資料）→ 該列被丟棄而非讓整支
   audit crash（比照既有 `_dead_letters()` 對 `ValueError`/`AuditContractError`
   的 `continue` 容錯寫法）。
9. SSM 端 `dead_letters` 回傳超過預算位元組數（`local-io` byte_limit）→
   `AuditPartial("local-io-byte-budget-exceeded")`（新 warning，需要在
   `AuditLimits`/常數新增對應字串，並在 `run_audit()` 的 blockers 收斂邏輯
   （947-957 行）中被涵蓋，不需要新增新的頂層分支，只要走既有
   `insufficient`/`warnings` 聚合路徑）。

table 名稱解析正向：
10. SSM snapshot 的 `config.TRUSTFORGE_CACHE_TABLE` 回傳一個等於
    `_APPROVED_TABLE_NAMES["connector-cache"]` 的合法值 → `TableBinding.configured(remote_config=...)`
    採用該值（現況下本來就相同值，這條測試鎖死「相同值時行為不變」）。
11. SSM snapshot 回傳一個**不在** `_APPROVED_TABLE_NAMES.values()` 內、但格式合法
    的字串（例如生產不慎切換到另一個合法命名的表）→ **拒絕採用**，
    fall back 靜態表或直接 `insufficient-evidence`（依 Phase 2 實作時決定
    「fail back」還是「fail closed」的精確語意，兩者都要各補一條測試鎖住
    選定的行為，不可以兩種行為同時發生在不同呼叫路徑）。

table 名稱解析負向（既有不變式延伸）：
12. **維持** `test_local_table_environment_cannot_redirect_approved_bindings`
    （`tests/test_hermes_audit.py:447-455`），並擴充：本機 `os.environ` 設定
    一個惡意 table 名稱，**同時** SSM snapshot 回傳合法值 → 只有 SSM snapshot
    值生效，本機環境變數完全被忽略（新增此組合情境，鎖死「本機永遠不能
    覆寫」這個安全不變式在新邏輯下依然成立）。
13. **維持** `test_contract_coverage_is_exact_and_no_raw_table_name_is_persisted`
    （`tests/test_hermes_audit.py:438-444`）——bundle 序列化結果依然不得出現
    任何 raw table 名稱字串，即使現在 table 名稱是從遠端動態決定的。
14. SSM snapshot 的 table 名稱欄位含有 shell 特殊字元／超長字串 → 被
    `TABLE_NAME_RE`／`_validate_ssm_snapshot` 拒絕，不得進入 `TableBinding`。
15. `config` 欄位 schema drift（新增未預期鍵、型別不符）→ 整個 SSM snapshot
    驗證失敗，`AuditContractError`（比照既有 462-468 行邏輯）。

### 2.4 驗收標準

- 上述 15 條測試（加上既有 32 條回歸）全數通過。
- `STATIC_SSM_COMMAND_DIGEST` 產生新值，`dry_run_plan()` 輸出的
  `static_ssm_command_sha256` 隨之改變——這是**預期且必要**的變更，需要在
  PR 描述與 harper 專審請求中明確標註「digest 變更」，避免被誤判為
  非預期漂移。
- `dry_run_plan()`／`_bounded_ssm_snapshot()` 對新增的 dead-letter 與 table
  名稱欄位維持既有的「no_mutation」「bounded」語意（不新增任何寫入類 API、
  不新增 IAM 權限需求）。
- Task 6 前置阻斷項（tasks.md 67-85 行「table 名稱未依有效設定解析」）解除，
  可在 tasks.md 該區塊補上完成紀錄。
- 此 Phase 完成後才**定案**新 digest，Phase 3 的核准紀錄簽章綁定這個新 digest
  （不是修訂前的舊 digest）。

---

## 3. Phase 3 — 核准紀錄防偽造（範圍 a）

### 3.1 目標

把 `scripts/hermes_production_audit.py:37-59`（`_validate_approval_record()`）
從「純 JSON 欄位格式檢查」升級為「Ed25519 簽章 + nonce 防重放 + 綁定
Phase 2 定案後的新 digest／`expected_release`／`output_dir`」，並把驗證邏輯
從這支零測試覆蓋的 CLI 腳本收斂進 `hermes_audit.py`/`hermes_audit_contracts.py`
（有既有測試基礎設施覆蓋的模組），複用 Phase 1 建立的簽章基礎設施
（`hermes_audit_signing.py`）。

### 3.2 新增/修改檔案與函式

**修改** `src/trustforge/hermes_audit_signing.py`（Phase 1 產物擴充，非重建）：

- 新增 approval 專用常數：
  `APPROVAL_RECORD_SCHEMA = "trustforge.hermes-audit-approval/v1"`、
  `APPROVAL_CEO_SIGNING_DOMAIN`／`APPROVAL_CPO_SIGNING_DOMAIN`／
  `APPROVAL_CISO_SIGNING_DOMAIN`／`APPROVAL_OPERATOR_SIGNING_DOMAIN`
  （比照 `evidence_action_intent.py:21-22` 的 domain-separation 慣例，
  四個角色各自獨立 domain，避免同一把私鑰跨角色簽出的簽章被誤用）。
- `@dataclass(frozen=True, slots=True) class ApprovalAttestationV1`：
  `schema, role (Literal["ceo","cpo","ciso","operator"]), region,
  instance_id, expected_release, output_dir, static_ssm_command_sha256,
  actor, issued_at, expires_at, nonce, key_id, signature`——**直接綁定**
  Phase 2 定案後的 `STATIC_SSM_COMMAND_DIGEST`、`expected_release`、
  `output_dir`（裁決要求的三個綁定欄位）。
- `build_unsigned_approval_v1(...)`／`approval_signing_bytes(...)`：
  比照 `evidence_action_intent.py:220-286` 的 `build_unsigned_evidence_action_v4`／
  `evidence_action_signing_bytes` 寫法（同一套 canonical-json + domain-prefix
  簽章位元組產生方式）。
- `validate_approval_bundle(ceo, cpo, ciso, operator, *, target,
  static_ssm_command_sha256, expected_release, output_dir, now,
  verification_keys) -> None`：
  - 四份 envelope 的 `region`/`instance_id`/`expected_release`/`output_dir`/
    `static_ssm_command_sha256` 必須逐一相等（比照
    `evidence_action_intent.py:302-331` 的 `ceo.scope() == operator.scope()`
    寫法，這裡是四路而非兩路比對）。
  - 四個角色的 `actor`／`key_id` 必須兩兩相異（防自我核准：同一人不能同時
    扮演 CEO window 批准者與 operator，也不能拿同一把私鑰簽兩個角色）。
  - 每份簽章各自對應各自的 signing domain 驗證（Ed25519 verify，比照
    `verified_receipt_release_gate.py:383-386`）。
  - `issued_at`/`expires_at` 視窗檢查（沿用 `MAX_AUTHORIZATION_LIFETIME`
    等既有常數慣例，於 `hermes_audit_signing.py` 內就近定義，不硬性複用
    `evidence_action_intent.py` 裡的 15 分鐘上限——approval window 與
    release evidence-action 的合理有效期不必然相同，實作時依 runbook
    既有「CEO window」概念決定，允許比 15 分鐘長，但仍需是**有限**視窗）。
  - 逐一消費四個 nonce（複用 Phase 1 的 `AuthenticatedLedger`／
    `NonceAlreadyConsumed`，四個角色各自的 nonce 都要防重放，其中任一個
    重複就整批拒絕——避免「三個角色是新的、一個角色重放舊簽章」這種
    局部重放攻擊）。
  - 任何一步失敗一律拋 `AuditContractError`（維持既有 CLI 的錯誤介面
    不變，呼叫端行為不需要跟著改）。

**修改** `src/trustforge/hermes_audit_contracts.py`：

- 視 Phase 3 實作需要，評估是否把 `ApprovalAttestationV1` 相關 dataclass
  的純格式驗證（不含簽章驗證與 nonce 消費）下沉到這個模組（維持
  「`hermes_audit_contracts.py` 是 stdlib-only 純資料契約層」的既有分工，
  `hermes_audit_signing.py` 才處理金鑰/簽章這類需要 `cryptography` 套件的
  部分）——這是分層細節，不影響外部行為，實作時依既有分層慣例決定。

**修改** `scripts/hermes_production_audit.py`：

- 移除 `_APPROVAL_RECORD_KEYS`（26-34 行）與 `_validate_approval_record()`
  （37-59 行）**函式本體**，改為呼叫
  `hermes_audit_signing.validate_approval_bundle(...)`（薄 CLI 轉接層，
  這正是裁決要求的「把驗證邏輯從零測試覆蓋的 CLI 腳本收斂進有測試基礎設施
  的模組」）。
- `_arguments()`（62-70 行）把單一 `--approval-record` 參數改為四個檔案路徑
  參數：`--ceo-approval`、`--cpo-approval`、`--ciso-approval`、
  `--operator-approval`（各自是一份已簽署的 `ApprovalAttestationV1` JSON），
  加上 `--approval-verification-keyring`（公鑰檔，比照
  `run_verified_receipt_release_gate.py:306-310` 的 `--xxx-public-keyring`
  慣例）。
- `main()`（73-105 行）非 dry-run 分支改呼叫新驗證函式，其餘流程
  （`create_aws_clients`、`run_audit`、`write_evidence_bundle`）不變。

**修改** `docs/runbooks/HERMES_PRODUCTION_AUDIT.md`：

- 「Production-read authorization gate (Task 6)」區塊（68-83 行）改寫成
  對應新的四份簽署核准紀錄流程與檔案格式，取代目前「單一 JSON 布林紀錄」
  的敘述。

### 3.3 新增測試案例

新增 `tests/test_hermes_audit_approval.py`（取代原本零覆蓋的隱性驗證）：

正向：
1. 四份合法、齊備、視窗內、nonce 皆未使用過的簽署紀錄 → 驗證通過。

負向（偽造/自我核准）：
2. 用純 JSON（無簽章欄位或簽章為偽造字串）→ 拒絕（對應裁決最核心的
   「任何人自己寫一份 JSON 就能通過」漏洞，鎖死回歸）。
3. `operator` 與 `ciso` 使用同一個 `key_id`（同一把私鑰簽兩個角色）→ 拒絕。
4. `operator` 與 `ceo` 的 `actor` 欄位相同（同一人）→ 拒絕。
5. 四份紀錄裡有一份的 `region`/`instance_id` 與其他三份不一致 → 拒絕。
6. 四份紀錄裡有一份綁定的 `static_ssm_command_sha256` 是**舊**（Phase 2 修訂前）
   的 digest → 拒絕（鎖死「舊核准不能核准新腳本版本」，這是裁決明確要求
   Phase 3 綁定 Phase 2 新 digest 的核心驗收點）。
7. `expected_release`／`output_dir` 與 CLI 實際傳入的參數不符 → 拒絕。

負向（重放）：
8. 同一份 `operator` 簽署紀錄用於兩次不同的稽核執行（nonce 重複）→
   第二次拒絕（`NonceAlreadyConsumed`）。
9. 把「上一次核准視窗」裡 ceo/cpo 兩份簽章跟本次新簽的 ciso/operator
   兩份混用（部分重放）→ 拒絕（因為混用會導致四份紀錄的某個共同欄位
   ——例如各自簽署時綁定的 `issued_at`/`expires_at`——對不上，或該
   nonce 已被消費過）。

負向（時效）：
10. 任一份簽署已過 `expires_at` → 拒絕。
11. `issued_at` 是未來時間（超出容許 skew）→ 拒絕。

負向（schema drift）：
12. 缺少四份中的任何一份 → 明確錯誤訊息，不得用「部分核准」矇混通過。
13. 簽署紀錄 JSON 多餘欄位／型別錯誤 → 拒絕（比照
    `evidence_action_intent.py:178-183` 的 exact-key 檢查風格）。

CLI 整合測試（延伸 `tests/test_hermes_audit.py` 現有的
`test_non_dry_cli_requires_human_approval_before_client_creation`，
`tests/test_hermes_audit.py:561-569`）：
14. 缺任一 `--xxx-approval` 參數 → `create_aws_clients` 不得被呼叫
    （維持既有「approval 驗證必須先於建立 AWS client」的既有測試精神，
    只是驗證對象從單一 `--approval-record` 換成四個新參數）。

### 3.4 驗收標準

- 上述 14 條測試 + 既有 CLI 回歸測試全數通過。
- `scripts/hermes_production_audit.py` 不再包含任何「安全判斷邏輯」本體
  （只剩 argparse + 轉接呼叫），驗證邏輯 100% 在有測試覆蓋的
  `hermes_audit_signing.py`/`hermes_audit_contracts.py` 內。
- runbook 更新完成，四份簽署紀錄的產生方式（誰用哪把私鑰簽、去哪裡拿
  verification keyring）有明確記載。
- Phase 3 完成後，四項待修範圍 (a)(b)(c)(d) 全部落地，進入 Phase 4 雙審。

---

## 4. Phase 間相依關係與交付順序

```text
Phase 1（範圍 b：evidence bundle 簽章）
  │  獨立，不依賴 Phase 2/3，可先行 PR/先行 review
  ▼
Phase 2（範圍 c+d：STATIC_SSM_COMMAND 合併修訂，定案新 digest）
  │  依賴 Phase 1 只在於「共用 hermes_audit_signing.py 模組已存在」
  │  這一點是弱依賴（Phase 2 本身不簽章，但若 Phase 1 尚未合併，
  │  Phase 2 仍可獨立開發測試，只是最終合併順序建議 1 先進 develop）
  ▼
Phase 3（範圍 a：核准紀錄簽章，綁定 Phase 2 定案後的新 digest）
  │  硬依賴 Phase 1（複用 hermes_audit_signing.py 的簽章/nonce基礎設施）
  │  硬依賴 Phase 2（必須綁定 Phase 2 完成後的最終 STATIC_SSM_COMMAND_DIGEST，
  │  若 Phase 3 先做，之後 Phase 2 改 digest 會讓 Phase 3 的綁定值全部作廢重工）
  ▼
Phase 4（全部完成後，harper + codex 雙審，通過後 PR #1197 draft → ready → merge）
```

**交付順序建議**：Phase 1 → Phase 2 → Phase 3 依序各自開一個小 PR（或
維持同一 PR #1197 內的獨立 commit），**不要**併成一個大 commit 一次送審——
三個 phase 各自的測試範圍、風險面不同，分開審查才能定位問題。Phase 3
絕對不能搶在 Phase 2 定案 digest 之前開始綁定實作（否則會產生裁決明確
要避免的「兩個 digest 版本」問題）。

---

## 5. 風險與已知限制

### 5.1 已知限制（不阻斷，需記錄於 design.md／runbook）

1. **`DeadLetterSummary.release_identity` 恆為 `None`**（裁決已預先核准）：
   `analysis_dead_letters` 來源表（`analysis_flow.py:755-759`）沒有這個欄位，
   這是資料源限制，不是程式缺陷。需在 design.md 與
   `docs/runbooks/HERMES_PRODUCTION_AUDIT.md` 明確註記：稽核無法對死信任務
   做 release 對照，若未來需要這個能力，屬於 `analysis_flow.py` 的資料表
   異動（migration，屬 CDO 體系，不在本次範圍）。
2. **`retry_state` 為衍生常數而非直接讀值**：由於來源表只保存終態死信
   （`analysis_flow.py:3052` 顯示只有 `retry >= 3` 才 INSERT），本次固定回傳
   `"dead-lettered"`。這在邏輯上正確，但如果未來 `analysis_flow.py` 的死信
   政策改變（例如允許非終態原因也寫入這張表），這個假設會失效且**不會
   自動被發現**——需在 runbook 記一筆「假設前提」，並在 Phase 2 測試中
   加一條「若假設被打破」的顯式回歸標記（例如註解引用本文件），供未來
   異動 `analysis_flow.py` 的人有跡可循。
3. **`error_class` allowlist 需要人工盤點且會隨程式演進而過時**：Phase 2
   設計的 `classify_error()` 是一個固定 allowlist，不是動態學習分類器。
   新的例外型別出現時會被歸類為 `"unclassified-error"`（fail-closed，
   不是錯誤，只是證據精細度降低），需要記錄「這個 allowlist 需要人工
   定期核對 `analysis_flow.py` 的例外用法」。
4. **【CEO 審查後裁定】table 名稱解析採 fail-closed，不做靜默 fallback**：
   2.3 節第 11 條與本節原列的「fail back vs fail closed」二選一，CEO 審查
   計劃時已拍板——遠端 SSM snapshot 回傳的 table 名稱若合法但不在
   `_APPROVED_TABLE_NAMES.values()` allowlist 內，一律視為
   `insufficient-evidence`，**不得** fall back 到靜態表繼續讀舊 table。
   理由：此工具全篇遵循 fail-closed 設計（R4「空資料不是健康」），
   靜默 fallback 等於在生產設定已經漂移到非預期 table 時，稽核工具仍然
   安靜地讀取舊表、看起來一切正常，這正是會掩蓋真實生產漂移的行為，
   與整個稽核工具的信任模型矛盾。Phase 2 實作與測試（2.3 節第 11 條）
   須依此裁定撰寫，不再是開放選項。

### 5.2 風險（需注意但非阻斷）

1. **`AuthenticatedLedger` 是本次的第一個生產呼叫者**（核對結果第 2 點）：
   風險在於這個類別過去只被測試呼叫過，沒有生產環境的實戰驗證（例如
   長時間執行後檔案成長、並發呼叫下的 `fcntl` 鎖行為）。緩解：Phase 1
   測試需包含「連續多次簽署 + 高頻呼叫」情境（見 1.3 節），且 nonce
   ledger 目錄大小需監控（`AuthenticatedLedger` 建構子已有
   `max_file_bytes`/`max_events` 上限，屬於既有保護，只需確認預設值
   適合本次用量）。
2. **`test_directory_override` 參數命名與本次生產用途不符**（核對結果
   第 2 點延伸）：這是既有程式碼的命名 nit，不阻斷本次交付，但若之後有
   人依字面意思誤以為這個參數只能用在測試，可能會在無意間繞過或誤用。
   建議另開一張小票（不在本次 4 個範圍內）評估是否重新命名為
   `directory_override`，屬於低優先級技術債，記錄於此僅供追蹤。
3. **`STATIC_SSM_COMMAND_DIGEST` 變更是預期但敏感的異動**：任何依賴舊
   digest 做完整性比對的既有文件、票證、稽核紀錄（若有）都需要在 Phase 2
   合併後同步更新引用值，避免出現「文件寫舊 digest，實際程式是新 digest」
   的落差造成 harper 專審時的混淆。
4. **Phase 2 新增 SQLite 讀取邏輯執行在生產 EC2 本機的 SSM RunCommand
   行程內**：雖然是唯讀連線，仍需注意 SQLite 檔案若正被
   `analysis_flow.py` 的 WAL 模式寫入行程占用時，唯讀連線的 `busy_timeout`
   行為（沿用 `analysis_flow.py:603` 一帶既有的 `mode=ro` 唯讀連線慣例，
   但 SSM 腳本內的 timeout 需要比 `analysis_flow.py` 自身的 `busy_timeout`
   更短，才能符合稽核工具本身的 SSM 逾時預算，Phase 2 實作時需要明確
   設定並測試這個逾時值）。
5. **四份簽署核准紀錄的操作複雜度提高**：範圍 (a) 從「一份 JSON」變成
   「四份分別簽署的檔案 + 一份 verification keyring」，Task 6 runbook
   的實際操作步驟會變長。這是裁決刻意要求的安全性提升的必然代價，
   已在 runbook 更新範圍內處理（見 3.2 節），但正式執行前建議先跑一次
   完整的 dry-run 型演練（用測試金鑰，非生產金鑰）驗證操作手順本身
   沒有遺漏步驟。

---

## 6. 最終驗收（Phase 4）

### 6.1 完整測試套件通過門檻

- `tests/test_hermes_audit.py`、`tests/test_hermes_audit_contracts.py`、
  `tests/test_hermes_audit_signing.py`（新）、
  `tests/test_hermes_audit_approval.py`（新）全數通過，且需附上逐字
  `pytest` 輸出（passed/failed 數），不得只回報「全綠」。
- 全量 repo `pytest`（`.githooks/pre-push` 既有的 batched 全量測試）需完整
  跑過一次確認無回歸，而不是只跑改動相關的 4 個檔案（避免簽章/nonce/
  keyring 這類共用模組的異動意外影響其他已使用 `secure_keyring.py`/
  `authenticated_ledger.py` 的既有測試，即使目前這兩個模組零生產呼叫者，
  仍要跑一次確認測試面沒有隱性耦合）。
- 針對 nonce/重放與 SQLite 併發讀取這類情境，需連續執行多次
  （建議 ≥10 次）確認無 flaky（沿用鐵律：計數/重放/併發類改動需高負載
  多次驗證，不能只跑一次綠燈就結案）。

### 6.2 eye 掃描

- 依既有慣例（`docs/runbooks/shadow-observation-operator-handoff.md:81`
  「有 UI 才做 eye scan」）：本次改動皆為後端/CLI/腳本，沒有 UI 變更，
  eye scan 可略過，但需在 PR 描述明確寫「本次無 UI 變更，略過 eye scan」，
  不得默默跳過不說明。

### 6.3 codex 對抗審

- Phase 2（digest 變更）與 Phase 3（簽章/防偽造邏輯）**都必須**各自過一輪
  codex 對抗審（規則 6：安全相關修改需雙審）；Phase 1（evidence bundle
  簽章）建議也一併送審，即使裁決文字未強制，因為它是後續 Phase 3 依賴的
  共用基礎設施，值得同等把關。
- codex 對抗審重點：nonce 重放邊界情境、四方簽章的角色混淆攻擊、
  `error_class` allowlist 是否可能洩漏敏感資訊、table 名稱 allowlist
  交集邏輯是否有繞過空間。

### 6.4 harper（CISO）雙審

- 依裁決 Phase 4 明確要求：Phase 1-3 全部完成後，**重跑** harper 專審
  （不是延續舊審查意見），理由是 Phase 2 改變了 `STATIC_SSM_COMMAND_DIGEST`
  本身，舊審查基準已經過時。
- harper 專審範圍需包含：新 digest 對應的完整 heredoc 腳本內容逐行核對
  （沿用 codex 對抗審 2026-07-31 對 Task 6 前置阻斷項的既有審查慣例，
  見 `tasks.md:85`）、四方簽章/nonce 機制的信任邊界、
  `AuthenticatedLedger` 首次生產化的目錄權限與所有權假設。

### 6.5 merge 前檢查清單

- [ ] Phase 1/2/3 測試逐字輸出已附上（非「應該會過」）。
- [ ] 全量 pytest 套件跑過且無回歸（附逐字 tail 輸出）。
- [ ] `STATIC_SSM_COMMAND_DIGEST` 新舊值已在 PR 描述中明確標註變更。
- [ ] design.md／`docs/runbooks/HERMES_PRODUCTION_AUDIT.md`／`tasks.md`
      三份文件皆已同步更新（Task 6 前置阻斷項標記解除，新增簽章操作步驟）。
- [ ] 已知限制章節（`release_identity`、`retry_state` 衍生值、
      `error_class` allowlist 維護責任）已寫入 design.md 或 runbook，
      不是只存在於本計劃文件。
- [ ] codex 對抗審通過紀錄（至少涵蓋 Phase 2、Phase 3）。
- [ ] harper（CISO）專審通過紀錄，基於 Phase 1-3 完成後的最終版本
      （非舊版審查意見延用）。
- [ ] 上述全部通過後，PR #1197 才可從 draft 轉正並 merge 進 `develop`；
      merge 前不進行任何真實 production SSM 呼叫（Task 6 的受控 production
      read 仍需獨立的 CEO window + CPO/CISO 批准，屬於本計劃範圍之外的
      後續操作步驟，不因本計劃完成而自動解鎖）。
