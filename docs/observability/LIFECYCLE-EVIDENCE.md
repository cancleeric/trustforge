# Lifecycle Evidence (#412)

## Provider Lifecycle
| Event | Evidence | Location |
|-------|----------|----------|
| Builtin LLM invoke | Execution log | agent/orchestrator.py |
| AgentCore LLM invoke | Backend registry routing | agent/agentcore_adapter.py |
| SageMaker training submit | Job ARN + status | sagemaker_submit.py |
| Cache read/write | SQLite/DynamoDB metrics | ingestion/cache.py |

## Kernel Lifecycle
| Event | Evidence | Location |
|-------|----------|----------|
| Trust scoring | Score + confidence | trustforge_core/scoring.py |
| Corroboration | Source agreement rate | trustforge_core/corroboration.py |
| Pure scoring | Deterministic output | trustforge_core/pure_scoring.py |

## Policy Lifecycle
| Event | Evidence | Location |
|-------|----------|----------|
| Budget guard | Daily cap enforcement | budget_guard.py |
| CSP mode switch | TRUSTFORGE_CSP_MODE | web.py |
| Outer Skill executor | Policy gate approval | outer_skill_policy.py |

## Upgrade Lifecycle
| Event | Evidence | Location |
|-------|----------|----------|
| EcoLink impact path | Multi-hop BFS | impact_path_evaluator.py |
| Backfill progress | SQLite state DB | backfill.py |
| Calibration retrain | Model artifact SHA | retrain_calibrator.py |
