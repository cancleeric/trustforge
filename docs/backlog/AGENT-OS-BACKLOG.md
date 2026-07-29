# Agent OS Backlog Index

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Plan: Hermes Agent OS Memory / Skill 能力主線
> Created: 2026-07-29

## Overview

Agent OS 為 TrustForge Hermes 建立可治理的 Memory、Task Skill、Tool Capability
與 Context Manifest 基礎層。所有子項遵循：

- Historical context 不得作為 Evidence 或 Trust scoring input
- 高風險 skill、write_external、activation、deployment 一律 fail-closed + 人工 approval
- MVP 採 repo-local registry、單一 SQLite deployment、Admin-only UI
- 不修改 Trust Kernel、Trust weights、Evidence scoring、production deployment

## Backlog Entries

### H-33: Memory OS Schema & Repository

| Field | Value |
|-------|-------|
| Issue | [#916](https://github.com/cancleeric/trustforge/issues/916) |
| Status | Implemented / in review |
| Priority | P0 |
| Depends on | #915 (this doc) |
| Contract | [MEMORY-OS-CONTRACT](../contracts/MEMORY-OS-CONTRACT.md) |
| Safety | ⛔ DB schema change — requires Eric's same-day authorization token |
| Deliverable | `src/trustforge/memory_os.py` + `tests/test_memory_os.py` |

---

### H-34: Task Skill Registry

| Field | Value |
|-------|-------|
| Issue | [#917](https://github.com/cancleeric/trustforge/issues/917) |
| Status | Implemented / in review |
| Priority | P0 |
| Depends on | #915 (this doc) |
| Contract | [TASK-SKILL-CONTRACT](../contracts/TASK-SKILL-CONTRACT.md) |
| Safety | ⛔ DB schema change — requires Eric's same-day authorization token |
| Deliverable | `src/trustforge/skill_registry.py` + `tests/test_skill_registry.py` |

---

### H-35: Tool Capability Registry

| Field | Value |
|-------|-------|
| Issue | [#918](https://github.com/cancleeric/trustforge/issues/918) |
| Status | Implemented / in review |
| Priority | P0 |
| Depends on | #915 (this doc) |
| Contract | [TOOL-CAPABILITY-CONTRACT](../contracts/TOOL-CAPABILITY-CONTRACT.md) |
| Safety | ⛔ DB schema change — requires Eric's same-day authorization token |
| Deliverable | `src/trustforge/tool_registry.py` + `tests/test_tool_registry.py` |

---

### H-36: Memory Retrieval / RAG Adapter

| Field | Value |
|-------|-------|
| Issue | [#919](https://github.com/cancleeric/trustforge/issues/919) |
| Status | Implemented / in review |
| Priority | P0 |
| Depends on | #916 |
| Contract | [MEMORY-OS-CONTRACT](../contracts/MEMORY-OS-CONTRACT.md) §5 Retrieval Lineage |
| Safety | No DB schema change. Historical conclusions must never enter scoring. |
| Deliverable | `src/trustforge/memory_retrieval.py` + `tests/test_memory_retrieval.py` |

---

### H-37: Skill Loader / Governance

| Field | Value |
|-------|-------|
| Issue | [#920](https://github.com/cancleeric/trustforge/issues/920) |
| Status | Implemented / in review |
| Priority | P0 |
| Depends on | #917 |
| Contract | [TASK-SKILL-CONTRACT](../contracts/TASK-SKILL-CONTRACT.md) §7 Frozen Manifest |
| Safety | High-risk skills require proposal → sandbox → approval. |
| Deliverable | `src/trustforge/skill_loader.py` + `tests/test_skill_loader.py` |

---

### H-38: Context Builder

| Field | Value |
|-------|-------|
| Issue | [#921](https://github.com/cancleeric/trustforge/issues/921) |
| Status | Implemented / in review |
| Priority | P0 |
| Depends on | #916, #917, #918, #920 |
| Contract | [CONTEXT-MANIFEST-CONTRACT](../contracts/CONTEXT-MANIFEST-CONTRACT.md) |
| Safety | Immutable after creation. Evidence-ineligible memory excluded from scoring. |
| Deliverable | `src/trustforge/context_builder.py` + `tests/test_context_builder.py` |

---

## Dependency Graph

```
#915 Architecture Contracts (THIS)
  │
  ├──→ #916 Memory OS ──→ #919 Memory Retrieval ──┐
  │                                                │
  ├──→ #917 Skill Registry ──→ #920 Skill Loader ─┤
  │                                                ├──→ #921 Context Builder
  ├──→ #918 Tool Registry ─────────────────────────┘         │
  │                                                          ↓
  │                                                    #922 Runtime Integration
  │                                                          │
  │                                                    #923 Admin API
  │                                                          │
  │                                                    #924 Admin UI
  │                                                          │
  └───────────────────────────────────────────────────→ #925 E2E / Release Gate
```

## Non-Implementation Items (Epic-Level)

| ID | Item | Status |
|----|------|--------|
| H-39 | Runtime Integration | Implemented / final review — [#922](https://github.com/cancleeric/trustforge/issues/922); regression and lineage gates pass |
| H-40 | Admin Summary API | Implemented / in review — [#923](https://github.com/cancleeric/trustforge/issues/923); real-handler auth success is covered |
| H-41 | Admin UI Rails | Implemented / in review — [#924](https://github.com/cancleeric/trustforge/issues/924); desktop/mobile Eye review remains open |
| H-42 | Replay/E2E/Release | Automated scope complete / human gates pending — [#925](https://github.com/cancleeric/trustforge/issues/925); replay, regression and lineage pass，仍待 CISO/CPO、`/codex-review` 與 Eye |

Statuses are implementation states, not release approval. No item above is `Done`
until all mandatory #925 gates and human dispositions are complete.
