# 設計：Memory Retrieval Lineage 與 Question RAG Adapter

> Issue: #919 | Epic: #914

## 架構決策

### AD-1: Adapter Pattern — 不替換既有模組

```
question_bank.py (existing, unchanged)
       │
       ↓
MemoryRetrievalAdapter (NEW wrapper)
       │
       ├─→ MemoryRepository.save() (register as memory entry)
       ├─→ execution_log.jsonl (lineage event)
       └─→ returns list[MemoryRef] (typed result with eligibility)
```

既有 `question_bank.py` 的 interface 保持不變。Adapter 在上層呼叫後
補充 lineage 記錄。

### AD-2: 新模組 `memory_retrieval.py`

新增 `src/trustforge/memory_retrieval.py`，包含：
- `MemoryRef` dataclass
- `MemoryRetrievalAdapter` class
- Lineage helper functions

### AD-3: Historical Conclusion Detection

```python
def _is_historical_conclusion(entry: MemoryEntry) -> bool:
    """Agent 自己過去產出的結論不可作為 Evidence."""
    return (
        entry.kind == "semantic"
        and entry.provider.startswith("hermes-")
    )
```

即使 content_hash / timestamps 完整，historical conclusion 也會被強制
`evidence_eligible = False`。

### AD-4: Execution Log Event Format

使用既有 `execlog.py` 的 append 機制，新增事件類型：

```python
def _emit_retrieval_event(run_id: str, refs: list[MemoryRef]) -> None:
    event = {
        "event": "memory_retrieval",
        "run_id": run_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "count": len(refs),
        "memories": [
            {
                "memory_id": r.memory_id,
                "kind": r.kind,
                "rank": r.rank,
                "reason": r.reason,
                "evidence_eligible": r.evidence_eligible,
            }
            for r in refs
        ],
    }
    # append to execution_log via existing mechanism
```

### AD-5: Category Counter

```python
def count_by_category(repo: MemoryRepository, run_id: str) -> dict[str, int]:
    entries = repo.find_by_run(run_id)
    historical = sum(1 for e in entries if not e.evidence_eligible)
    evidence = sum(1 for e in entries if e.evidence_eligible)
    # used_as_evidence requires runtime marker (set by #922)
    return {"historical": historical, "evidence": evidence, "used_as_evidence": 0}
```

`used_as_evidence` 在 #922 runtime integration 時才有真正數據。
本 issue 先提供 interface，預設 0。

## 資料流

```
User query ("BTC 近期走勢？")
       │
       ↓
question_bank.search(query)  → existing results
       │
       ↓
MemoryRetrievalAdapter.retrieve_question_memory()
       │
       ├─ For each result:
       │    ├─ Create MemoryEntry (kind=semantic, provider="question_bank")
       │    ├─ Check _is_historical_conclusion → set evidence_eligible
       │    ├─ MemoryRepository.save() (if new)
       │    └─ Build MemoryRef(rank, reason="question_rag_similarity")
       │
       ├─ _emit_retrieval_event(run_id, refs)
       │
       └─ return list[MemoryRef]
```

## 測試策略

`tests/test_memory_retrieval.py`：
- retrieve_question_memory maps results to MemoryRef
- Historical conclusion detection（hermes-* provider + semantic kind）
- evidence_eligible correctly set based on conditions
- Execution log event emitted correctly
- count_by_category returns correct counts
- Adapter does not modify question_bank.py interface
- Existing question_bank tests still pass (regression)
