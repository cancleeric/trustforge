#!/bin/bash
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
OUTDIR="${REPO}/out"
ZIPFILE="${OUTDIR}/finale-submission.zip"

echo "=== TrustForge Finale Submission Pack ==="
mkdir -p "${OUTDIR}"

# Build list of files to include
FILES=(
    README.md
    pyproject.toml
    src/
    tests/
    frontend/src
    frontend/package.json
    frontend/tsconfig.json
    frontend/vite.config.ts
    data/
    docs/competition/
    scripts/
    .githooks/
    Dockerfile
    AGENTS.md
)

echo "Packing..."
cd "${REPO}"
zip -r "${ZIPFILE}" "${FILES[@]}" -x "*.pyc" -x "__pycache__/*" -x "*.egg-info/*" -x ".venv/*" -x "node_modules/*" -x "out/*" -x ".git/*"

SIZE=$(du -h "${ZIPFILE}" | cut -f1)
echo "✅ ${ZIPFILE} (${SIZE})"
echo "Done."
