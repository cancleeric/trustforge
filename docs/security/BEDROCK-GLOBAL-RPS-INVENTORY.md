# Bedrock global request inventory (#1347)

All production model requests share `trustforge.bedrock._DEFAULT_RPS_LIMITER`.
Production EC2 and competition Lambda select the same DynamoDB owner lock and
the same `TRUSTFORGE_BUDGET_COUNTER_TABLE`; local development alone may use the
host-local flock fallback. The enforced minimum is 1.0 seconds and cannot be
reduced by environment configuration.

| Call surface | Operation | Shared boundary |
|---|---|---|
| `BedrockClient.complete` | Converse | `_bedrock_slot()` |
| stance strict/fallback | Converse | `_bedrock_slot()` |
| structured record/claim extraction | Converse | `_bedrock_slot()` |
| Hermes planner | Converse | `bedrock_invoke_slot()` |
| Hermes exact tokenizer | CountTokens | `bedrock_invoke_slot()` |
| Bedrock smoke | Converse | `bedrock_invoke_slot()` |
| review and cache scripts | Via `BedrockClient` | `_bedrock_slot()` |

`scripts/verify_bedrock.py` only lists models after constructing the runtime
client; it does not invoke a model. The legacy `app/CustomerSupport` Strands
demo is fail-closed because Strands would otherwise create an unaudited client.

The architecture test scans `src/`, `scripts/`, and `app/` and fails whenever a
new model operation appears outside this inventory or a direct
`boto3.client("bedrock-runtime")` is added outside the sole audited factory.

Audit logs identify the gate/backend and request start without prompt, response,
credentials, tokens, or secrets. A backend error, damaged local state,
contention timeout, stale lease, or owner-conditional release failure never
falls through to an unpaced request.
