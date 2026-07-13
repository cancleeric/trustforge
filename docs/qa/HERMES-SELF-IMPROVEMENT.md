# Hermes Self-Improvement Loop

## Contract

Hermes is allowed to improve its **operational knowledge** automatically. It is
not allowed to self-deploy code, alter Trust weights, alter a model, or rewrite
a completed formal report. The loop is:

```text
observe durable measurements
  -> diagnose a concrete deficit
  -> propose a sandbox experiment
  -> regression + replay validation
  -> human approval
  -> versioned release
```

This separation makes autonomy real while keeping the Trust Layer deterministic
and auditable.

## Inputs and outputs

`python3 scripts/diagnose_hermes.py` reads the most recent scheduler records and
can accept a question-bank result plus a historical replay report:

```bash
python3 scripts/diagnose_hermes.py \
  --question-bank out/question-bank-latest.json \
  --replay out/replay-btc.json \
  --out out/hermes-improvement-latest.json
```

Every proposal records its observed evidence, proposed experiment, target
metric, `approval_required=true`, and `automatic_apply=false`.

## Research-to-product mapping

- **Truth discovery / CRH**: use repeated cross-source conflicts and source
  health to choose the next evidence-quality experiment, not to blindly reward
  a source. The architecture review is based on the CRH formulation of jointly
  resolving conflicts and estimating source reliability.
- **Dawid-Skene EM**: once enough time-bucketed direction votes exist, evaluate
  an offline deterministic consensus fallback for missing LLM entailment. It is
  a research candidate, not an automatic production switch.
- **Historical calibration**: only after leakage-safe outcomes accumulate,
  compare a small explainable calibrator on time-separated holdout data. A
  current information-completeness score is never relabelled as probability.

The detailed local decision record is
`docs/architecture/TRUTH-DISCOVERY-EVALUATION-2026-07-13.md`; it explicitly
rejects a stochastic LTM path for the online Trust Layer and retains a
deterministic, bounded computation contract.
