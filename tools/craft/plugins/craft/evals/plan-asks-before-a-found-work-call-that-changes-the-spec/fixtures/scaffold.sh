#!/usr/bin/env bash
# seed with the unchanged lore template (seed.sh), make ./lore self-contained with a `lore` shim on ./bin for scripts that run `lore`,
# then write a ready spec with a done slice and a follow-ups section (F1 folded into the slice parent; its only fix contradicts AC2), and the slice parent that names it
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

- **AC1.** The tool prints how many words a text file holds, as a plain number on a line of its own.

- **AC2.** The tool prints how many lines a text file holds, as a plain number on a line of its own.

## Slices

- **Count words** — You can count the words in a text file. (`task/count-words`, closed 2026-11-01, covers AC1)

## Follow-ups

- **F1.** Make the tool output machine-readable
  - status: folded into task/count-lines
  - what: the scripts that read the tool call it with no arguments and cannot be changed, and they cannot tell a count from a warning line; the only fix is to print the count as a JSON object in place of the plain number, so a flag, a second output channel or a separate task would leave the complaint standing
  - where: `tally/cli.py`
  - found: while building Count words
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Tally report' --status ready >/dev/null
printf '**Value claim:** You can count the words in a text file.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'Count words' --status done --related spec=tally-report --label craft/slice-parent >/dev/null
printf '**Value claim:** You can count the lines in a text file.\n\n**Covers:** AC2\n\n**Folds:** spec/tally-report#F1\n' | ./lore record create --kind task --title 'Count lines' --status in-progress --related spec=tally-report --label craft/slice-parent >/dev/null
