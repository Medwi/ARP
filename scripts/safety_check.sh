#!/usr/bin/env bash
# Run Safety with the repo policy file — same invocation as CI.
# Do not run bare `safety check -r requirements.txt` (Streamlit CVEs are
# documented ignores; Trivy gates the Docker image for HIGH/CRITICAL).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

POLICY="${ROOT}/.safety-policy.yml"
RUNTIME_REQ="$(mktemp)"
trap 'rm -f "$RUNTIME_REQ"' EXIT

if [[ ! -f "$POLICY" ]]; then
  echo "ERROR: missing $POLICY — cannot run dependency scan." >&2
  exit 1
fi

grep -Ev '^(pytest|#)' requirements.txt > "$RUNTIME_REQ"

if ! command -v safety >/dev/null 2>&1; then
  echo "Install safety: pip install safety" >&2
  exit 1
fi

echo "Safety scan (policy: .safety-policy.yml, runtime deps only)…"
safety check -r "$RUNTIME_REQ" --full-report --policy-file "$POLICY"
