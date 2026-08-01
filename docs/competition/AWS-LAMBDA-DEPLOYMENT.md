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

## Token rotation contract

The Lambda process pins the optional Secrets Manager `VersionId` supplied in
`TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID` and caches it only for that published
Lambda execution environment. Rotation must never update a secret in place
while leaving warm environments active:

1. create a new secret version;
2. update the Lambda configuration with its explicit VersionId;
3. publish a new Lambda version and move the deployment alias;
4. verify no old version or alias has a Function URL or resource-policy invoke
   entry, then verify the old token is rejected through the deployment alias;
5. if immediate execution-environment recycling is required, briefly set the
   whole function's reserved concurrency to zero, restore it to the reviewed
   value, and repeat the old-token rejection check (Lambda does not support
   alias- or version-level reserved concurrency);
6. retain no plaintext token in environment variables, logs, URLs, artifacts,
   PRs, or repository files.

If any step fails, keep live activation disabled. The offline deployment does
not configure a secret ARN or a token and therefore does not perform secret
retrieval.
