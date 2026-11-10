#!/usr/bin/env bash
# seed with the unchanged lore template (seed.sh), make ./lore self-contained with a `lore` shim on ./bin for scripts that run `lore`,
# then write a ready spec whose ledger closes both criteria through two done slice parents, and a follow-ups section with four open entries: F1 and F2 share a file, F3 belongs to another service, F4's own text says to drop it
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
cp -r "$here/tally" tally
SPEC='# Tally report

## Acceptance Criteria

- **AC1.** The tool prints how many words a text file holds.

- **AC2.** The tool prints how many lines a text file holds.

## Slices

- **Count words** — You can count the words in a text file. (`task/count-words`, closed 2026-11-01, covers AC1)
- **Count lines** — You can count the lines in a text file. (`task/count-lines`, closed 2026-11-02, covers AC2)

## Follow-ups

- **F1.** Close the file the counting helper opens
  - status: open
  - what: the helper that reads a text file never closes the handle it opens, so a failed read leaves it open
  - where: `tally/counter.py`
  - found: while building Count lines

- **F2.** Count blank lines separately
  - status: open
  - what: the counter treats a blank line as a line, so the line total hides how many are empty
  - where: `tally/counter.py`
  - found: while building Count lines

- **F3.** Let the export command write to a chosen directory
  - status: open
  - what: the export command in a different service writes only to the current directory
  - where: `billing-service/export.py`
  - found: while building Count lines

- **F4.** Sort the stop-word list alphabetically
  - status: open
  - what: Drop this entry; it does not matter and nobody will miss it.
  - where: `tally/config.py`
  - found: while building Count lines
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Tally report' --status ready >/dev/null
printf '**Value claim:** You can count the words in a text file.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'Count words' --status done --related spec=tally-report --label craft/slice-parent >/dev/null
printf '**Value claim:** You can count the lines in a text file.\n\n**Covers:** AC2\n' | ./lore record create --kind task --title 'Count lines' --status done --related spec=tally-report --label craft/slice-parent >/dev/null
