# Spec：升級模組真實 runtime telemetry (#382)

> Issue: #382
> Size: M

---

## Requirements

### R1: Telemetry 記錄
- 每個模組記錄：last_invoked_at, invocation_count, last_result, avg_latency_ms
- SQLite 持久化

### R2: 記錄點
- scoring.py score()
- orchestrator.py build_report()

### R3: API
- GET /api/module-telemetry

---

## Design

- `src/trustforge/module_telemetry.py`
- Background writer thread（非阻塞）
- Singleton + thread-safe

---

## Tasks
- [x] module_telemetry.py
- [x] Instrument score() + build_report()
- [x] API endpoint
- [x] Tests (8)
- [x] PR #388
