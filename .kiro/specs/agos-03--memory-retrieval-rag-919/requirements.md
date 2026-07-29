# Memory Retrieval Lineage 與 Question RAG Adapter

> Issue: #919 | Epic: #914
> Depends on: #916
> Labels: agent-os, retrieval, P0

## 背景

TrustForge 已有 Question RAG（`question_bank.py`）和 dialogue history 機制。
本 issue 將這些既有 retrieval 行為映射到正式的 episodic/semantic memory reference，
並記錄 retrieval lineage（rank, reason, run_id），同時確保 historical conclusions
永遠不會進入 scoring input。

## 範圍

建立 memory retrieval adapter，包裝既有 Question RAG 與 dialogue history，
寫入 memory lineage 與 execution-log 事件。

**不包含**：Memory OS schema（#916 已完成）、Context Builder（#921）、Runtime wiring（#922）。

## 功能需求

### FR-1: Memory Retrieval Adapter

```python
class MemoryRetrievalAdapter:
    """Maps existing Question RAG and dialogue history to formal memory references."""

    def retrieve_question_memory(self, query: str, *, run_id: str, limit: int = 10) -> list[MemoryRef]
    def retrieve_dialogue_memory(self, session_id: str, *, run_id: str, limit: int = 5) -> list[MemoryRef]
    def retrieve_by_kind(self, kind: str, *, run_id: str, limit: int = 20) -> list[MemoryRef]
```

### FR-2: MemoryRef dataclass

```python
@dataclass
class MemoryRef:
    memory_id: str
    kind: str
    rank: int              # retrieval rank (1-based)
    reason: str            # e.g. "question_rag_similarity", "dialogue_recent"
    evidence_eligible: bool
    content_preview: str   # truncated content for lineage display
    run_id: str
    retrieved_at: str
```

### FR-3: Retrieval Lineage 記錄

每次 retrieval 寫入：
- `MemoryRepository.save()` — 如果是新 memory entry（from question bank）
- execution_log.jsonl — event type `memory_retrieval` with fields:
  ```json
  {
    "event": "memory_retrieval",
    "run_id": "...",
    "memories": [{"memory_id": "...", "rank": 1, "reason": "...", "evidence_eligible": false}],
    "timestamp": "..."
  }
  ```

### FR-4: Report Disclosure

提供 helper 函式供 report/admin 使用：
- `count_by_category(run_id) -> dict` — 回傳 `{"historical": N, "evidence": M, "used_as_evidence": K}`
- historical = evidence_eligible=False
- evidence = evidence_eligible=True
- used_as_evidence = 實際進入 scoring pipeline 的（由 runtime 標記）

### FR-5: Historical Conclusion Guard

- 所有 Agent 過去產出的分析結論（kind=semantic + provider starts with "hermes-"）
  → 自動設定 `evidence_eligible=False`
- 即使被 retrieve 也**不可進入 scoring input**
- Adapter 會在 retrieval 結果中明確標記 `evidence_eligible=False`

### FR-6: 既有 API 不回歸

- Question RAG 既有行為（question_bank.py 的查詢介面）不改變
- Dialogue history 既有行為不改變
- Adapter 是**包裝層**，不是取代層

## 非功能需求

- **NFR-1: 零第三方依賴**
- **NFR-2: 不修改 Trust Kernel/scoring**
- **NFR-3: 向後相容** — 既有 question/dialogue retrieval API signature 不變

## 驗收條件

1. 既有 question/dialogue retrieval 經由 adapter 映射
2. Retrieval 寫入 memory lineage 和 execution-log events
3. Report 揭露 historical/evidence/used-as-evidence counts
4. Retrieved historical conclusions 永不進入 scoring input
5. 既有 Question RAG behavior 不回歸
6. Unit, integration 和 full pre-push pass
