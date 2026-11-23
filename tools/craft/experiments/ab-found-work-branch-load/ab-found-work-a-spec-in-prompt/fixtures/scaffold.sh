#!/usr/bin/env bash
# Seed with the unchanged lore template (seed.sh), then make ./lore self-contained and put a `lore` shim on ./bin
# for scripts that run `lore`. The repo root is the file ../../repo-root when the arm driver wrote one (a temp
# plugin copy), else six directories above this one (fixtures -> case -> experiment -> experiments ->
# tool -> tools -> repo).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$here/../../repo-root" ]; then
  repo="$(cat "$here/../../repo-root")"
else
  repo="$(cd "$here/../../../../../.." && pwd)"
fi
export AB_REPO_ROOT="$repo"
bash "$here/seed.sh"
mkdir -p lore-plugin bin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
line=$(grep -m1 ' exec ' lore)
rest=${line#* exec }
eval "set -- ${rest% \"\$@\"}"
py=$1
q=${py//\'/\'\\\'\'}
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec '"'"'%s'"'"' "$here/lore-plugin/cli/lore" "$@"\n' "$q" > lore
chmod +x lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")/.." && pwd)\nexec "$here/lore" "$@"\n' > bin/lore
chmod +x bin/lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
cp "$here/review.md" review.md
