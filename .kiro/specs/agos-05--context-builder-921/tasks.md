# 實作任務：Context Builder 與 Immutable Manifest

> Issue: #921 | Epic: #914

## Task 1: 建立 context_builder.py 模組

- [x] 建立 `src/trustforge/context_builder.py`
- [x] 實作 `IncludedRefs` dataclass
- [x] 實作 `ExcludedRef` dataclass
- [x] 實作 `ContextManifest` dataclass（含 `to_dict()`, `from_dict()`）
- [x] 定義 exclusion reason constants

## Task 2: 實作 Manifest Persistence

- [x] 新增 `context_manifests` table migration（在 context_builder.py 或 shared migration）
- [x] 實作 `_persist(manifest: ContextManifest) -> None`
- [x] 實作 `get_manifest(run_id: str) -> ContextManifest | None`
- [x] 確保 UNIQUE on run_id（一個 run 一份 manifest）
- [x] 確保 manifest 一旦寫入不可 UPDATE

## Task 3: 實作 ContextBuilder.build()

- [x] 實作 `__init__(memory_repo, skill_loader, tool_registry)`
- [x] 實作 memory ref processing（stale / over_budget / evidence_ineligible exclusion）
- [x] 實作 skill ref processing（stale / approval_required exclusion）
- [x] 實作 tool ref processing（unknown / approval_required exclusion）
- [x] 實作 policy ref passthrough
- [x] 實作 token budget tracking
- [x] 實作 `_estimate_tokens(text) -> int`
- [x] 組裝 ContextManifest 並 persist

## Task 4: 實作 Deterministic Hash

- [x] 實作 `_compute_hash(run_id, included, excluded, token_budget, token_used) -> str`
- [x] 確保 canonical JSON serialization（sort_keys, no spaces）
- [x] 驗證 same input → same hash

## Task 5: 實作 Helper Functions

- [x] 實作 `manifest_summary(manifest) -> dict`（for report/admin disclosure）
- [x] 實作 `get_evidence_eligible_memories(manifest) -> list[dict]`
  - 從 included memory_refs 中 filter evidence_eligible=True

## Task 6: 單元測試

- [x] 建立 `tests/test_context_builder.py`
- [x] 測試 build produces manifest with correct fields
- [x] 測試 deterministic hash（same input → same hash）
- [x] 測試 stale memory exclusion
- [x] 測試 over-budget memory exclusion
- [x] 測試 unapproved high-risk skill exclusion
- [x] 測試 unknown tool exclusion
- [x] 測試 evidence_ineligible marking
- [x] 測試 manifest persistence + cannot overwrite
- [x] 測試 get_manifest retrieval
- [x] 測試 manifest_summary helper
- [x] 測試 token estimation（ASCII + CJK mix）
- [x] 確認 pre-push 通過

### HEAD evidence

Implemented by `src/trustforge/context_builder.py`; immutable persistence,
deterministic hashing, exclusions, evidence eligibility, summaries, and token
budget behavior are covered by `tests/test_context_builder.py`.
