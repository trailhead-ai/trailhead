#!/usr/bin/env bash
# Thin scaffold: this case's parameters, then the corpus's shared scaffold.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
# shellcheck disable=SC1091
. "$here/params"
set +a
W="$PWD"
bash "$here/../../pr-step-scaffold.sh"
# Replace the seeded record body with this case's earlier run block(s).
"$W/lore" record update task/count-words >/dev/null <<'BODY'
**Value claim:** You can count the words and lines in a text file.

**Covers:** AC1, AC2

## Unattended run

**Format:** 1
**Rituals:** execute (2 calls decided unattended)
**Outcome:** needs-a-decision
**Checkpoint:** needs-a-decision
**Decisions:**
1. **Built the word counter before the line counter**
   - Question: which child task to build first
   - Choice: the word counter
   - Reasoning: the line counter reuses its file reader
**Parked questions:**
- Q1: **Correct the stale usage note in the text-handling area profile**
  - Question: whether to correct the usage note on the area profile, a record outside this slice
  - Options: (a) correct the note to match the shipped counter — costs one record edit; (b) leave the note as it is — costs a note that stays out of date (recommended)
  - Progress: the counter and its tests are built; the area profile was not touched
**Answer:** (a)


## Unattended run

**Format:** 1
**Rituals:** execute (2 calls decided unattended), pull_request (0 calls decided unattended)
**Checkpoint:** close
**Decisions:**
1. **Built the word counter before the line counter**
   - Question: which child task to build first
   - Choice: the word counter
   - Reasoning: the line counter reuses its file reader
**Parked questions:**
- Q1: still open — answer goes under the original Q1 entry

BODY
