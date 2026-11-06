#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Export invoices

## Acceptance Criteria

- **AC1.** Invoices can be exported as CSV.

- **AC2.** Exports include the invoice total.

- **AC3.** Export failures are reported to the user.

## Slices

- **Export CSV** — You can export invoices as CSV. (`task/export-csv`, closed 2026-11-01, covers AC1, AC2)
- **Report export failures** — You see why an export failed. (`task/report-export-failures`, closed 2026-11-02, covers AC3)
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Export invoices' --status ready >/dev/null
printf '**Value claim:** You can export invoices as CSV.\n\n**Covers:** AC1, AC2\n' | ./lore record create --kind task --title 'Export CSV' --status done --related spec=export-invoices --label craft/slice-parent >/dev/null
printf '**Value claim:** You see why an export failed.\n\n**Covers:** AC3\n' | ./lore record create --kind task --title 'Report export failures' --status done --related spec=export-invoices --label craft/slice-parent >/dev/null
