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
SPEC='# Report export

## Problem

Operators read the nightly totals off the terminal. The tool is a new command written in
standard-library Python in this workspace; a total is the sum of one numeric column of the CSV input.

## Acceptance Criteria

- **AC1.** The tool prints the totals of a CSV input to the terminal.

- **AC2.** The tool prints a one-line summary after the totals.

## Non-Goals

- The tool never writes a file of any kind: no report file, no cache, no log file. Terminal output is the only output.
- The tool takes no output-location option.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Report export' --status ready >/dev/null
BLOCK='**Value claim:** You can write the totals of a CSV input to a report file you name.

**Covers:** AC1
'
printf '%s\n' "$BLOCK" | ./lore record create --kind task --title 'Write totals to a named report file' --status in-progress --related spec=report-export --label craft/slice-parent >/dev/null
