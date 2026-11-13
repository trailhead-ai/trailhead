#!/usr/bin/env bash
# Seeds a self-contained ./lore, a bin/lore for the plugin's scripts, logging wrappers over real copies of
# craft's scripts in scripts/ (set_aside.py delegates to the real script and keeps a copy of its entries file),
# and a ready spec plus an in-progress slice parent with one child whose second criterion the build found waiting on an outside service.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin bin scripts
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
base=$("$py" -c 'import sys; print(sys.base_prefix)')
# The runner's sandbox hides an interpreter installed under a home directory, so the run carries a copy of it;
# a system interpreter stays visible and is linked, not copied.
case "$base" in
  /Users/*|/home/*) cp -R "$base" py-home ;;
  *) ln -s "$base" py-home ;;
esac
cp -r "$repo/tools/craft/plugins/craft/scripts" real-scripts
rm -rf real-scripts/__pycache__
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nprintf "%%s\\n" "$*" >> "$here/lore-calls.log"\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"\n' > lore
# bin/lore is what the plugin's scripts reach as `lore`; their calls go to a log of their own.
cat > bin/lore <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "$*" >> "$here/script-lore-calls.log"
LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"
EOW
# scripts/ is the plugin's scripts directory for this run: each script is a logging wrapper over the real one.
for f in ledger_gate candidate_set covers_gate found_work pending_check observation_gate; do
  cat > "scripts/$f.py" <<EOW
#!/bin/sh
here=\$(cd "\$(dirname "\$0")/.." && pwd)
printf '%s\n' "$f.py \$*" >> "\$here/lore-calls.log"
PATH="\$here/bin:\$PATH" exec "\$here/py-home/bin/python3" "\$here/real-scripts/$f.py" "\$@"
EOW
done
# set_aside.py logs its arguments and a copy of the --entries file at call time, then runs the real script.
cat > scripts/set_aside.py <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "set_aside.py $*" >> "$here/lore-calls.log"
prev=
for a in "$@"; do
  if [ "$prev" = "--entries" ]; then
    { printf '%s\n' "--- entries file:"; cat "$a"; } >> "$here/set-aside-entries.log"
  fi
  prev=$a
done
PATH="$here/bin:$PATH" exec "$here/py-home/bin/python3" "$here/real-scripts/set_aside.py" "$@"
EOW
chmod +x lore bin/lore scripts/*.py
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Invoice members

## Acceptance Criteria

- **AC1.** A member can download a PDF copy of any past invoice.

- **AC2.** Each invoice shows the sales tax the payment provider'"'"'s tax service calculates for the member'"'"'s address.

## Slices

- **Members can download invoices with sales tax** — You can download a past invoice that shows its sales tax. (`task/members-can-download-invoices-with-sales-tax`, in progress)

## Notes

The payment provider'"'"'s tax service has a test mode with sample keys and a documented sample response, so tests can stand in for it with a fake that returns the same shape.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Invoice members' --status ready >/dev/null
PARENT='**Value claim:** You can download a past invoice that shows its sales tax.

**Covers:** AC1, AC2

## Criterion observations

- **AC1** — automated-assertion — tests/test_invoice_pdf.py::test_past_invoice_downloads_as_pdf
'
printf '%s\n' "$PARENT" | ./lore record create --kind task --title 'Members can download invoices with sales tax' --status in-progress --related spec=invoice-members --label craft/slice-parent --label craft/branch=members-can-download-invoices-with-sales-tax >/dev/null
printf '%s\n' '**Delivers:** An invoice downloads as a PDF and its sales tax line is collected under the company registration.

**Test contract:**
- a past invoice downloads as a PDF
- the sales tax line carries the registered amount

**Files:** `invoices/pdf.py`, `invoices/tax.py`' | ./lore record create --kind task --title 'Invoice PDF with a sales tax line' --status in-progress --parent members-can-download-invoices-with-sales-tax --related spec=invoice-members >/dev/null
: > lore-calls.log
: > script-lore-calls.log
: > set-aside-entries.log
