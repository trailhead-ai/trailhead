#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nprintf "%%s\\n" "$*" >> "$here/lore-calls.log"\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Print shipping labels

## Acceptance Criteria

- **AC1.** Labels print at the paper size the operator chose.

- **AC2.** Each label carries the order barcode.

- **AC3.** The printed label matches the on-screen preview.

## Slices

- **Print sized labels** — You can print a label at the paper size you chose. (`task/print-sized-labels`, closed 2026-11-01, covers AC1, AC2)
- **Show a label preview** — You see the label on screen before it prints. (`task/show-a-label-preview`, closed 2026-11-02, partially covers AC3)

## Waiting

- **AC3.** pending check — task/show-a-label-preview
  - check: the printed label matches the on-screen preview
  - how: print one label on the office printer and hold it against the preview
  - changed behaviour: no
  - 2026-11-05 waiting: printed labels are only checked on the office printer
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Print shipping labels' --status ready >/dev/null
printf '**Value claim:** You can print a label at the paper size you chose.\n\n**Covers:** AC1, AC2\n' | ./lore record create --kind task --title 'Print sized labels' --status done --related spec=print-shipping-labels --label craft/slice-parent >/dev/null
printf '**Value claim:** You see the label on screen before it prints.\n\n**Partially covers:** AC3\n' | ./lore record create --kind task --title 'Show a label preview' --status done --related spec=print-shipping-labels --label craft/slice-parent >/dev/null
: > lore-calls.log
