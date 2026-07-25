# ModelHub 唯讀端點需求 — TrustForge 三軌整合

> 日期：2026-07-25
> 來源：Eric Wang（HurricaneSoft CEO）
> 轉交：Anderson（HurricaneCore）
> 性質：跨公司 API 需求，不涉及 DB schema 或 secret 異動

## 1. 背景

TrustForge 三軌統一學習架構的第三軌「Wrapper 受控升級」（#510）需要從 ModelHub 取候選 calibration model artifact。目前 ModelHub 提供 health check + model listing + retrain trigger，但缺少 4 個唯讀端點讓 TrustForge 完成 artifact 身分驗證與 provenance 複驗。

**目前狀態**：TrustForge 的唯讀 probe（#503）誠實回報 `unverified`——probe 跑通了，但 4 個 component 缺資料無法判定 `verified`。wrapper activation 在 `unverified` 時保持 `disabled`（fail-closed 設計）。

## 2. TrustForge 已在用的 ModelHub API（僅供參考，不需改動）

| 端點 | 方法 | 用途 | 狀態 |
|------|------|------|------|
| `/health` | GET | 服務健康 | ✅ 已用 |
| `/v1/models` | GET | 列出模型 | ✅ 已用 |
| `/api/submissions/{req_no}/retrain-lightning` | POST | 觸發訓練 | ✅ 已用 |
| `/api/submissions/{req_no}/training-result` | GET | 輪詢訓練結果 | ✅ 已用 |
| `/api/external-models/{product}/{name}/path` | GET | 取模型路徑 | ✅ 已用 |

## 3. 缺少的 4 個唯讀端點（本次需求）

### 3.1 Identity — `GET /v1/identity`

**需求**：確認 API key 對應的租戶與產品身分。

TrustForge 的 probe 需要驗證「這個 key 確實屬於 trustforge 租戶」，防止 key 被誤發到錯誤租戶。

```
GET /v1/identity
X-API-Key: <key>

200 OK
{
  "tenant_id": "trustforge",
  "product": "trustforge",
  "key_id": "<key 的內部識別>",
  "key_scopes": ["read_models", "trigger_retrain"]
}
```

**錯誤行為**：無效 key → 401；有效 key 但無 identity 欄位 → 200 但回 `{"tenant_id": null}`（讓 probe 判 unverified 而非 crash）。

### 3.2 Read Access 隔離驗證 — 跨租戶 / 跨 artifact 讀取被擋

**需求**：TrustForge 用自己的 key 嘗試讀取**其他租戶**的 model 或**不存在的 artifact**時，ModelHub 必須回 403 或 404，且**不洩漏 metadata**。

這不是一個新端點——是對現有 `/v1/models` 和 `/api/external-models/{product}/{name}/path` 的**行為要求**：

| 場景 | 期望回應 |
|------|---------|
| trustforge key 讀 hurricanecore tenant 的 model | **403 Forbidden**（不回 model metadata） |
| trustforge key 讀不存在的 artifact | **404 Not Found**（不回 200 + 空） |
| trustforge key 讀自己的 model | 200 + metadata（現有行為） |

**關鍵**：403/404 的回應 body **不含任何 model metadata**（防止資訊洩漏）。

### 3.3 Artifact Checksum — `GET /v1/models/{id}` 擴充

**需求**：現有 `/v1/models` 回應的每個 model 缺少 artifact checksum。TrustForge 需要驗證 artifact 沒被替換。

**方案 A（推薦）**：擴充 `/v1/models` 或新增 `/v1/models/{id}` 回應：

```
GET /v1/models/{id}
X-API-Key: <key>

200 OK
{
  "id": 1,
  "req_no": "MH-2026-046",
  "arch": "lightgbm",
  "version_tag": "v1",
  "created_at": "2026-07-19T09:20:23.827667",

  "artifact_sha256": "a1b2c3d4e5f6...",
  "artifact_size_bytes": 45678,
  "artifact_format": "joblib",
  "artifact_url": "/api/external-models/trustforge/calibrator-v1/path"
}
```

**方案 B（替代）**：獨立端點 `GET /v1/models/{id}/checksum` 回 `{"sha256": "...", "size_bytes": ...}`。

任一方案皆可。TrustForge probe 只需要能拿到 `sha256` 和 `size_bytes`。

### 3.4 Provenance — `GET /v1/models/{id}/provenance`

**需求**：每個 model artifact 附帶完整 provenance（訓練資料來源、程式碼版本、訓練參數），讓 TrustForge 驗證 artifact 來源可信。

```
GET /v1/models/{id}/provenance
X-API-Key: <key>

200 OK
{
  "model_id": 1,
  "training_data_source": "trustforge/calibration_dataset@manifest_sha256:...",
  "training_data_rows": 2005,
  "code_version": "trustforge@v0.18.1",
  "training_params": {"arch": "lightgbm", "objective": "isotonic"},
  "trained_at": "2026-07-19T09:20:23Z",
  "trained_by": "trustforge-train-calibration"
}
```

**最低要求**：`training_data_source` + `code_version` + `trained_at`。其他欄位可選。

## 4. 安全要求

1. **所有端點都要求 `X-API-Key` header**（與現有端點一致）
2. **跨租戶讀取回 403/404 且不洩漏 metadata**
3. **artifact checksum 是 server-computed**（不是 client 上傳的）——TrustForge 下載 artifact 後重算 SHA-256 與此值比對
4. **唯讀**：這 4 個端點都不寫入、不修改 ModelHub 狀態

## 5. 優先序

| 優先 | 端點 | 理由 |
|------|------|------|
| P0 | 3.2 Read Access 隔離 | 安全核心——cross-tenant 洩漏是最嚴重的風險 |
| P0 | 3.3 Artifact Checksum | artifact 替換防護——沒有 checksum 就無法驗證完整性 |
| P1 | 3.4 Provenance | 來源信任——沒有可降級（probe 回 unverified） |
| P1 | 3.1 Identity | 租戶確認——沒有可降級 |

P0 兩項到位後，TrustForge 的 wrapper activation 可以從 `disabled` 升到 `verified`（但仍需人工啟用才進 production）。

## 6. 同步資訊

- TrustForge 的 ModelHub base URL：`http://localhost:8950`
- TrustForge 的 API key 存於 Hurricane Vault：`trustforge/dev/MODELHUB_API_KEY`
- ModelHub 目前版本：v0.12.0
- TrustForge 目前版本：v0.18.1

## 7. TrustForge 端配合

Anderson 補完端點後，TrustForge 端需要：
1. 更新 `modelhub_probe_collector.py`——收集 4 個新 component 的 observation
2. 跑 evaluator 確認 7/7 component `verified`
3. wrapper activation 從 `disabled` 升到可評估候選

**同時**，TrustForge 端也會擴充支援 AWS SageMaker 作為替代 backend（老闆裁示），讓 artifact source 不單依賴 ModelHub。
