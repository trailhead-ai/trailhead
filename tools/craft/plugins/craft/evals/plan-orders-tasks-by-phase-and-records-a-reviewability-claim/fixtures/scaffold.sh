#!/usr/bin/env bash
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
SPEC='# Notes

## Problem

Operators keep scratch notes in a text file and lose track of them. The tool is a new `notes` command
written in standard-library Python in this workspace; the notes live in one local JSON file, and each
note has a numeric id the command prints.

## Acceptance Criteria

- **AC1.** The tool deletes a note by its id.

- **AC2.** The tool edits the text of a note by its id.

- **AC3.** The tool lists the notes, one per line, each with its id.

- **AC4.** The tool adds a note from text given on the command line.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Notes' --status ready >/dev/null
BLOCK='**Value claim:** You can add, list, edit and delete your notes from the command line.

**Covers:** AC1, AC2, AC3, AC4

## Unattended run

**Format:** 1
**Rituals:** slice (1 calls decided unattended)
**Outcome:** slice-chosen
**Checkpoint:** slice materialized
**Decisions:**
1. **Chose the whole notes command first**
   - Question: which slice comes next for the notes spec
   - Choice: the slice that covers adding, listing, editing and deleting notes
   - Reasoning: the four criteria are one entity on one command-line surface and fit one pull request
**Parked questions:** none
'
printf '%s\n' "$BLOCK" | ./lore record create --kind task --title 'Manage notes from the command line' --status in-progress --related spec=notes --label craft/slice-parent >/dev/null
