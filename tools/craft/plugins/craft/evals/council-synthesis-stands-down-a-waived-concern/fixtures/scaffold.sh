#!/usr/bin/env bash
# Stage a council review: the branch's own council.md, the spec, and the calibration block
# the branch's own renderer produces for it. The plugin root is three directories above this
# file (fixtures -> <case> -> evals -> plugin); the repo root is seven.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin="$(cd "$here/../../.." && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
python="$repo/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
cp "$plugin/skills/_shared/council.md" council.md
cp "$here/spec.md" spec.md
[ -f "$here/responses.md" ] && cp "$here/responses.md" responses.md
"$python" "$plugin/scripts/maturity_bars.py" < spec.md > calibration.md
