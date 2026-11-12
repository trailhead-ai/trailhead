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
**Rituals:** execute (2 calls decided unattended), pull_request (0 calls decided unattended)
**Checkpoint:** monitor dispatched; PR https://example.test/o/r/pull/7; deadline 1800 seconds after this write
**Decisions:**
1. **Built the word counter before the line counter**
   - Question: which child task to build first
   - Choice: the word counter
   - Reasoning: the line counter reuses its file reader
**Parked questions:** none
BODY
