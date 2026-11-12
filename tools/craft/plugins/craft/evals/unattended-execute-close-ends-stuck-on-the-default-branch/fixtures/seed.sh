#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
python="$repo/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
exec "$python" "$repo/tools/lore/scripts/seed-eval-vault" "$here/records.json"
