#!/usr/bin/env python3
"""Ask the configured Bedrock LLM to adversarially review upgrade candidates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.bedrock import BedrockClient, BedrockConfig  # noqa: E402
from trustforge.upgrade_review import review  # noqa: E402


def main() -> int:
    source = REPO / "out" / "hermes-improvement-latest.json"
    target = REPO / "out" / "hermes-upgrade-review-latest.json"
    diagnostic = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else {"proposals": []}
    config = BedrockConfig()
    if not config.model_id:
        result = {"status": "waiting_model_configuration", "reviews": [], "can_activate": False}
    else:
        client = BedrockClient(config=config, offline=False)
        result = review(diagnostic, lambda system, prompt: client.complete(system=system, prompt=prompt).text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
