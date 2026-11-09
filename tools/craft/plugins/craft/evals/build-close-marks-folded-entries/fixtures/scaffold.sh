#!/usr/bin/env bash
# seed with the unchanged lore template (seed.sh), make ./lore self-contained with a `lore` shim on ./bin for scripts that run `lore`,
# then write a ready spec with a done slice and a follow-ups section (F1 and F2 folded into the slice parent), the slice parent, and one done child that names F1
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
cp "$here/review.md" review.md
SPEC='# Tally report

## Acceptance Criteria

- **AC1.** The tool prints how many words a text file holds.

- **AC2.** The tool prints how many lines a text file holds.

## Slices

- **Count words** — You can count the words in a text file. (`task/count-words`, closed 2026-11-01, covers AC1)

## Follow-ups

- **F1.** Close the file the counting helper opens
  - status: folded into task/count-lines
  - what: the helper that reads a text file never closes the handle it opens, so a failed read leaves it open
  - where: `tally/counter.py`
  - found: while building Count words

- **F2.** Return a count record from every counting function
  - status: folded into task/count-lines
  - what: every counting function returns a bare number; change them all to return a record carrying the count and the unit, and update each caller and test
  - where: `tally/counter.py`, `tally/cli.py`, `tally/export.py`
  - found: while building Count words
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Tally report' --status ready >/dev/null
printf '**Value claim:** You can count the words in a text file.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'Count words' --status done --related spec=tally-report --label craft/slice-parent >/dev/null
printf '**Value claim:** You can count the lines in a text file.\n\n**Covers:** AC2\n\n**Folds:** spec/tally-report#F1, spec/tally-report#F2\n' | ./lore record create --kind task --title 'Count lines' --status in-progress --related spec=tally-report --label craft/slice-parent >/dev/null
printf '# Count lines build\n\n**Delivers:** The tool prints how many lines a text file holds.\nResolves spec/tally-report#F1: Close the file the counting helper opens.\n\n**Test contract:**\n- a text file with three lines prints 3\n- a failed read leaves no handle open\n\n**Files:** `tally/counter.py`, `tally/cli.py`\n' | ./lore record create --kind task --title 'Count lines build' --status done --parent count-lines --related spec=tally-report >/dev/null
