#!/usr/bin/env bash
# Stage a planning session at step 7: the branch's own plan skill, the spec, and the
# migration bar the branch's own renderer produces for it. The plugin root is three
# directories above this file (fixtures -> <case> -> evals -> plugin); the repo root is seven.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin="$(cd "$here/../../.." && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
python="$repo/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
cp "$plugin/skills/plan/SKILL.md" plan.md
cp "$here/spec.md" spec.md
"$python" "$plugin/scripts/migration_bar.py" --target-repo trailhead < spec.md > migration-bar.txt
