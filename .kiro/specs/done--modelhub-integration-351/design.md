# Design

> See requirements.md for the full architecture and failure semantics.

The integration follows a strict human-review-only pattern:

1. **Flat loader** validates `data/training/{COIN}.jsonl` files
2. **Gate** checks minimum 100 unique labelled outcomes
3. **Chronological split** preserves holdout integrity
4. **REST client** (`modelhub_client.py`) communicates with ModelHub via loopback-only HTTP
5. **Orchestrator** triggers retrain, polls for result, validates artifact digest
6. **ECE comparison** determines if candidate improves over baseline by >= 0.02
7. **Durable writes** (proposal + execution log) land before any current manifest update
8. **Human approval** is always required before activation
