# 設計：Memory OS Schema、Migration 與 Repository

> Issue: #916 | Epic: #914

## 架構決策

### AD-1: 單一模組檔案

新增 `src/trustforge/memory_os.py`，包含：
- Dataclass 定義（MemoryEntry, MemoryLink）
- Repository class（MemoryRepository）
- Migration functions（upgrade, rollback）
- Validation logic

理由：MVP 階段表數少（2 張），放在單一模組降低複雜度，
後續可依需求拆分為 `memory_os/` package。

### AD-2: SQLite 存儲模式

遵循既有 TrustForge 慣例（`telemetry_store.py`、`ledger.py` 等），
使用 file-based SQLite，路徑由環境變數控制：

```python
def default_db_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_MEMORY_DB", "data/memory_os.db"))
```

### AD-3: Content Hash 計算

與 `skills.py::canonical_json` + SHA-256 一致：

```python
def memory_content_hash(content: dict | str) -> str:
    if isinstance(content, dict):
        payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        payload = content
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

### AD-4: Evidence Eligibility Guard

```python
def validate_evidence_eligible(entry: MemoryEntry) -> None:
    """Fail-closed validation before setting evidence_eligible=true."""
    errors = []
    if not entry.provider:
        errors.append("provider is required")
    if not entry.published_at:
        errors.append("published_at is required for evidence")
    if not entry.retrieved_at:
        errors.append("retrieved_at is required for evidence")
    if not entry.content_hash or len(entry.content_hash) != 64:
        errors.append("valid SHA-256 content_hash is required")
    if entry.kind == "dialogue":
        errors.append("dialogue memory cannot be evidence")
    if errors:
        raise ValueError(f"evidence_eligible validation failed: {'; '.join(errors)}")
```

### AD-5: Migration Versioning

使用 `_meta` table 追蹤 migration version：

```sql
CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Version 由 integer 遞增，每次 upgrade 檢查當前 version 決定要跑哪些 step。

### AD-6: Repository 介面設計

```python
class MemoryRepository:
    def __init__(self, db_path: Path | None = None):
        self._db = db_path or default_db_path()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection: ...
    def ensure_schema(self) -> None: ...  # calls upgrade()
    def save(self, entry: MemoryEntry) -> None: ...
    def get(self, memory_id: str) -> MemoryEntry | None: ...
    def find_by_kind(self, kind: str, *, limit: int = 100) -> list[MemoryEntry]: ...
    def find_by_run(self, run_id: str) -> list[MemoryEntry]: ...
    def find_eligible_evidence(self, *, limit: int = 50) -> list[MemoryEntry]: ...
    def link(self, from_id: str, to_id: str, relation: str) -> None: ...
    def get_links(self, memory_id: str) -> list[MemoryLink]: ...
    def close(self) -> None: ...
```

所有 write 操作在 transaction 內完成，失敗 rollback。

## 資料流

```
MemoryEntry(evidence_eligible=False)  ─┐
                                       ├─→ MemoryRepository.save()
                                       │       │
validate_evidence_eligible() ←─────────┘       ↓
  (only if evidence_eligible=True)         SQLite memory_entries table
                                               │
MemoryLink ────────────────────────────→ memory_links table
```

## 不觸碰的模組

- `src/trustforge/trust/` — 不 import、不修改
- `src/trustforge/agent/orchestrator.py` — 不接入（#922 負責）
- `src/trustforge/web.py` — 不加 endpoint（#923 負責）
- Production deployment config — 不變

## 測試策略

`tests/test_memory_os.py`：
- Migration upgrade → 表存在、schema 正確
- Migration rollback → 表不存在
- save + get round-trip
- save duplicate (provider, content_hash) → IntegrityError
- find_by_kind / find_by_run / find_eligible_evidence 查詢
- evidence_eligible validation：缺 provider → fail
- evidence_eligible validation：缺 published_at → fail
- evidence_eligible validation：dialogue kind → fail
- evidence_eligible validation：invalid hash format → fail
- evidence_eligible validation：all valid → pass
- link creation + self-link rejection
- get_links round-trip
