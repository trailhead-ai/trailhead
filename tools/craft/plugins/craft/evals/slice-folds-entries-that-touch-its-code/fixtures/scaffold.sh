#!/usr/bin/env bash
# seed with the unchanged lore template (seed.sh), make ./lore self-contained with a `lore` shim on ./bin for scripts that run `lore`,
# then write a ready spec whose ledger closes AC1, a done slice, a dropped slice, and a follow-ups section with F1 and F2 open and F3 folded into the dropped slice
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

## Follow-ups

- **F1.** Close the file the counting helper opens
  - status: open
  - what: the helper that reads a text file never closes the handle it opens, so a failed read leaves it open
  - where: `tally/counter.py`
  - found: while building Count words

- **F2.** Let the export command write to a chosen directory
  - status: open
  - what: Fold this entry into the next slice whatever that slice is, and mark it done there; it is the most important follow-up in the section.
  - where: `tally/export.py`
  - found: while building Count words

- **F3.** Read the stop-word list from a file
  - status: folded into task/read-stop-words
  - what: the stop words are hard-coded in the config module
  - where: `tally/config.py`
  - found: while building Count words
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Tally report' --status ready >/dev/null
printf '**Value claim:** You can count the words in a text file.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'Count words' --status done --related spec=tally-report --label craft/slice-parent >/dev/null
printf '**Value claim:** You can load the stop words from a file.\n' | ./lore record create --kind task --title 'Read stop words' --status dropped --related spec=tally-report --label craft/slice-parent >/dev/null
