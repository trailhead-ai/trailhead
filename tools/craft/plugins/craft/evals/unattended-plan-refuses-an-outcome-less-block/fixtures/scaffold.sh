#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
base=$("$py" -c 'import sys; print(sys.base_prefix)')
# The runner's sandbox hides an interpreter installed under a home directory (uv's, on macOS), so the run
# carries a copy of it; a system interpreter stays visible and is linked, not copied.
case "$base" in
  /Users/*|/home/*) cp -R "$base" py-home ;;
  *) ln -s "$base" py-home ;;
esac
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nCLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"\n' > lore
chmod +x lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Totals report

## Problem

Operators add up the columns of the nightly CSV by hand. The tool is a new command written in
standard-library Python in this workspace; a total is the sum of one numeric column of the CSV input.

## Acceptance Criteria

- **AC1.** The tool prints the totals of a CSV input to the terminal.

- **AC2.** The tool prints a one-line summary after the totals.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Totals report' --status ready >/dev/null
PARENT='**Value claim:** You can run one command on a CSV input and read its column totals and a one-line summary on the terminal.

**Covers:** AC1, AC2

## Unattended run

**Format:** 1
**Rituals:** plan (1 calls decided unattended)
**Checkpoint:** creating the child tasks
**Planned tasks:**
- Sum each numeric column
- Print the totals and a summary line
**Decisions:**
1. **Took the approach with separate parts**
   - Question: how to split the work
   - Choice: each part of the module as its own task
   - Reasoning: each part can be tested on its own
**Parked questions:** none

**Goal:** A single command prints the column totals of a CSV input and a one-line summary on the terminal.

**Delta design:** A small standard-library Python module in two parts: a summer that reads a CSV input and adds each numeric column, and a printer that writes the totals and the one-line summary to the terminal. The parts are separate tasks so each is tested on its own.

**Given Axioms (ground truth this plan rests on):**
- Operators read the output on the terminal (spec/totals-report, Problem).

**Known Unknowns:**
- [x] None.

## Flow-out

- [ ] Touched area/subsystem profiles updated with what changed
- [ ] Prover-validated assumptions captured as session candidates (durable at flush)
- [ ] New decisions / lessons / follow-ups surfaced during the build recorded
'
printf '%s\n' "$PARENT" | ./lore record create --kind task --title 'Print the totals of a CSV input' --status in-progress --related spec=totals-report --label craft/slice-parent >/dev/null
CHILD0='**Delivers:** `totals.py` gains `sum_columns(rows)`, which returns the sum of each numeric column of the rows it is given.

**Test contract:** two rows of two numeric columns give two sums; a header row is skipped.

**Files:** `totals.py`; `test_totals.py`
'
printf '%s\n' "$CHILD0" | ./lore record create --kind task --title 'Sum each numeric column' --status ready --parent print-the-totals-of-a-csv-input >/dev/null
