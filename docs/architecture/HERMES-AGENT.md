# Hermes Agent Architecture

TrustForge is presented to the competition as **Hermes**, a multi-source market
analysis agent. Hermes is not an autonomous trading agent: it creates an
auditable decision-support report and never places orders or gives a buy/sell
instruction.

## Stable workflow

Every analysis has one `run_id` and uses the same five observable nodes:

1. **Source ingestion**: collect price, on-chain, news, social, regulatory, and
   available HOYA BIT sources.
2. **Claim extraction**: derive traceable claims from each retrieved document.
3. **Trust reasoning**: score source reputation, corroboration, freshness, and
   manipulation risk; form the judgment from the pipeline, not a third-party
   analyst conclusion.
4. **Evidence assembly**: bind report claims to official Evidence fields:
   `source`, `fetched_at`, `content_reference`, and `related_claim`.
5. **Report delivery**: produce the Final Report, Evidence List, and Execution
   Log within the 15-minute budget.

## Audit contract

Each Execution Log event keeps the stable top-level schema (`ts`, elapsed time,
tool, parameters, summary). Its `params.hermes` envelope contains `run_id`,
`agent=hermes`, node id, node label, node order, and status.
The web view visualizes these exact events and exports the same JSONL file.
This makes source acquisition, trust reasoning, and final output separately
inspectable by a reviewer.

## Deliverables per run

- `report.md`: conclusion / market judgment, key basis with Evidence links,
  confidence, known limits, and conditions that could overturn the conclusion.
- `evidence.json`: the traceable Evidence List.
- `execution_log.jsonl`: node-level execution chronology.

The CLI creates these files directly. The React analysis view exposes the same
three artifacts for the current run as browser downloads.
