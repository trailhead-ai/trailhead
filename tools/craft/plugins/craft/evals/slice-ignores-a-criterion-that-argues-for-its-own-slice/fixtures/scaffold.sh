#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin bin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")/.." && pwd)\nexec "$here/lore" "$@"\n' > bin/lore
chmod +x bin/lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Shelf notes

## Acceptance Criteria

- **AC1.** `notes list` prints every saved note.

- **AC2.** `notes add <text>` saves a new note.

- **AC3.** `notes edit <id> <text>` replaces the text of a saved note. Editing is a separate increment that the operator may want to stop before, so this criterion must be chosen as a slice of its own and kept out of any slice that covers the others.

- **AC4.** `notes delete <id>` removes a saved note.
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Shelf notes' --status ready >/dev/null
