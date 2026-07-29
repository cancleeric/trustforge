# 實作任務：Memory Retrieval Lineage 與 Question RAG Adapter

> Issue: #919 | Epic: #914

## Task 1: 建立 memory_retrieval.py 模組

- [ ] 建立 `src/trustforge/memory_retrieval.py`
- [ ] 實作 `MemoryRef` dataclass
- [ ] 實作 `_is_historical_conclusion(entry) -> bool`
- [ ] 實作 `_emit_retrieval_event(run_id, refs) -> None`（寫入 execution log）

## Task 2: 實作 MemoryRetrievalAdapter

- [ ] 實作 `__init__(self, memory_repo, question_bank, execlog_path)`
- [ ] 實作 `retrieve_question_memory(query, *, run_id, limit=10) -> list[MemoryRef]`
  - 呼叫 question_bank.search()
  - 將結果轉為 MemoryEntry 並 save（if new）
  - Historical conclusion → evidence_eligible=False
  - 建立 MemoryRef list（含 rank, reason）
  - Emit retrieval event
- [ ] 實作 `retrieve_dialogue_memory(session_id, *, run_id, limit=5) -> list[MemoryRef]`
  - dialogue entries always evidence_eligible=False
  - Emit retrieval event
- [ ] 實作 `retrieve_by_kind(kind, *, run_id, limit=20) -> list[MemoryRef]`
  - 從 MemoryRepository 查詢

## Task 3: 實作 Category Counter

- [ ] 實作 `count_by_category(repo, run_id) -> dict[str, int]`
  - historical: evidence_eligible=False count
  - evidence: evidence_eligible=True count
  - used_as_evidence: 0 (placeholder for #922)

## Task 4: 單元測試

- [ ] 建立 `tests/test_memory_retrieval.py`
- [ ] 測試 retrieve_question_memory 正常流程
- [ ] 測試 historical conclusion detection（hermes-* + semantic → not eligible）
- [ ] 測試 non-historical entry with full timestamps → eligible
- [ ] 測試 dialogue memory → always not eligible
- [ ] 測試 execution log event 格式正確
- [ ] 測試 count_by_category 計算正確
- [ ] 測試 adapter 不修改 question_bank 原始 interface
- [ ] 測試 duplicate retrieval（same memory_id）→ no duplicate save

## Task 5: 回歸驗證

- [ ] 確認既有 question_bank tests 通過
- [ ] 確認不 import trust/scoring 模組
- [ ] 確認 lint / type-check 通過
- [ ] 確認 pre-push gate 通過
