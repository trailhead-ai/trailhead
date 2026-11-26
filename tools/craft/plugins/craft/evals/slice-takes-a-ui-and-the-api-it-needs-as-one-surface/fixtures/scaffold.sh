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
SPEC='# Reservation book

## Problem

The restaurant keeps its reservations in a paper notebook. This spec builds one reservations service reached two ways: a web page the host stand uses, and a public HTTP API under `/api/reservations`. The web page is only a front end for the API. Every list it shows and every reservation it adds goes through the API, so the page does nothing while the API is missing. The API has no consumer of its own yet: no other system, script or partner calls it, and nobody would use it without the page. Neither part gives the restaurant anything without the other.

## Acceptance Criteria

- **AC1.** The Reservations page lists every reservation.

- **AC2.** The Reservations page has a form that adds a reservation.

- **AC3.** `GET /api/reservations` returns every reservation.

- **AC4.** `POST /api/reservations` creates a reservation.
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Reservation book' --status ready >/dev/null
