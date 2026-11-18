#!/usr/bin/env bash
# Seeds a self-contained ./lore, a bin/lore for the plugin's scripts, logging wrappers over real copies of
# craft's scripts in scripts/ (set_aside.py delegates to the real script and keeps a copy of its entries file),
# and a ready spec with no slices yet whose first criterion needs a live registration nobody holds.
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
for f in ledger_gate candidate_set covers_gate found_work pending_check; do
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
SPEC='# Bill members monthly

## Acceptance Criteria

- **AC1.** Sales tax is collected on each invoice under the company'"'"'s live registration with the payment provider'"'"'s tax service.

- **AC2.** A member can download a PDF copy of any past invoice.

- **AC3.** Each invoice lists its line items with the price of each.

## Notes

The company has not applied for the payment provider'"'"'s tax registration. The provider issues it only after a business-verification review that takes several weeks, and it offers no test version of it: a registration is either live or does not exist.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Bill members monthly' --status ready >/dev/null
: > lore-calls.log
: > script-lore-calls.log
: > set-aside-entries.log
