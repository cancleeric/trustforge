#!/usr/bin/env python3
"""Build an Ed25519-signed identity for one immutable A/B app endpoint."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from trustforge.endpoint_manifest import (
    EndpointManifestError,
    build_signed_endpoint_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        body = build_signed_endpoint_manifest(
            release_manifest_path=args.release_manifest,
            origin=args.origin,
            key_id=args.key_id,
            private_key_path=args.private_key,
        )
        output = Path(args.output)
        if not output.is_absolute() or output.exists() or output.is_symlink():
            raise EndpointManifestError("output must be a new absolute path")
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        try:
            view = memoryview(body)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, EndpointManifestError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
