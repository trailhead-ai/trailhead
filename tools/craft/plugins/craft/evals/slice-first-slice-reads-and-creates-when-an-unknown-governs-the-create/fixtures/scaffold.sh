#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin bin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nCLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")/.." && pwd)\nexec "$here/lore" "$@"\n' > bin/lore
chmod +x bin/lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Shelf notes

## Acceptance Criteria

- **AC1.** `notes list` prints every note saved in the notes file, including the notes the old `jot` tool already saved there.

- **AC2.** `notes add <text>` saves a new note to the notes file.

## Open Questions

- Does the notes file keep both notes when two terminals run `notes add` at the same moment, or does the second write overwrite the first? Nobody has found out yet. It has to be learned before anything that writes to the notes file is built: `notes add` is the only criterion that writes, so it cannot be built until the answer is known. `notes list` only reads the file and does not depend on it.
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Shelf notes' --status ready >/dev/null
