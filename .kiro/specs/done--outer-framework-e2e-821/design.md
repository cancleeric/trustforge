# Outer Framework E2E 驗證設計

## 驗證流程

```
模擬營運資料（scheduler_runs + analysis_history）
  ↓
diagnose() → ImprovementProposal[]
  ↓
UpgradeQueue.sync_diagnostic(report) → proposals 入庫
  ↓
record_reviews({reviews: [{verdict: "sandbox_ready"}]}) → llm_reviewed
  ↓
SandboxAttestationAuthority.issue() → SandboxAttestation
  ↓
record_sandbox(attestation) → sandbox_passed
  ↓
decide(proposal_id, "approve", reason, principal=admin) → approved
  ↓
activate(proposal_id, reason, principal=admin) → activated
  ↓
驗證 skill_changes.jsonl 有新的 "approved" 紀錄
驗證 activation receipt journal 有 crash-safe 紀錄
```

## 必要配置

```python
UpgradeQueue(
    path=db_path,                                          # 獨立 SQLite
    authority=LocalAuthority(),                            # 認證 adapter
    sandbox_verifier=SandboxAttestationAuthority(path=…),  # capability journal
    activation_handler=HermesActivationHandler(log_path=…),# activation journal
    catalog=LocalCatalog(),                                # artifact resolver
)
```

## 候選 Artifact 準備

```python
# 基於現有 baseline 微調
baseline = load skills/hermes/source/*.json
candidate = baseline + new rule
write_artifact(candidate) → skills/hermes/source/{hash}.json
```
