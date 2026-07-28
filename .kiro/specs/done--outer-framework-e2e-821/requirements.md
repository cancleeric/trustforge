# Outer Framework 升級循環端到端驗證紀錄

> Issue: #821
> 依據: docs/reports/OUTER-FRAMEWORK-UPGRADE-GOVERNANCE-2026-07-27.md

## 需求

記錄並證實 Outer Framework Upgrade Governance 完整循環已可在本機端到端運作。

## 驗證結果

2026-07-28 本機驗證通過：

```
[1] Propose: source-reliability-investigation ✓
[2] Review: llm_reviewed ✓
[3] Sandbox: passed ✓
[4] Approve: done ✓
[5] Activate: done ✓
    result keys: [activation_id, proposal_id, state, family, revision, previous_revision]
```

## 涉及模組

- `src/trustforge/improvement.py` — diagnose() 產出 proposals
- `src/trustforge/upgrade_queue.py` — durable queue（sync_diagnostic / record_reviews / record_sandbox / decide / activate）
- `src/trustforge/upgrade_state_machine.py` — 狀態轉換（proposed → llm_reviewed → sandbox_passed → approved → activated）
- `src/trustforge/upgrade_adapters.py` — SandboxAttestationAuthority + HermesActivationHandler
- `src/trustforge/upgrade_ports.py` — AuthenticatedPrincipal + Protocol 定義
- `src/trustforge/skills.py` — skill artifact 管理
- `src/trustforge/skill_changes.py` — append-only change log

## 關鍵安全機制驗證

| 機制 | 驗證結果 |
|---|---|
| SandboxAttestation OS-protected journal | ✅ 正常簽發、驗證 |
| AuthenticatedPrincipal 認證 | ✅ 缺認證會 PermissionError |
| artifact hash 校驗 | ✅ 候選 revision 必須在 skills/ 存在 |
| tenant_id 綁定 | ✅ principal 必須 match proposal |
| idempotency（重複 activate 擋） | ✅ 已啟用的 proposal 不可重複 |
| `approval_required = true, automatic_apply = false` | ✅ 每步都需要明確呼叫 |

## 之前未跑過的根因

不是程式碼缺失，是：
1. 本機無 production analysis jobs → diagnose() 產不出有意義 proposals
2. 需要手動模擬營運資料才能觸發後續階段
