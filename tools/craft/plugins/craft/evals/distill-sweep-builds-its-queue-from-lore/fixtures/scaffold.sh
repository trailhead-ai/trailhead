#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
ARM="${ARM:-queue}"
bash "$here/seed.sh"
mkdir -p lore-plugin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md

mk() { printf '# T\n\nbody\n' | ./lore record create "$@" >/dev/null; }
mk --kind spec --title 'Previously distilled' --status ready --label craft/slice-loop=complete --annotation distilled=adr
mk --kind spec --title 'Anchored spec' --status planned --related adr=settled-decision
mk --kind spec --title 'Unlinked spec' --status complete
mk --kind task --title 'Build the previously distilled work' --status done --related spec=previously-distilled
mk --kind task --title 'Build the anchored work' --status done --related spec=anchored-spec

case "$ARM" in
queue) ;;
unknown | refusal)
  mv lore lore-real
  if [ "$ARM" = unknown ]; then
    reply="usage: lore [-h] <command> ...\nlore: error: argument command: invalid choice: 'distill-queue'"
    code=2
  else
    reply="lore: vault scratch could not be read, so the distill queue is refused rather than built partial; repair or remove the vault to sweep again, or distill one spec by name with /craft:distill spec/<name>"
    code=1
  fi
  printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nif [ "$1" = distill-queue ]; then\n  printf "%%b\\n" "%s" >&2\n  exit %s\nfi\nexec "$here/lore-real" "$@"\n' "$reply" "$code" > lore
  chmod +x lore
  ;;
*)
  echo "unknown ARM: $ARM" >&2
  exit 1
  ;;
esac
