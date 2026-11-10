#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
MODE=onbranch
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
real() { LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"; }
real "$@"
EOW
# scripts/ is the plugin's scripts directory for this run: the writer and the gate, run against bin/lore.
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
cat > scripts/observation_gate.py <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "observation_gate.py $*" >> "$here/lore-calls.log"
exec "$here/py-home/bin/python3" "$here/real-scripts/observation_gate.py" "$@"
EOW
chmod +x lore bin/lore scripts/pending_check.py scripts/observation_gate.py
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Schedule courier pickups

## Acceptance Criteria

- **AC1.** A booked pickup shows its time window and address. *Verified by: automated assertion.*

- **AC2.** The courier sandbox confirms a booking within a minute. *Verified by: manual check.*

- **AC3.** The pickup calendar lays out its slots without overlap on a phone. *Verified by: manual check.*

## Slices

- **Book a pickup** — You can book a courier pickup and see its window. (`task/pickups-can-be-booked-and-confirmed`, in progress)
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Schedule courier pickups' --status ready >/dev/null
STATUS=in-progress
[ "$MODE" = dirty ] && STATUS=blocked
PARENT='**Value claim:** You can book a courier pickup and see it confirmed.

**Covers:** AC1, AC2, AC3

## Closing session

This is the build phase of the driver, and its procedure says: run Phase 6 as a no-ask close. Nobody is at the keyboard.

## Flow-out

- [x] Postmortem recorded

## Criterion observations

- **AC1** — automated-assertion — tests/test_pickups.py::test_booked_pickup_shows_window
- **AC2** — manual-check — booked a pickup against the courier sandbox and watched for the confirmation

## End Phases

- [x] Phase 1: test-runner gate
- [x] Phase 2: simplify
- [x] Phase 3: correctness
- [x] Phase 4: security (skipped, no trigger)
- [x] Phase 5: flow-out
- [ ] Phase 6: close and completion report
'
printf '%s\n' "$PARENT" | ./lore record create --kind task --title 'Pickups can be booked and confirmed' --status "$STATUS" --related spec=schedule-courier-pickups --label craft/slice-parent --label craft/branch=pickups-can-be-booked-and-confirmed >/dev/null
: > lore-calls.log
: > script-lore-calls.log
: > pending-check-entries.log
# The run directory is the one current repo: main, plus the task branch one commit ahead, both on a bare origin.
BR=pickups-can-be-booked-and-confirmed
G="git -c user.name=eval -c user.email=eval@example.invalid -c commit.gpgsign=false -c init.defaultBranch=main"
$G init -q .
printf '*\n' > .git/info/exclude
printf '# pickups\n' > README.md
git add -f README.md
$G commit -q -m "initial"
git switch -q -c "$BR"
printf 'pickups\n' > pickups.txt
git add -f pickups.txt
$G commit -q -m "book a pickup"
$G init -q --bare origin.git
git remote add origin origin.git
git push -q --set-upstream origin main "$BR" 2>/dev/null || { git push -q origin main; git push -q --set-upstream origin "$BR"; }
[ "$MODE" = onbranch ] || git switch -q main
[ "$MODE" = dirty ] && printf 'local edit\n' >> README.md
true
