# Live Data Licensing And Archive Readiness Checklist

This checklist gates commercial POC evidence before any live provider result is shown to a buyer.

## Evidence Classes

| Class | Description | Required Gate |
| --- | --- | --- |
| Daily OHLCV | Aggregated market time series used for context only. | Provider terms, timestamp policy, and archive coverage. |
| Raw source Evidence | Provider or public-source payload used to support a claim. | Full Evidence contract fields and content hash. |
| Derived Trust snapshot | TrustForge score, divergence, and rationale generated from Evidence. | Run lineage, scoring version, and source snapshot ID. |

## Required Fields

Every raw source Evidence item must include:

- Provider.
- Source URL.
- Terms or license reference.
- Published time.
- Retrieved time.
- Content hash.
- Raw payload reference.
- Normalization version.
- Source state.

## Source States

| State | Meaning | Buyer Display Rule |
| --- | --- | --- |
| ready | Reviewed source can be used for this POC scope. | May appear in the report with lineage. |
| credential-gated | A key, customer credential, or environment variable is missing. | Show as unavailable; do not imply coverage. |
| archive-required | Historical archive rights or payloads are missing. | Show as historical gap; do not replay current API data as past data. |
| blocked | Legal, cost, technical, or provider availability gate is unresolved. | Exclude from scoring and show blocked reason. |

## Hard Prohibition

Current API responses must never be presented as historical archives. Historical replay requires the original or licensed archived payload, its `published_at`, actual `retrieved_at`, provider, URL, terms, hash, and raw payload reference.

## Review Required Before Live Enablement

- Security review for credentials, logs, customer data, and secret-bearing payload storage.
- Cost review for paid APIs, rate limits, retries, and budget caps.
- Product review for buyer-facing claims and source-state wording.
