# Design

> See requirements.md for context.

The telemetry system tracks module state transitions with runtime evidence. Each module progresses through: registered → configured → resolved → invoked → verified. State is recorded at actual invocation points in `scoring.py` and `orchestrator.py`, exposed via `/api/module-telemetry`.
