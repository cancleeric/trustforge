# Hermes Execution Journey Skill

## Purpose

Turn a formal TrustForge run into a visual journey without changing the deterministic Trust Layer.

## Inputs

- A signed `run_id`, execution nodes, source provenance, durations, evidence links, and report output.
- The current visual brand tokens and a user-selected display mode.

## Required Behavior

1. Render the same ordered nodes: source intake, claim extraction, trust reasoning, evidence assembly, report delivery.
2. Each node must expose start/end time, outcome, source count, and a link to its immutable audit record.
3. Motion may explain the transition between nodes but may never imply evidence that does not exist.
4. Every visual state needs a non-animated accessible fallback and must work without generated video assets.
5. Version the journey specification with the run, test it locally, and retain screenshots/API evidence before production release.

## Reusable Lessons

- Treat transitions as contracts: a node handoff must be explicitly represented and visually continuous.
- Build scenes/configuration from data; do not hard-code a narrative that can drift from the audit log.
- Preview a low-cost prototype first; only add generated media after an approved visual and cost budget.

## Boundaries

- Do not import external scrub-engine code or generated assets into TrustForge.
- Do not let visual configuration alter time boundaries, evidence binding, scoring, or calibration.
- Keep the core Trust Layer deterministic and independently reproducible.
