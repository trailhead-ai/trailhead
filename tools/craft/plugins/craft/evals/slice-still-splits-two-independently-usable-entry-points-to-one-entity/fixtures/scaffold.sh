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
SPEC='# Ticket queue

## Problem

Support tickets are one entity with two entry points, and each entry point serves a different consumer who never needs the other. Support staff work the queue at their terminals with the `deskq tickets` command group and never call the HTTP API. A separate billing system, owned by another team, calls the public HTTP API under `/tickets` to raise and read tickets and never runs the command. Each entry point is complete for its own consumer and delivers value without the other.

## Acceptance Criteria

- **AC1.** `deskq tickets list` prints every ticket in the queue.

- **AC2.** `deskq tickets add <title>` adds a ticket to the queue.

- **AC3.** `deskq tickets edit <id> <title>` changes the title of a ticket.

- **AC4.** `GET /tickets` returns every ticket in the queue.

- **AC5.** `POST /tickets` creates a ticket.

- **AC6.** `PATCH /tickets/<id>` changes the title of a ticket.
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Ticket queue' --status ready >/dev/null
