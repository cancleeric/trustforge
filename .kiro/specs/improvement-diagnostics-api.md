# Spec：改善診斷器完整接入

> Issue: #278
> Branch: `feat/issue-278-improvement-diagnostics-api`

## 概述

將 `scripts/diagnose_hermes.py` 產出的改善診斷報告暴露到 Web API 觀測層，
讓前端可以即時查看 Hermes 的自我改善提案（proposals）狀態。

---

## 一、需求（Requirements）

### R1: GET /api/improvement-diagnostics
- 讀取 `out/hermes-improvement-latest.json`（最新一次診斷結果）
- 不需 admin token（唯讀觀測）
- 若檔案不存在 → 回傳 `{"status": "no_diagnostic_available"}`

### R2: OpenAPI spec 同步更新

---

## 二、實作任務

### Task 1: handler + route
### Task 2: OpenAPI spec
### Task 3: 驗證
