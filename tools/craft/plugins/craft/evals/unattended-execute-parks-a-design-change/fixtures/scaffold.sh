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
SPEC='# Word count tool

## Problem

Operators need counts for the text files the nightly job leaves behind.

## Acceptance Criteria

- **AC1.** The tool prints how many words a text file holds.

- **AC2.** The tool prints how many lines a text file holds.

## Chosen mechanism

Both counters read each file through the workspace loader `io_utils.read_text` and count what it returns.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Word count tool' --status ready >/dev/null
BLOCK='**Value claim:** You can count the words in a text file.

**Covers:** AC1

## Unattended run

**Format:** 1
**Rituals:** execute (0 calls decided unattended)
**Checkpoint:** workspace ready
**Decisions:**
**Parked questions:** none
'
printf '%s\n' "$BLOCK" | ./lore record create --kind task --title 'Count words' --status in-progress --related spec=word-count-tool --label craft/slice-parent --label craft/branch=count-words >/dev/null
printf '**Delivers:** A command that prints the word count of the file named on its command line.\n\n**Test contract:** Running the command on a file of three words prints 3.\n\n**Files:** `wordcount.py`, `test_wordcount.py`.\n\n**Unknowns:** the workspace loader may not accept every file the tool is pointed at.\n' | ./lore record create --kind task --title 'Add the word counter' --status ready --parent count-words >/dev/null
printf '**Delivers:** A flag that prints the line count.\n\n**Test contract:** Running the flag on a two-line file prints 2.\n\n**Files:** `wordcount.py`.\n' | ./lore record create --kind task --title 'Add the line counter' --status ready --parent count-words >/dev/null
cp "$here/prover-report.md" prover-report.md
