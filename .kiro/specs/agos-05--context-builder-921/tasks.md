# 實作任務：Context Builder 與 Immutable Manifest

> Issue: #921 | Epic: #914

## Task 1: 建立 context_builder.py 模組

- [ ] 建立 `src/trustforge/context_builder.py`
- [ ] 實作 `IncludedRefs` dataclass
- [ ] 實作 `ExcludedRef` dataclass
- [ ] 實作 `ContextManifest` dataclass（含 `to_dict()`, `from_dict()`）
- [ ] 定義 exclusion reason constants

## Task 2: 實作 Manifest Persistence

- [ ] 新增 `context_manifests` table migration（在 context_builder.py 或 shared migration）
- [ ] 實作 `_persist(manifest: ContextManifest) -> None`
- [ ] 實作 `get_manifest(run_id: str) -> ContextManifest | None`
- [ ] 確保 UNIQUE on run_id（一個 run 一份 manifest）
- [ ] 確保 manifest 一旦寫入不可 UPDATE

## Task 3: 實作 ContextBuilder.build()

- [ ] 實作 `__init__(memory_repo, skill_loader, tool_registry)`
- [ ] 實作 memory ref processing（stale / over_budget / evidence_ineligible exclusion）
- [ ] 實作 skill ref processing（stale / approval_required exclusion）
- [ ] 實作 tool ref processing（unknown / approval_required exclusion）
- [ ] 實作 policy ref passthrough
- [ ] 實作 token budget tracking
- [ ] 實作 `_estimate_tokens(text) -> int`
- [ ] 組裝 ContextManifest 並 persist

## Task 4: 實作 Deterministic Hash

- [ ] 實作 `_compute_hash(run_id, included, excluded, token_budget, token_used) -> str`
- [ ] 確保 canonical JSON serialization（sort_keys, no spaces）
- [ ] 驗證 same input → same hash

## Task 5: 實作 Helper Functions

- [ ] 實作 `manifest_summary(manifest) -> dict`（for report/admin disclosure）
- [ ] 實作 `get_evidence_eligible_memories(manifest) -> list[dict]`
  - 從 included memory_refs 中 filter evidence_eligible=True

## Task 6: 單元測試

- [ ] 建立 `tests/test_context_builder.py`
- [ ] 測試 build produces manifest with correct fields
- [ ] 測試 deterministic hash（same input → same hash）
- [ ] 測試 stale memory exclusion
- [ ] 測試 over-budget memory exclusion
- [ ] 測試 unapproved high-risk skill exclusion
- [ ] 測試 unknown tool exclusion
- [ ] 測試 evidence_ineligible marking
- [ ] 測試 manifest persistence + cannot overwrite
- [ ] 測試 get_manifest retrieval
- [ ] 測試 manifest_summary helper
- [ ] 測試 token estimation（ASCII + CJK mix）
- [ ] 確認 pre-push 通過
