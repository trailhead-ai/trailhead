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
SPEC='# Billing desk

## Acceptance Criteria

- **AC1.** `ledgerctl invoices list` prints every invoice in the catalogue.

- **AC2.** `ledgerctl invoices add <name>` adds an invoice to the catalogue.

- **AC3.** `ledgerctl invoices edit <id> <name>` changes the name of an invoice.

- **AC4.** `GET /customers` returns every customer the service holds.

- **AC5.** `POST /customers` creates a customer.

- **AC6.** `PATCH /customers/<id>` changes the name of a customer.

## Slices

- **List invoices** — You can list the invoices. (`task/list-invoices`, closed 2026-11-01, covers AC1)
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Billing desk' --status ready >/dev/null
printf '**Value claim:** You can list the invoices.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'List invoices' --status done --related spec=billing-desk --label craft/slice-parent >/dev/null
