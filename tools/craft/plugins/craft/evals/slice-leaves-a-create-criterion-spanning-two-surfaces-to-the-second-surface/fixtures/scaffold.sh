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
SPEC='# Team notes

## Problem

The team keeps short notes in a shared notes file and wants two things built. The first is the `notes` command, which the team runs at their terminals. Today the file is edited by hand, so `notes list` and `notes show` are useful on their own the day they exist. The second is a separate web feed service. Staff already post announcements to it directly, with no help from the `notes` command, so the feed is useful on its own too. The two are separate programs with separate consumers. The command group is the part to build first.

## Acceptance Criteria

- **AC1.** `notes list` prints every note in the notes file.

- **AC2.** `notes show <id>` prints one note.

- **AC3.** `notes add <text>` saves a new note and also publishes it to the web feed.

- **AC4.** `POST /feed/announcements` posts an announcement to the web feed.

- **AC5.** `DELETE /feed/announcements/<id>` removes an announcement from the web feed.
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Team notes' --status ready >/dev/null
