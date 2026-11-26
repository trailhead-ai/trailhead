#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin bin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nLORE_EMAIL=eval@example.invalid CLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")/.." && pwd)\nexec "$here/lore" "$@"\n' > bin/lore
chmod +x bin/lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Front desk

## Acceptance Criteria

- **AC1.** `deskctl books list` prints every book in the catalogue.

- **AC2.** `deskctl books add <name>` adds a book to the catalogue.

- **AC3.** `deskctl books edit <id> <name>` changes the name of a book.

- **AC4.** `GET /members` returns every member the service holds.

- **AC5.** `POST /members` creates a member.

- **AC6.** `PATCH /members/<id>` changes the name of a member.

## Slices

- **Book desk** — You can list, add and edit books at the desk. (`task/book-desk`, closed 2026-11-01, covers AC1, AC2, AC3)
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Front desk' --status ready >/dev/null
printf '**Value claim:** You can list, add and edit books at the desk.\n\n**Covers:** AC1, AC2, AC3\n' | ./lore record create --kind task --title 'Book desk' --status done --related spec=front-desk --label craft/slice-parent >/dev/null
