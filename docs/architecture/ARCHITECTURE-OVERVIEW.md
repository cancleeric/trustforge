# TrustForge Architecture Overview

![TrustForge architecture overview](ARCHITECTURE-OVERVIEW.svg)

This diagram records the verified architecture at `v0.17.2` (`27fe4c5`). It
distinguishes the current production execution path from interfaces, control
planes, research components, and planned integrations.

## Reading the diagram

- Solid arrows are current runtime calls.
- Dashed arrows are intended, optional, or not-yet-connected routes.
- Green nodes are connected production paths.
- Blue nodes are core computation capabilities.
- Purple nodes are upgrade or governance controls.
- Amber nodes are implemented but partial or research-only.
- Red nodes are blocked, fake, unimplemented, or not connected.

The editable source is
[`ARCHITECTURE-OVERVIEW.puml`](ARCHITECTURE-OVERVIEW.puml). Regenerate both
rendered formats from the repository root with:

```bash
plantuml -charset UTF-8 -tsvg docs/architecture/ARCHITECTURE-OVERVIEW.puml
plantuml -charset UTF-8 -tpng docs/architecture/ARCHITECTURE-OVERVIEW.puml
```

## Current boundary statement

The upgrade control plane registers 31 modules, but registration is not proof
of runtime invocation. `run_kernel()` and `resolve_providers()` exist, while the
production pipeline still calls `scoring.py`, `BedrockClient`, and `collect()`
directly. Module telemetry and outer-skill policy execution are also only
partially connected. The diagram intentionally preserves those distinctions.

