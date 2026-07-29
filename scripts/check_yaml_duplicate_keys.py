#!/usr/bin/env python3
"""Reject duplicate mapping keys in repository YAML files at every depth."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml


class DuplicateKeyError(ValueError):
    """A YAML mapping repeats a key that a normal loader would silently replace."""


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict:
    loader.flatten_mapping(node)
    mapping = {}
    first_marks: dict[object, yaml.error.Mark] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            first = first_marks[key]
            raise DuplicateKeyError(
                f"{key_node.start_mark.name}:{key_node.start_mark.line + 1}:"
                f"{key_node.start_mark.column + 1}: duplicate key {key!r}; "
                f"first defined at line {first.line + 1}, column {first.column + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
        first_marks[key] = key_node.start_mark
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_unique_yaml(path: Path) -> object:
    with path.open(encoding="utf-8") as stream:
        return yaml.load(stream, Loader=UniqueKeyLoader)


def yaml_files(root: Path) -> list[Path]:
    excluded = {".git", ".venv", "node_modules", "out"}
    return sorted(
        path
        for suffix in ("*.yaml", "*.yml")
        for path in root.rglob(suffix)
        if not excluded.intersection(path.relative_to(root).parts)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    paths = args.paths or yaml_files(root)
    failures = []
    for path in paths:
        candidate = path if path.is_absolute() else root / path
        try:
            load_unique_yaml(candidate)
        except (OSError, yaml.YAMLError, DuplicateKeyError) as exc:
            failures.append(str(exc))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"YAML duplicate-key gate passed ({len(paths)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
