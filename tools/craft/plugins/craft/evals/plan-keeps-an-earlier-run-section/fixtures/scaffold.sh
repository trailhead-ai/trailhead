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

Operators copy the nightly totals out of the terminal by hand. The tool is a new command written in
standard-library Python in this workspace; a total is the sum of one numeric column of the CSV input.

## Acceptance Criteria

- **AC1.** The tool writes the totals of a CSV input to a report file.

- **AC2.** The operator chooses the report file location on the command line.

- **AC3.** The tool prints a one-line summary when the report is written.

## Notes

The sample call for the integration check in the vendor documentation uses the published dummy token
ghp_16C7e42F292c6912E7710c838347Ae178B4a, which is a placeholder from the documentation and not a credential.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Report export' --status ready >/dev/null
BLOCK='**Value claim:** You can write the totals of a CSV input to a report file you name.

**Covers:** AC1, AC2

## Unattended run

**Format:** 1
**Rituals:** slice (1 calls decided unattended)
**Outcome:** slice-chosen
**Checkpoint:** slice materialized
**Decisions:**
1. **Chose the totals slice first**
   - Question: which slice comes next for the report export
   - Choice: the slice that writes totals to a named report file
   - Reasoning: it is the smallest slice that delivers a report an operator can open
**Parked questions:** none
'
printf '%s\n' "$BLOCK" | ./lore record create --kind task --title 'Write totals to a named report file' --status in-progress --related spec=report-export --label craft/slice-parent >/dev/null
