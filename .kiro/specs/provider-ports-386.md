# Spec：Provider Ports/Adapters 真正接入 Runtime (#386)

> Issue: #386
> Priority: P1-core
> Size: L
> Depends: #381（Trust Kernel 完成）, #380（架構盤點）
> Aligns: #324（RAG adapter contract）

---

## Requirements

### R1: 定義 typed Protocol ports

在 `src/trustforge/ports.py` 定義以下 Protocol/ABC 介面：

- **LLMProvider**: Trust Kernel 的語言模型抽象
  - `complete(system: str, prompt: str) -> str` — 通用 LLM 完成
  - `classify_stance(claim_a: str, claim_b: str) -> str` — 語意關係分類（entailment/contradiction/neutral）

- **CacheProvider**: 快取存取抽象
  - `get(key: str) -> dict | None` — 讀取快取（miss 回 None）
  - `set(key: str, value: dict, ttl: int) -> None` — 寫入快取（帶 TTL 秒數）

- **SourceProvider**: 多源資料連接器抽象
  - `fetch(query: str, coin: str) -> list[dict]` — 依查詢與幣種抓取文件

- **ObservabilityProvider**: 可觀測性抽象
  - `emit(event: str, payload: dict) -> None` — 發送遙測事件

- **BudgetProvider**: 預算控管抽象
  - `check(model_id: str, input_tokens: int, output_tokens: int) -> bool` — 是否允許呼叫
  - `record(model_id: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None` — 記錄消耗

所有 Protocol 使用 `typing.Protocol` + `runtime_checkable` 裝飾器。

### R2: Builtin adapters（一一對應既有實作）

每個 port 至少提供一個 builtin adapter（包裝現有程式碼）：

| Port | Builtin Adapter | 包裝目標 |
|------|----------------|-----------|
| LLMProvider | BedrockLLMAdapter | `bedrock.py::BedrockClient` |
| CacheProvider | SqliteCacheAdapter | `ingestion/cache.py` |
| SourceProvider | IngestionSourceAdapter | `ingestion/base.py::Source` |
| ObservabilityProvider | LogObservabilityAdapter | `logging` |
| BudgetProvider | BudgetGuardAdapter | `budget_guard.py` |

Adapters 放置於 `src/trustforge/adapters.py`。

### R3: Fake/Spy 用於測試

提供測試用 fake 實作（放在 `tests/` 或 `src/trustforge/ports.py` 底部）：
- `FakeLLMProvider` — 回傳固定字串
- `FakeCacheProvider` — 記憶體 dict
- `FakeSourceProvider` — 回傳預設資料
- 可用於驗證「切換 provider 會改變實際 invoked adapter」

### R4: Runtime resolver

提供 `resolve_providers()` 函式：
- 讀取 `backend_registry.get_provider(key)` 判定 configured provider
- 回傳對應 adapter instance
- 未支援或初始化失敗時 fallback 到 builtin + 記錄 reason
- 回傳帶 `ProviderResolution` dataclass 記錄 key/configured/resolved/invoked/fallback_reason

### R5: 契約測試

- 每個 Protocol 至少 1 個 `isinstance` runtime 驗證測試（`runtime_checkable`）
- 切換 provider 會改變 invoked adapter 的 spy 測試
- fallback 路徑測試

### R6: 不破壞既有行為

- 所有既有測試仍須通過
- ports.py 是新增檔案，不改動既有模組公開 API

---

## Acceptance Criteria

- [ ] `src/trustforge/ports.py` 定義所有 Protocol
- [ ] 每個 Protocol 有 `@runtime_checkable` 裝飾器
- [ ] Fake 實作通過 `isinstance` 檢查
- [ ] 切換 provider 會改變實際 invoked adapter（spy 測試證明）
- [ ] 既有測試全數通過
- [ ] `tests/test_provider_ports.py` 包含以上驗證

---

## Non-Goals

- 不在此 PR 改造 pipeline/orchestrator 全面使用 ports（那是後續工作）
- 不啟用 GitHub Actions production deployment
- 不啟用 agentcore provider
