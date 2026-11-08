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

## Acceptance Criteria

- **AC1.** The tool prints how many words a text file holds.

- **AC2.** The tool prints how many lines a text file holds.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Word count tool' --status ready >/dev/null
printf '**Value claim:** You can count the words in a text file.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'Count words' --status done --related spec=word-count-tool --label craft/slice-parent >/dev/null
printf '**Value claim:** You can count the lines in a text file.\n\n**Covers:** AC2\n' | ./lore record create --kind task --title 'Count lines' --status in-progress --related spec=word-count-tool --label craft/slice-parent >/dev/null
