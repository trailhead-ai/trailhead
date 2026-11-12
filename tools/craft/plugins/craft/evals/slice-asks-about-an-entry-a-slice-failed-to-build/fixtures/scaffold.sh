#!/usr/bin/env bash
# seed with the unchanged lore template (seed.sh), make ./lore self-contained with a `lore` shim on ./bin for scripts that run `lore`,
# then write a ready spec whose ledger closes both criteria through two done slice parents, a third done slice parent made only of entries
# (it folds F1 and covers nothing), and a follow-ups section with F1 back open; one open task holds the work F1 asks for.
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
cp -r "$here/labels" labels
SPEC='# Parcel labels

## Acceptance Criteria

- **AC1.** The tool prints the label text for every parcel in a manifest file.

- **AC2.** The tool prints how many parcels a manifest file holds.

## Slices

- **Print labels** — You can print a label for each parcel in a manifest. (`task/print-labels`, closed 2026-11-01, covers AC1)
- **Count parcels** — You can count the parcels in a manifest. (`task/count-parcels`, closed 2026-11-02, covers AC2)
- **Tidy the label printer** — You can print labels without leaving the manifest file open. (`task/tidy-the-label-printer`, closed 2026-11-03)

## Follow-ups

- **F1.** Close the manifest file the label reader opens
  - status: open
  - what: the helper that reads a manifest never closes the file it opens, so a failed read leaves the handle open
  - where: `labels/printer.py`
  - found: while building Count parcels
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Parcel labels' --status ready >/dev/null
printf '**Value claim:** You can print a label for each parcel in a manifest.\n\n**Covers:** AC1\n' | ./lore record create --kind task --title 'Print labels' --status done --related spec=parcel-labels --label craft/slice-parent >/dev/null
printf '**Value claim:** You can count the parcels in a manifest.\n\n**Covers:** AC2\n' | ./lore record create --kind task --title 'Count parcels' --status done --related spec=parcel-labels --label craft/slice-parent >/dev/null
printf '**Value claim:** You can print labels without leaving the manifest file open.\n\n**Folds:** spec/parcel-labels#F1\n' | ./lore record create --kind task --title 'Tidy the label printer' --status done --related spec=parcel-labels --label craft/slice-parent >/dev/null
printf '**Value claim:** Make every reader in the label tool release the files it opens, including after a failed read.\n' | ./lore record create --kind task --title 'Label tool releases the files it opens' --status open >/dev/null
