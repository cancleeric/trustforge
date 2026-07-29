# Memory OS Schema、Migration 與 Repository

> Issue: #916 | Epic: #914
> Depends on: #915
> Labels: agent-os, database, P0
> Safety: ⛔ DB schema/migration — 需 Eric 當日授權 token

## 背景

Agent OS 需要正式的 memory persistence 層，將 episodic/semantic/procedural/dialogue
四類 memory 存入 SQLite（MVP），並建立 memory_links 關聯圖譜。所有 memory 預設
**不可作為 Evidence**，只有符合嚴格條件的才能升格。

## 範圍

實作 `memory_entries` 與 `memory_links` 兩張表的 migration、repository CRUD、
以及相關 Python dataclass 與 validation logic。

**不包含**：Trust Kernel/scoring 修改、production wiring、retrieval logic（#919）、
Context Builder 整合（#921）。

## 功能需求

### FR-1: memory_entries table

| Column | Type | Constraint |
|--------|------|-----------|
| memory_id | TEXT (UUID) | PK |
| kind | TEXT | NOT NULL, CHECK in (episodic, semantic, procedural, dialogue) |
| provider | TEXT | NOT NULL |
| content_hash | TEXT (SHA-256) | NOT NULL |
| content_ref | TEXT | NOT NULL |
| published_at | TEXT (ISO 8601) | nullable |
| retrieved_at | TEXT (ISO 8601) | NOT NULL |
| expires_at | TEXT (ISO 8601) | nullable |
| evidence_eligible | INTEGER | NOT NULL DEFAULT 0 |
| run_id | TEXT (UUID) | nullable |
| created_at | TEXT (ISO 8601) | NOT NULL |

- UNIQUE constraint on `(provider, content_hash)` — 防止重複
- Index on `(kind, evidence_eligible)`
- Index on `(run_id)`

### FR-2: memory_links table

| Column | Type | Constraint |
|--------|------|-----------|
| link_id | TEXT (UUID) | PK |
| from_memory_id | TEXT | FK → memory_entries |
| to_memory_id | TEXT | FK → memory_entries |
| relation | TEXT | NOT NULL, CHECK in (derived_from, supersedes, contradicts, supports) |
| created_at | TEXT (ISO 8601) | NOT NULL |

- UNIQUE constraint on `(from_memory_id, to_memory_id, relation)`
- Self-link 禁止（from ≠ to）

### FR-3: MemoryEntry dataclass

```python
@dataclass
class MemoryEntry:
    memory_id: str
    kind: str  # episodic | semantic | procedural | dialogue
    provider: str
    content_hash: str
    content_ref: str
    published_at: str | None
    retrieved_at: str
    expires_at: str | None
    evidence_eligible: bool  # default=False
    run_id: str | None
    created_at: str
```

### FR-4: MemoryRepository

- `save(entry: MemoryEntry) -> None` — insert or fail on duplicate
- `get(memory_id: str) -> MemoryEntry | None`
- `find_by_kind(kind: str, *, limit: int = 100) -> list[MemoryEntry]`
- `find_by_run(run_id: str) -> list[MemoryEntry]`
- `find_eligible_evidence(*, limit: int = 50) -> list[MemoryEntry]`
- `link(from_id: str, to_id: str, relation: str) -> None`
- `get_links(memory_id: str) -> list[MemoryLink]`

### FR-5: Evidence Eligibility Validation

設定 `evidence_eligible = true` 時，必須驗證：
1. `provider` 非空
2. `published_at` 非空
3. `retrieved_at` 非空
4. `content_hash` 非空且為有效 SHA-256 格式
5. kind 不是 `dialogue`（對話記錄永遠不可作為 Evidence）

不滿足任一條件 → raise `ValueError`，fail closed。

### FR-6: Migration upgrade/rollback

- 提供 `upgrade()` 函式建立表
- 提供 `rollback()` 函式 DROP 表
- Migration 版本號追蹤（simple integer version in metadata table）

## 非功能需求

- **NFR-1: 零第三方依賴** — 純 stdlib sqlite3
- **NFR-2: fail-closed** — duplicate hash, invalid identity, missing required field → 拒絕
- **NFR-3: 不觸碰 Trust Kernel** — 不 import scoring/kernel 模組
- **NFR-4: 測試覆蓋** — migration upgrade/rollback + repository CRUD + validation edge cases

## 驗收條件

1. Migration 與 repository 支援 `memory_entries` / `memory_links`
2. 預設 memory 為 non-evidentiary（`evidence_eligible=false`）
3. Evidence memory 需要 provider, published/retrieved time 與 content hash
4. Duplicate/invalid identity 與 hash fail closed
5. Migration upgrade/rollback 測試通過
6. 無 Trust Kernel/scoring/production wiring 修改
7. 完整 pre-push 通過
