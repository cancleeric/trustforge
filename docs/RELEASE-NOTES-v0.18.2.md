# TrustForge v0.18.2 Release Notes

> 日期：2026-07-25
> Tag: v0.18.2
> 前版: v0.18.1

## 主要變更

### 三軌接線完成（#570, PR #664）

三軌 learning event 正式接入 production analysis pipeline：
- 新 module `three_track_wiring.py`：feature flag gate + emit_for_completed_job + emit_for_failed_job
- `analysis_flow._worker()` 兩個 completion point（SUCCESS + FAILURE）加 hook
- feature flag `TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED` 預設 **OFF**
- fail-soft 三層：durable state 後 + flag gate + broad catch（學習事件失敗不破壞主分析）
- **開 flag 就啟用**：flag ON → 每個分析產生 immutable analysis-quality.v1 learning event

### CISO 安全雙審

- **#510 wrapper activation**：兩輪（H1 自我核准繞過修復後 PASS）
- **#570 接線**：七項風險全過（R2 error message 不需 redact——同信任邊界）
- 34 條 CISO 負向安全測試 + 23 條接線測試

### ModelHub 唯讀端點需求（轉交 Anderson）

`docs/handoff/2026-07-25-modelhub-readonly-endpoint-requirements.md`：
- P0：Read Access 隔離（cross-tenant 403/404）+ Artifact Checksum（SHA-256）
- P1：Provenance + Identity

### 持久化 live 服務

- web:8799 launchd KeepAlive（live Bedrock，`scripts/start-local-live.sh` wrapper）
- frontend:4174 launchd KeepAlive
- `~/.trustforge-live.env`：AWS_PROFILE + Bedrock model + token（umask 600）

## 測試

- 4192 backend tests passed / 7 skipped / 0 failed
- 346 frontend tests passed
- 86%+ coverage
- pre-push gate 8/8 全綠

## 三軌最終狀態

| 層面 | 狀態 |
|------|------|
| 程式碼 | ✅ 全部完成（12 issue + #570 接線） |
| 接線 | ✅ production pipeline hook 完成 |
| CISO 雙審 | ✅ PASS |
| feature flag | OFF（開了就啟用） |
| #503 ModelHub | ⚠️ 等 Anderson 補 4 個唯讀端點 |

## 版控

- main: v0.18.2 (2408c4f)
- develop: synced
- Open PR: 0
- Tags: v0.17.2 → v0.18.0 → v0.18.1 → v0.18.2
