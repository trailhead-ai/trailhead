#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin bin scripts
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
cp -r "$repo/tools/craft/plugins/craft/scripts" real-scripts
rm -rf real-scripts/__pycache__
# ./lore is the agent's own door to the vault; every call is logged by its arguments.
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nprintf "%%s\\n" "$*" >> "$here/lore-calls.log"\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"\n' > lore
# bin/lore is what the plugin's scripts reach as `lore`; their calls go to a log of their own.
cat > bin/lore <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "$*" >> "$here/script-lore-calls.log"
LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"
EOW
# scripts/ is the plugin's scripts directory for this run: each script is a thin wrapper around a real copy, run against bin/lore.
wrap() {
  cat > "scripts/$1" <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
name=$(basename "$0")
printf '%s\n' "$name $*" >> "$here/lore-calls.log"
PATH="$here/bin:$PATH" exec "$here/py-home/bin/python3" "$here/real-scripts/$name" "$@"
EOW
  chmod +x "scripts/$1"
}
wrap found_work.py
wrap observation_gate.py
cat > scripts/pending_check.py <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "pending_check.py $*" >> "$here/lore-calls.log"
for a in "$@"; do
  if [ "$prev" = "--entries" ]; then
    { printf '%s\n' "--- entries file:"; cat "$a"; } >> "$here/pending-check-entries.log"
  fi
  prev=$a
done
PATH="$here/bin:$PATH" exec "$here/py-home/bin/python3" "$here/real-scripts/pending_check.py" "$@"
EOW
chmod +x lore bin/lore scripts/pending_check.py
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC="$(cat <<'EOS'
# Export invoices

## Problem

Accountants need an invoice list they can open in a spreadsheet.

## Acceptance Criteria

- **AC1.** An export lists every invoice's number and its total. *Verified by: automated assertion.*

## Slices

- **Export invoices as CSV** — You can export invoices as a CSV file and see each invoice's total. (`task/export-invoices-as-csv`, in progress)
EOS
)"
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Export invoices' --status ready >/dev/null
PARENT="$(cat <<'EOS'
**Value claim:** You can export invoices as a CSV file and see each invoice's total.

**Covers:** AC1

## Flow-out

- [ ] Touched area/subsystem profiles updated with what changed
- [ ] Prover-validated assumptions captured as session candidates (durable at flush)
- [ ] New decisions / lessons / follow-ups surfaced during the build recorded

## End Phases

- [x] Phase 1: test-runner gate
- [x] Phase 2: simplify
- [ ] Phase 3: correctness
- [ ] Phase 4: security
- [ ] Phase 5: flow-out
- [ ] Phase 6: close and completion report

## Unattended run

**Format:** 1
**Rituals:** execute (0 calls decided unattended)
**Checkpoint:** workspace ready
**Decisions:**
**Parked questions:** none
EOS
)"
printf '%s\n' "$PARENT" | ./lore record create --kind task --title 'Export invoices as CSV' --status in-progress --related spec=export-invoices --label craft/slice-parent --label craft/branch=export-invoices-as-csv >/dev/null
printf '**Delivers:** A command that writes the invoice list to a CSV file.\n\n**Test contract:** Exporting two invoices writes a header row and two rows.\n\n**Files:** `export/csv.py`, `test_csv.py`.\n' | ./lore record create --kind task --title 'Write the CSV exporter' --status ready --parent export-invoices-as-csv >/dev/null
printf '**Delivers:** A total column on each exported row.\n\n**Test contract:** An invoice of 12.50 exports a total of 12.50.\n\n**Files:** `export/csv.py`.\n' | ./lore record create --kind task --title 'Add the total column' --status ready --parent export-invoices-as-csv >/dev/null
./lore record update task/write-the-csv-exporter --status done >/dev/null
./lore record update task/add-the-total-column --status done >/dev/null
cp "$here/re-review.md" re-review.md
: > lore-calls.log
: > script-lore-calls.log
: > pending-check-entries.log
