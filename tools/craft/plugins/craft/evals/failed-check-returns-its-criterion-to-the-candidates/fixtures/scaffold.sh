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
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nprintf "%%s\\n" "$*" >> "$here/lore-calls.log"\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"\n' > lore
chmod +x lore
mkdir -p bin scripts
cp -r "$repo/tools/craft/plugins/craft/scripts" real-scripts
rm -rf real-scripts/__pycache__
# bin/lore is what the plugin's scripts reach as `lore`; their calls go to a log of their own.
cat > bin/lore <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "$*" >> "$here/script-lore-calls.log"
LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"
EOW
# scripts/pending_check.py runs the plugin's own writer against bin/lore.
cat > scripts/pending_check.py <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "pending_check.py $*" >> "$here/lore-calls.log"
PATH="$here/bin:$PATH" exec "$here/py-home/bin/python3" "$here/real-scripts/pending_check.py" "$@"
EOW
chmod +x bin/lore scripts/pending_check.py
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Print shipping labels

## Acceptance Criteria

- **AC1.** Labels print at the paper size the operator chose.

- **AC2.** Each label carries the order barcode.

- **AC3.** The printed label matches the on-screen preview.

## Slices

- **Print sized labels** — You can print a label at the paper size you chose. (`task/print-sized-labels`, closed 2026-11-01, covers AC1, AC2)
- **Show a label preview** — You see the label on screen before it prints. (`task/show-a-label-preview`, closed 2026-11-02, partially covers AC3)

## Waiting

- **AC3.** pending check — task/show-a-label-preview
  - check: the printed label matches the on-screen preview
  - how: print one label on the office printer and hold it against the preview
  - changed behaviour: no
  - 2026-11-05 waiting: printed labels are only checked on the office printer
  - 2026-11-06 failed: the barcode was cut off on the printed label
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Print shipping labels' --status ready >/dev/null
printf '**Value claim:** You can print a label at the paper size you chose.\n\n**Covers:** AC1, AC2\n' | ./lore record create --kind task --title 'Print sized labels' --status done --related spec=print-shipping-labels --label craft/slice-parent >/dev/null
printf '**Value claim:** You see the label on screen before it prints.\n\n**Partially covers:** AC3\n' | ./lore record create --kind task --title 'Show a label preview' --status done --related spec=print-shipping-labels --label craft/slice-parent >/dev/null
: > lore-calls.log
