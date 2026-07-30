#!/usr/bin/env python3
"""Validate TrustForge QA registry and release-bound acceptance evidence."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import mimetypes
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMA_VERSION = "1.0"
FORBIDDEN_KEY = re.compile(
    r"(authorization|cookie|password|passwd|secret|token|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


class AcceptanceValidationError(ValueError):
    """Raised when acceptance evidence is incomplete or unsafe."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def expanded_case_ids(
    registry: dict[str, Any],
    repo_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Expand requirement matrices to deterministic case IDs."""
    if registry.get("schema_version") != SCHEMA_VERSION:
        raise AcceptanceValidationError("unsupported registry schema_version")
    dimensions = registry.get("dimensions")
    requirements = registry.get("requirements")
    if not isinstance(dimensions, dict) or not isinstance(requirements, list):
        raise AcceptanceValidationError("registry requires dimensions and requirements")

    result: dict[str, dict[str, Any]] = {}
    requirement_ids: set[str] = set()
    for requirement in requirements:
        requirement_id = requirement.get("id")
        if not isinstance(requirement_id, str) or requirement_id in requirement_ids:
            raise AcceptanceValidationError(f"duplicate or invalid requirement id: {requirement_id!r}")
        requirement_ids.add(requirement_id)
        if requirement.get("hard") and not requirement.get("automation"):
            raise AcceptanceValidationError(f"hard requirement lacks automation: {requirement_id}")
        status = requirement.get("implementation_status", "implemented")
        if status not in {"implemented", "planned"}:
            raise AcceptanceValidationError(
                f"invalid implementation_status for {requirement_id}: {status}"
            )
        if repo_root is not None and status == "implemented":
            missing_automation = [
                path for path in requirement.get("automation", [])
                if not (repo_root / path).is_file()
            ]
            if missing_automation:
                raise AcceptanceValidationError(
                    f"implemented automation missing for {requirement_id}: "
                    f"{', '.join(missing_automation)}"
                )

        matrix = requirement.get("matrix", [])
        unknown = [name for name in matrix if name not in dimensions]
        if unknown:
            raise AcceptanceValidationError(
                f"{requirement_id} references unknown dimensions: {', '.join(unknown)}"
            )
        values = [dimensions[name] for name in matrix]
        combinations = itertools.product(*values) if values else [()]
        for combination in combinations:
            suffix = ",".join(f"{name}={value}" for name, value in zip(matrix, combination))
            case_id = f"{requirement_id}[{suffix}]" if suffix else requirement_id
            if case_id in result:
                raise AcceptanceValidationError(f"duplicate expanded case id: {case_id}")
            result[case_id] = requirement
    return result


def _schema_validator(schemas_dir: Path, name: str) -> Draft202012Validator:
    schema_path = schemas_dir / name
    schema = load_json(schema_path)
    resources: list[tuple[str, Resource[Any]]] = []
    for candidate in schemas_dir.glob("*.schema.json"):
        document = load_json(candidate)
        resource = Resource.from_contents(document)
        resources.append((candidate.resolve().as_uri(), resource))
        if "$id" in document:
            resources.append((document["$id"], resource))
    return Draft202012Validator(
        schema,
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def _reject_secret_fields(value: Any, trail: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if FORBIDDEN_KEY.search(str(key)):
                raise AcceptanceValidationError(f"secret-bearing field prohibited: {trail}.{key}")
            _reject_secret_fields(child, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{trail}[{index}]")


def _validate_artifact_path(path: str, release_id: str) -> None:
    pure = PurePosixPath(path)
    expected = PurePosixPath("out") / "acceptance" / release_id
    if pure.is_absolute() or ".." in pure.parts or pure == expected or expected not in pure.parents:
        raise AcceptanceValidationError(
            f"artifact path must be below out/acceptance/{release_id}/: {path}"
        )


def _verify_artifact(
    artifact: dict[str, Any],
    release_id: str,
    artifact_root: Path,
) -> None:
    _validate_artifact_path(artifact["path"], release_id)
    path = artifact_root / artifact["path"]
    if not path.is_file():
        raise AcceptanceValidationError(f"artifact does not exist: {artifact['path']}")
    payload = path.read_bytes()
    if len(payload) != artifact["size_bytes"]:
        raise AcceptanceValidationError(f"artifact size mismatch: {artifact['path']}")
    if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        raise AcceptanceValidationError(f"artifact sha256 mismatch: {artifact['path']}")
    guessed_media_type, _ = mimetypes.guess_type(path.name)
    if guessed_media_type and guessed_media_type != artifact["media_type"]:
        raise AcceptanceValidationError(f"artifact media_type mismatch: {artifact['path']}")
    magic = artifact.get("magic")
    if magic and not payload.startswith(magic.encode("utf-8")):
        raise AcceptanceValidationError(f"artifact magic mismatch: {artifact['path']}")


def validate_acceptance(
    registry: dict[str, Any],
    summary: dict[str, Any],
    schemas_dir: Path,
    artifact_root: Path | None = None,
) -> None:
    repo_root = schemas_dir.resolve().parents[1]
    expected_cases = expanded_case_ids(registry, repo_root)
    artifact_root = artifact_root or repo_root
    errors = sorted(
        _schema_validator(schemas_dir, "acceptance-summary.schema.json").iter_errors(summary),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path) or "$"
        raise AcceptanceValidationError(f"schema error at {location}: {first.message}")

    _reject_secret_fields(summary)
    release_id = summary["release_id"]
    manifest = summary["manifest"]
    if manifest["release_id"] != release_id:
        raise AcceptanceValidationError("manifest release_id does not match summary")

    manifest_paths: set[str] = set()
    for artifact in manifest["artifacts"]:
        path = artifact["path"]
        _verify_artifact(artifact, release_id, artifact_root)
        if path in manifest_paths:
            raise AcceptanceValidationError(f"duplicate artifact path: {path}")
        manifest_paths.add(path)

    observed: dict[str, dict[str, Any]] = {}
    for case in summary["cases"]:
        case_id = case["case_id"]
        if case_id in observed:
            raise AcceptanceValidationError(f"duplicate result case_id: {case_id}")
        if case_id not in expected_cases:
            raise AcceptanceValidationError(f"unknown result case_id: {case_id}")
        requirement = expected_cases[case_id]
        if case["requirement_id"] != requirement["id"]:
            raise AcceptanceValidationError(f"requirement mismatch for {case_id}")
        if case["hard"] != bool(requirement["hard"]):
            raise AcceptanceValidationError(f"hard flag mismatch for {case_id}")
        if case["release_id"] != release_id:
            raise AcceptanceValidationError(f"release mismatch for {case_id}")
        for evidence in case["evidence"]:
            _validate_artifact_path(evidence["path"], release_id)
            if evidence["path"] not in manifest_paths:
                raise AcceptanceValidationError(
                    f"case evidence absent from manifest: {evidence['path']}"
                )
        observed[case_id] = case

    missing = sorted(set(expected_cases) - set(observed))
    if missing:
        raise AcceptanceValidationError(
            f"missing required cases ({len(missing)}): {', '.join(missing[:5])}"
        )

    deployment_pass = all(
        case["status"] == "pass"
        for case_id, case in observed.items()
        if expected_cases[case_id]["gate"] == "deployment"
    )
    all_automated = all(
        requirement.get("implementation_status", "implemented") == "implemented"
        for requirement in expected_cases.values()
    )
    all_pass = all_automated and all(
        case["status"] == "pass" for case in observed.values()
    )
    expected_disposition = (
        "production_accepted"
        if all_pass
        else "deployed_not_accepted"
        if deployment_pass
        else "deployment_failed"
    )
    if summary["disposition"] != expected_disposition:
        raise AcceptanceValidationError(
            f"disposition must be {expected_disposition}, got {summary['disposition']}"
        )


def is_production_accepted(summary: dict[str, Any]) -> bool:
    return summary.get("disposition") == "production_accepted"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("qa/requirements.json"))
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--schemas", type=Path, default=Path("qa/schemas"))
    args = parser.parse_args()
    try:
        validate_acceptance(load_json(args.registry), load_json(args.summary), args.schemas)
    except (AcceptanceValidationError, json.JSONDecodeError) as exc:
        print(f"QA acceptance validation failed: {exc}", file=sys.stderr)
        return 1
    if not is_production_accepted(load_json(args.summary)):
        print(
            f"QA acceptance evidence is valid but not accepted: {args.summary}",
            file=sys.stderr,
        )
        return 2
    print(f"QA acceptance validation passed: {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
