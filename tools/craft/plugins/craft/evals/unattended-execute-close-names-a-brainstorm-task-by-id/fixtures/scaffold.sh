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

- [x] Touched area/subsystem profiles updated with what changed
- [x] Prover-validated assumptions captured as session candidates (durable at flush)
- [x] New decisions / lessons / follow-ups surfaced during the build recorded

## Criterion observations

- **AC1** — automated-assertion — test_csv.py::test_export_lists_every_invoice

## End Phases

- [x] Phase 1: test-runner gate
- [x] Phase 2: simplify
- [x] Phase 3: correctness
- [x] Phase 4: security (skipped, no trigger)
- [x] Phase 5: flow-out
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
./lore record update task/write-the-csv-exporter --status done >/dev/null
# The run's repository: this directory, with a bare origin that holds the base branch only.
real_git=""
for c in /Library/Developer/CommandLineTools/usr/bin/git /opt/homebrew/bin/git /usr/local/bin/git /usr/bin/git; do
  [ -x "$c" ] && { real_git="$c"; break; }
done
export GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@e.x GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@e.x
G="git -c commit.gpgsign=false -c init.defaultBranch=main"
git init -q --bare origin.git
$G init -q .
git remote add origin "$PWD/origin.git"
printf '# Invoices\n' > README.md
git add README.md
$G commit -q -m "chore: base"
git push -q origin main
git remote set-head origin main >/dev/null
git checkout -q -b export-invoices-as-csv
mkdir export
printf 'def write_rows(invoices):\n    return [(i.number, i.total) for i in invoices]\n' > export/csv.py
git add export/csv.py
$G commit -q -m "feat: write the invoice export"
# The runner's sandbox on macOS cannot run the platform git shim; ./git execs a real git by absolute path.
printf '#!/bin/sh\nexec %s "$@"\n' "$real_git" > git
chmod +x git
: > lore-calls.log
: > script-lore-calls.log
: > pending-check-entries.log
# The session's id reaches the script the way it does in a live session: through the environment.
cat > scripts/found_work.py <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "found_work.py $*" >> "$here/lore-calls.log"
CLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee PATH="$here/bin:$PATH" exec "$here/py-home/bin/python3" "$here/real-scripts/found_work.py" "$@"
EOW
cat > decision.json <<'EOJ'
{"decision":"brainstorm","parent":"task/export-invoices-as-csv","spec":"spec/export-invoices","finding":{"title":"Streaming export design","what":"Accounts with millions of invoices need a streaming export; no approach is agreed and it spans storage, queueing and the API.","where":["export/"],"found":"re-review"},"reason":"too open for any slice"}
EOJ
./scripts/found_work.py decide --session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee < decision.json >/dev/null
rm decision.json
: > lore-calls.log
: > script-lore-calls.log
