#!/usr/bin/env bash
# Scaffold for a runner eval case: seed a throwaway lore vault in the run's cwd.
#
# Copy this file unchanged to <case>/fixtures/scaffold.sh and put the case's
# records beside it as <case>/fixtures/records.json. The repo root is seven
# directory levels above this file's directory (fixtures -> <case> -> evals ->
# plugins/lore -> plugins -> tools/lore -> tools -> repo), so the copy only works
# at that depth.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
python="$repo/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
exec "$python" "$repo/tools/lore/scripts/seed-eval-vault" "$here/records.json"
