# Production Interaction Canary

> Last verified: 2026-07-14, release `v0.14.6`
>
> Historical evidence only. GitHub Actions are now intentionally disabled;
> current deployments follow the controlled local release/deploy runbook.

## Release And Health

- Historical GitHub production run `29315134262` completed successfully.
- EC2 health returned `status=ok`, `version=v0.14.6`.
- `trustforge.service` was active after deployment.
- The active frontend bundle contains `execution-log.json` and no longer exposes
  `execution-log.jsonl` as the user download name.

## Concurrent Analyze Canary

Two identical requests were started concurrently against the EC2 service over
localhost, using an isolated canary query. Both returned HTTP 200 in about 3.6
seconds. A second run-id canary returned the same execution id to both callers:

```text
hermes-ec4c16d8f648
hermes-ec4c16d8f648
```

This verifies the service-level coalescing path: concurrent identical requests
receive one shared Hermes result instead of running duplicate analysis. The
production service was configured with the DynamoDB lease backend and the lease
table/IAM/TTL checks had already passed independently.

## Remaining Visual Evidence

The local release smoke and desktop/mobile viewport checks are complete. The
2026-07-14 production browser-control session could not initialize because of a
desktop browser runtime conflict, so production desktop/mobile screenshots are
still pending. This is recorded as an evidence gap, not a product failure.
