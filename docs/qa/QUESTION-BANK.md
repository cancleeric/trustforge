# Competition Question Bank

`python3 scripts/run_question_bank.py --all` runs 240 deterministic, original
prompts: 60 multi-source analysis prompts, 60 hypothesis-verification prompts,
and 120 pair-comparison prompts. The three classes match the official examples,
but the variants deliberately test broader live-draw conditions:

- government/regulatory announcement evidence and its provenance;
- crawler/cache freshness, failure and per-source execution time;
- five-year OHLCV lineage versus the short analysis window;
- source conflict, manipulation risk, missing evidence, abstention and reversal
  conditions; and
- all required report, Evidence and Execution Log fields.

The runner defaults to a 24-case offline smoke run. `--all` runs the full bank;
`--online --all` is intentionally explicit because it uses live/cache/provider
configuration. It emits JSON with case pass/fail, p50/p95 end-to-end latency,
and p50/p95 per-source latency, so slow or incomplete connectors are visible.

## Quality gate

The mandatory pre-push hook runs the deterministic 24-case offline subset and
writes `out/pre-push/question-bank-results.json`. The gate requires every case
to have the required report fields, Evidence contract, a source-level execution
event, and the source event contract (`source`, `kind`, `duration_ms`,
`document_count`, `outcome`). It deliberately does not run live crawlers or
Bedrock: online runs are a separately scheduled, credentialed measurement and
must not be conflated with fixture latency.

The prompts are authored by TrustForge, not copied from external question sets.
Public financial QA datasets and exchange documentation are suitable only as
coverage inspiration; competition validation must remain reproducible and
within the official scope.

## External evaluation references

- [FinQA](https://finqasite.github.io/) demonstrates numerical reasoning with
  supporting facts and reasoning programs over financial reports. We borrow the
  test dimensions (calculation, evidence linkage and explainability), not its
  questions or answers.
- [TAT-QA](https://github.com/NExTplusplus/TAT-QA) combines tables and text in
  financial reports. It informs our source-conflict and cross-format coverage,
  while its CC BY terms still require attribution if data is ever reused.
- [OpenAI Evals build guidance](https://github.com/openai/evals/blob/main/docs/build-eval.md)
  reinforces the local JSONL-style, per-sample evaluation pattern. The runner
  keeps a deterministic case ID and per-case result for that reason.
