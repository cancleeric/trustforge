# Spec：升級模組 runtime telemetry (#382) — v2

> Issue: #382 (re-opened)
> 前版問題：module_status.py 以空輸入和預設值回傳 ready/registered

---

## Requirements

### R1: 狀態模型
registered → configured → resolved → invoked → verified
（另允許：disabled / blocked / degraded / failed / stale）

### R2: 每個狀態有 runtime evidence
- invoked_at（實際呼叫時間）
- evidence_ref（呼叫的程式碼位置）
- revision（版本）
- reason（為何在此狀態）

### R3: 31 個外框模組
所有模組的狀態來自真實 runtime，不是預設值。

---

## Tasks
- [x] 定義狀態 enum + evidence schema
- [x] 在 scoring.py score() 記錄 invoked
- [x] 在 orchestrator build_report() 記錄 invoked
- [x] /api/module-telemetry 回傳真實狀態（不是預設）
- [x] 測試：invoked 狀態可追溯到真實呼叫
