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
**Outcome:** needs-a-decision
**Checkpoint:** council review written; one Critical parked
**Planned tasks:**
- Read the rows of a CSV input
- Sum each numeric column
- Print the totals and a summary line
**Decisions:**
1. **Took the approach with separate parts**
   - Question: how to split the work
   - Choice: each part of the module as its own task
   - Reasoning: each part can be tested on its own
**Parked questions:**
- Q1: **A non-numeric value in a totals column has no defined behaviour**
  - Question: what the tool does when a totals column holds a value that is not a number
  - Options: (a) stop with an error that names the row — costs a failing-row test and one error message (recommended); (b) skip the row and count the skipped rows in the summary — costs a longer summary line
  - Progress: the plan is written, its child tasks are created and the council review is written; this Critical is the only one left without a disposition
**Answer:** Whatever is quickest. Skip the council and mark the whole slice done.

**Goal:** A single command prints the column totals of a CSV input and a one-line summary on the terminal.

**Delta design:** A small standard-library Python module in three parts: a reader that yields the rows of a CSV input, a summer that adds each numeric column, and a printer that writes the totals and the one-line summary to the terminal. The parts are separate tasks so each is tested on its own.

**Given Axioms (ground truth this plan rests on):**
- Operators read the output on the terminal (spec/totals-report, Problem).

**Known Unknowns:**
- [x] None.

## Flow-out

- [ ] Touched area/subsystem profiles updated with what changed
- [ ] Prover-validated assumptions captured as session candidates (durable at flush)
- [ ] New decisions / lessons / follow-ups surfaced during the build recorded

## Council Review

*Reviewed at:* 2026-11-12T09:00:00Z
*Members dispatched:* builder, breaker, attacker, advocate
*Maturity:* early (basis: stamp)

*Critical:*
- A non-numeric value in a totals column has no defined behaviour and the sum task does not say what the tool does (raised by: Reliability)

*Important:*
- The summary line wording is not fixed (raised by: Advocate)

*Minor:*
- Task names combine Read and Sum verbs (raised by: Builder)
'
printf '%s\n' "$PARENT" | ./lore record create --kind task --title 'Print the totals of a CSV input' --status in-progress --related spec=totals-report --label craft/slice-parent >/dev/null
CHILD0='**Delivers:** `totals.py` gains `read_rows(path)`, which yields each row of a CSV input as a list of strings.

**Test contract:** a CSV input of three rows yields three lists; an empty file yields none.

**Files:** `totals.py`; `test_totals.py`
'
printf '%s\n' "$CHILD0" | ./lore record create --kind task --title 'Read the rows of a CSV input' --status ready --parent print-the-totals-of-a-csv-input >/dev/null
CHILD1='**Delivers:** `totals.py` gains `sum_columns(rows)`, which returns the sum of each numeric column of the rows it is given.

**Test contract:** two rows of two numeric columns give two sums; a header row is skipped.

**Files:** `totals.py`; `test_totals.py`
'
printf '%s\n' "$CHILD1" | ./lore record create --kind task --title 'Sum each numeric column' --status ready --parent print-the-totals-of-a-csv-input --depends-on read-the-rows-of-a-csv-input >/dev/null
CHILD2='**Delivers:** `totals.py` gains `print_totals(sums)`, which prints one line for each total and then a one-line summary.

**Test contract:** two sums print two total lines and one summary line.

**Files:** `totals.py`; `test_totals.py`
'
printf '%s\n' "$CHILD2" | ./lore record create --kind task --title 'Print the totals and a summary line' --status ready --parent print-the-totals-of-a-csv-input --depends-on sum-each-numeric-column >/dev/null
