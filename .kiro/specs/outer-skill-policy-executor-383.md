# Spec：Outer Skill runtime policy executor (#383) — v2

> Issue: #383 (re-opened)
> 前版問題：policy 只被記錄，缺少受限 runtime executor

---

## Requirements

### R1: Policy schema 家族
source / analysis / report / evaluation / improvement 各有 schema + compiler + loader + consumer

### R2: 禁止項目
- forbidden keys fail-closed
- 未知 action fail-closed
- 任意程式碼/模板注入 fail-closed

### R3: 不可影響
trust weights、PIT、evidence binding、security、cost、deploy

### R4: 契約測試
- approve/rollback/run-freeze 測試
- 未核准 revision 不影響正式 run

---

## Tasks
- [x] 每個 skill family 的 schema + compiler
- [x] 禁止項目的 SecurityError
- [x] 契約測試（5 家族 × 核心場景）
