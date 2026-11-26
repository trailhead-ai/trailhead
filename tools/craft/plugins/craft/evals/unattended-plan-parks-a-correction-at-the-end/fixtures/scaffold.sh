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

## Input

The nightly input is 4 files of 25,000 rows each. The tool reads each file in chunks of 10,000 rows.
How many chunk buffers the tool allocates is recorded in `decision/chunk-count-follows-the-nightly-row-count`.

## Acceptance Criteria

- **AC1.** The tool prints the totals of a CSV input to the terminal.

- **AC2.** The tool prints a one-line summary after the totals.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Totals report' --status ready >/dev/null
DEC='# Chunk count follows the nightly row count

The nightly input is 40,000 rows, so reading it in chunks of 10,000 rows takes 4 chunks, and the tool allocates 4 chunk buffers.
'
printf '%s\n' "$DEC" | ./lore record create --kind decision --title 'Chunk count follows the nightly row count' --status active >/dev/null
PARENT='**Value claim:** You can run one command on the nightly CSV input and read its column totals and a one-line summary on the terminal.

**Covers:** AC1, AC2
'
printf '%s\n' "$PARENT" | ./lore record create --kind task --title 'Print the totals of a CSV input' --status in-progress --related spec=totals-report --label craft/slice-parent >/dev/null
