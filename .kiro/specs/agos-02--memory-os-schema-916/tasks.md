# 實作任務：Memory OS Schema、Migration 與 Repository

> Issue: #916 | Epic: #914

## Task 1: 建立 memory_os.py 模組骨架與 dataclass

- [x] 建立 `src/trustforge/memory_os.py`
- [x] 實作 `MemoryEntry` dataclass（含所有欄位）
- [x] 實作 `MemoryLink` dataclass（link_id, from/to, relation, created_at）
- [x] 實作 `VALID_KINDS = frozenset({"episodic", "semantic", "procedural", "dialogue"})`
- [x] 實作 `VALID_RELATIONS = frozenset({"derived_from", "supersedes", "contradicts", "supports"})`
- [x] 實作 `memory_content_hash(content: dict | str) -> str`
- [x] 實作 `default_db_path() -> Path`（env var 控制）

## Task 2: 實作 Migration

- [x] 實作 `upgrade(conn: sqlite3.Connection) -> None`
  - 建立 `_meta` table（if not exists）
  - 建立 `memory_entries` table（含 CHECK constraints）
  - 建立 `memory_links` table（含 FK、CHECK constraints）
  - 建立 indexes：`(kind, evidence_eligible)`, `(run_id)`, `(provider, content_hash)` UNIQUE
  - 建立 `memory_links` UNIQUE constraint
  - 更新 `_meta` version
- [x] 實作 `rollback(conn: sqlite3.Connection) -> None`
  - DROP `memory_links`, `memory_entries`
  - 更新 `_meta` version

## Task 3: 實作 Evidence Eligibility Validation

- [x] 實作 `validate_evidence_eligible(entry: MemoryEntry) -> None`
  - 檢查 provider 非空
  - 檢查 published_at 非空
  - 檢查 retrieved_at 非空
  - 檢查 content_hash 為有效 64-char hex
  - 檢查 kind ≠ dialogue
  - 任一失敗 → raise ValueError with detail message

## Task 4: 實作 MemoryRepository

- [x] 實作 `__init__`（db_path, lazy connection）
- [x] 實作 `ensure_schema()`（call upgrade if needed）
- [x] 實作 `save(entry: MemoryEntry)`
  - 如 evidence_eligible=True → 先 validate_evidence_eligible()
  - INSERT INTO memory_entries
  - Duplicate (provider, content_hash) → raise IntegrityError
- [x] 實作 `get(memory_id: str) -> MemoryEntry | None`
- [x] 實作 `find_by_kind(kind, *, limit=100) -> list[MemoryEntry]`
- [x] 實作 `find_by_run(run_id) -> list[MemoryEntry]`
- [x] 實作 `find_eligible_evidence(*, limit=50) -> list[MemoryEntry]`
- [x] 實作 `link(from_id, to_id, relation) -> None`
  - Self-link 檢查（from_id ≠ to_id）
  - Validate relation in VALID_RELATIONS
- [x] 實作 `get_links(memory_id) -> list[MemoryLink]`
- [x] 實作 `close()`

## Task 5: 單元測試

- [x] 建立 `tests/test_memory_os.py`
- [x] 測試 migration upgrade → tables exist, schema correct
- [x] 測試 migration rollback → tables dropped
- [x] 測試 save + get round-trip
- [x] 測試 save duplicate (provider, content_hash) → error
- [x] 測試 find_by_kind filtering
- [x] 測試 find_by_run filtering
- [x] 測試 find_eligible_evidence only returns evidence_eligible=True
- [x] 測試 evidence_eligible validation: missing provider → fail
- [x] 測試 evidence_eligible validation: missing published_at → fail
- [x] 測試 evidence_eligible validation: dialogue kind → fail
- [x] 測試 evidence_eligible validation: invalid hash → fail
- [x] 測試 evidence_eligible validation: all valid → pass
- [x] 測試 link creation round-trip
- [x] 測試 self-link rejection
- [x] 測試 duplicate link rejection
- [x] 測試 invalid kind → fail on save
- [x] 確認所有測試通過

## Task 6: 整合驗證

- [x] 確認不 import trust/ 模組
- [x] 確認不修改 orchestrator / web / production config
- [x] 執行完整 pytest suite 無回歸
- [x] 執行 lint / type-check 通過
- [x] 執行 pre-push gate 通過

### HEAD evidence

Implemented by `src/trustforge/memory_os.py`; behavior is covered by
`tests/test_memory_os.py` and the commit-bound AGOS run. Status is implemented /
in review, not epic release approval.
