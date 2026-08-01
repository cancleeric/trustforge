# Competition Lambda deployment contract

The machine-readable source of truth is
`deploy/competition-lambda-contract.json`. Phase 1 is deliberately offline:
it exposes only `GET /` and `GET /healthz`, has no Bedrock, secret, database,
S3, or production-account permission, and is bounded to one concurrent Lambda
with a 30-second timeout.

Live activation is a separate security milestone. The contract records both
priced `us.*` model IDs, the daily cap, and exact Bedrock resource ARNs, but its
status remains blocked until the owner explicitly authorizes creation of the
competition token and harper approves the final IAM, counter, and endpoint
configuration.

After explicit owner authorization, the reviewed Live target is recorded in
`deploy/competition-lambda-live-contract.json`.  It uses a separate `-live`
function identity, an exact-version Secrets Manager token, an exact-model
Bedrock allowlist, and competition-only DynamoDB tables for atomic reservation
and durable cost accounting.  Live analysis accepts only `GET /analyze` and
`GET /analyze.json`, requires both `live=1` and `X-Live-Token`, and rejects all
POST and non-allowlisted routes.  `TRUSTFORGE_ONLINE_STANCE` stays unset so no
unauthenticated side path can invoke Bedrock.

## Lambda secret rotation contract

The Lambda process pins an explicit Secrets Manager `VersionId` for every
configured secret and caches plaintext only inside that published Lambda
execution environment. The Live deployment pins five ARN/VersionId pairs:

- competition Live token;
- Arkham;
- CoinMarketCap;
- Etherscan;
- Whale Alert.

Their exact environment variable names and ARNs are recorded in
`deploy/competition-lambda-live-contract.json`. Rotation must never update a
secret in place while leaving warm environments active:

1. create a new secret version;
2. update the Lambda configuration with its explicit VersionId;
3. publish a new Lambda version and move the deployment alias;
4. verify no old version or alias has a Function URL or resource-policy invoke
   entry; for the Live token verify the old token is rejected through the deployment alias, and for a
   provider run its bounded connector probe through the deployment alias while
   recording only success/schema/document-count metadata;
5. if immediate execution-environment recycling is required, briefly set the
   whole function's reserved concurrency to zero, restore it to the reviewed
   value, and repeat the old-token rejection check (Lambda does not support
   alias- or version-level reserved concurrency);
6. verify the active Lambda configuration contains all five exact ARN and
   VersionId pairs and none of the five plaintext target variables;
7. retain no plaintext token or provider key in environment configuration, logs, URLs, artifacts,
   PRs, or repository files.

If any step fails, move the `live` alias back to the prior immutable published
version and repeat health, route, budget, and credential checks. Never recover
by adding plaintext environment values. The offline deployment does not
configure secret ARNs and therefore does not perform secret retrieval.
