#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
export HERMES_HOME="${HERMES_HOME:-$project_root/.local/hermes-home}"
export HERMES_RUNTIME_DIR="${HERMES_RUNTIME_DIR:-$project_root/.local/hermes-managed}"
runtime="$project_root/.local/hermes-runtime"
if [ ! -x "$runtime/.venv/bin/python" ]; then
  echo "Install the isolated Hermes runtime first; see docs/feasibility.md." >&2
  exit 1
fi
if ! "$runtime/.venv/bin/python" -c 'import jsonschema' 2>/dev/null; then
  echo "Install native delegation validation before starting Hermes:" >&2
  echo "uv pip install --python $runtime/.venv/bin/python -r $project_root/hermes-validation-requirements.txt" >&2
  exit 1
fi
"$runtime/.venv/bin/python" "$project_root/scripts/patch_hermes.py"
cd "$runtime"
exec "$runtime/.venv/bin/python" "$runtime/hermes" "$@"
