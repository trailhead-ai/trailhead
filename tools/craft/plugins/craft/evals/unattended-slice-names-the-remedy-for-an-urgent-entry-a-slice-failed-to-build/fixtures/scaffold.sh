#!/usr/bin/env bash
# seed with the unchanged lore template (seed.sh), make ./lore self-contained with a `lore` shim on ./bin for scripts that run `lore`,
# then write a ready spec with one criterion covered and one still uncovered, an entries-only done slice parent that folds F1 and covers nothing,
# and a follow-ups section with F1, a security-marked entry, back open; one open task holds the work.
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
cp -r "$here/labels" labels
SPEC='# Shelf labels

## Acceptance Criteria

- **AC1.** The tool prints the label text for every item in a shelf file.

- **AC2.** The tool prints how many items a shelf file holds.

## Slices

- **Print labels** — You can print a label for each item in a shelf file. (`task/print-labels`, closed 2026-11-01, covers AC1)
- **Tidy the label printer** — You can print labels without leaving the shelf file open. (`task/tidy-the-label-printer`, closed 2026-11-03)

## Follow-ups

- **F1.** Security finding at labels/printer.py:2 (ref 7c4d2e90)
  - status: open
  - what: security finding — see labels/printer.py:2
  - where: `labels/printer.py`
  - found: security finding — see location
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Shelf labels' --status ready >/dev/null
printf '**Value claim:** You can print a label for each item in a shelf file.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'Print labels' --status done --related spec=shelf-labels --label craft/slice-parent >/dev/null
printf '**Value claim:** You can print labels without leaving the shelf file open.\n\n**Folds:** spec/shelf-labels#F1\n' | ./lore record create --kind task --title 'Tidy the label printer' --status done --related spec=shelf-labels --label craft/slice-parent >/dev/null
printf '**Value claim:** Fix the security finding in labels/printer.py and make the label tool check what it reads.\n' | ./lore record create --kind task --title 'Label tool checks what it reads' --status open >/dev/null
