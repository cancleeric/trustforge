# Agent OS Admin Summary API

> Issue: #923 | Epic: #914
> Depends on: #922
> Labels: agent-os, api, P1

## 背景

Agent OS Runtime (#922) 產生了 memory、skill manifest、tool invocation 和
context manifest 的 lineage data。本 issue 建立 read-only Admin API 供管理者
查詢這些資料。

## 範圍

新增 read-only Admin API endpoints（authorization-gated）。

**不包含**：activation/deployment mutation、public API、UI（#924）。

## 功能需求

### FR-1: Memory Summary API

```
GET /api/admin/agos/memories?run_id=...&kind=...&page=1&page_size=20
```

Response:
```json
{
  "items": [
    {
      "memory_id": "...",
      "kind": "episodic",
      "provider": "coingecko",
      "evidence_eligible": false,
      "content_preview": "[REDACTED]",  // sensitive content redacted
      "retrieved_at": "...",
      "lineage": {"rank": 1, "reason": "question_rag_similarity"}
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```

### FR-2: Skill Manifest API

```
GET /api/admin/agos/skills?run_id=...&family=...&page=1&page_size=20
```

Response:
```json
{
  "items": [
    {
      "skill_id": "analysis-fundamental",
      "revision_hash": "abc123...",
      "family": "analysis",
      "risk_class": "read_only",
      "lifecycle": "active",
      "dependencies": [...],
      "frozen_at": "..."
    }
  ],
  "total": 5,
  "page": 1,
  "page_size": 20
}
```

### FR-3: Tool Invocation API

```
GET /api/admin/agos/tools?run_id=...&status=...&page=1&page_size=20
```

Response:
```json
{
  "items": [
    {
      "invocation_id": "...",
      "tool_id": "coingecko-price-fetch",
      "side_effect_class": "read_only",
      "evidence_class": "candidate_evidence",
      "status": "success",
      "input_hash": "...",
      "output_hash": "...",
      "started_at": "...",
      "completed_at": "..."
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 20
}
```

### FR-4: Context Manifest API

```
GET /api/admin/agos/context?run_id=...
```

Response:
```json
{
  "manifest_id": "...",
  "run_id": "...",
  "content_hash": "...",
  "token_budget": 4096,
  "token_used": 3200,
  "included_count": 15,
  "excluded_count": 3,
  "exclusion_reasons": {"stale": 1, "over_budget": 2},
  "included_refs": {...},
  "excluded_refs": [...]
}
```

### FR-5: Typed Envelopes

所有 responses 使用統一 envelope：
```json
{
  "status": "ok",
  "data": {...},
  "timestamp": "..."
}
```

Error responses：
```json
{
  "status": "error",
  "error": {"code": "NOT_FOUND", "message": "..."},
  "timestamp": "..."
}
```

### FR-6: Classification Badges

API response 中 `evidence_eligible` / `evidence_class` 明確區分：
- `context_only` — 用於 context display，不作為 Evidence
- `candidate_evidence` — 可能成為 Evidence（pending validation）
- `trusted_evidence` — 已驗證的 Evidence

### FR-7: Sensitive Content Redaction

Memory content 預設 redacted（`content_preview: "[REDACTED]"`）。
加 `?show_content=true` 參數且通過額外 authorization 才顯示。

### FR-8: Authorization Gate

所有 `/api/admin/agos/*` endpoints 需要 Admin authorization。
MVP：環境變數 `TRUSTFORGE_ADMIN_TOKEN` 作為 Bearer token。

## 非功能需求

- **NFR-1: Read-only** — 所有 endpoints 為 GET，無 mutation
- **NFR-2: Pagination** — stable cursor or offset-based
- **NFR-3: 零第三方依賴** — 使用既有 web.py HTTP server

## 驗收條件

1. APIs 提供 pagination 和 stable typed envelopes
2. Context-only, candidate evidence, trusted evidence 明確區分
3. Lineage 和 exclusion reasons 可見
4. Sensitive content 預設 redacted
5. Endpoints authorization-gated 且 read-only
6. API contract 和 integration tests 通過
7. 完整 pre-push 通過
