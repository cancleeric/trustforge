# Signed canary cost-budget contract

Production B routing is admitted only when the HTTP allowlist, authenticated
identity, current release/ramp state, and a signed cost budget agree exactly.
The canonical request binding covers the endpoint, ordered assets, opaque query
digest, analysis type, live/sample/data/LLM modes, effective online-stance
state, opaque live-token digest, identity digest, A and B
artifact digests, ramp, control ledger, and routing-policy digest.

The router computes the request-binding digest independently. The deployment
control boundary compares that digest with the signed envelope and persists the
router value. A budget therefore cannot be replayed for another query, release,
ramp, policy, or canary epoch.

## Conservative accounting

Each admitted B request first reserves its signed per-request maximum. Terminal
events record `charged_max_model_calls` and `charged_max_cost_microusd`: these
are conservative upper bounds, not provider receipts. If no authenticated
provider receipt exists, TrustForge charges the full signed maximum. This can
overestimate actual spend but cannot silently undercount it.

The nonce identifies the authorization and may be reused for multiple
reservations. It never creates a fresh cap. All reservations sharing a ramp are
aggregated under `ramp_budget_id`, so separate queries and nonces consume the
same signed model-call and monetary ceilings.

Request count is independent from model-call and monetary accounting. Every
real-data candidate—including the default request and explicit `real=1`—is
treated as model-call-capable and requires a signed budget. This conservative
classification prevents a router/web process race from turning a free
admission into online stance after routing. An execution that remains offline
is still charged at the signed maximum because no authenticated provider
receipt exists.
Sample requests remain A-only.

`live=1` is admitted only with a valid `X-Live-Token`. The router forwards that
header to the backend but binds only a domain-separated token digest in the
authorization; raw credentials never enter outcome or control evidence.

## Restart behavior

At service startup, reservations older than the fixed five-minute window are
reconciled exactly once and charged at their signed maxima. Paid B routing is
disabled if startup reconciliation cannot authenticate or append its evidence.
Every later paid reservation repeats reconciliation while holding the shared
coordination lock, so restarts and competing processes cannot reset or
duplicate cap usage. Free A/B requests remain available when paid routing is
disabled.

Public-key provisioning and production ledger-permission migration are owned
by issue #1021; this contract does not weaken those deployment gates.
