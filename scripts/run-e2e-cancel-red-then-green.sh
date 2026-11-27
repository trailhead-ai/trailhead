#!/usr/bin/env bash
# Runs the gateway cancel scenario (test_cancel_scenario_*) twice: first against the commits before the cancel
# slice (all four tests should fail: `run cancel` is an unknown subcommand, and
# `run cancel` or `run view --log` is refused as unknown), then against this branch (all should pass). Then runs Outpost's own
# socket-bound startup tests once on this branch, which include "boots with run.cancel declared".
# Run it in a normal terminal (not a sandboxed session or a `!` command): the suite needs unix sockets and sudo.
#
# Before running:
#   1. Re-provision andromeda, so the act wrapper runs act under `setpriv --pdeathsig KILL` (a cancelled run's
#      act then dies when sudo is killed at the end of the grace period):
#        sudo ~/.local/share/chezmoi/scripts/provision-outpost-runner
#   2. Stop your Outpost daemon (it holds the engine filter socket):
#        systemctl --user stop outpost.service      # and start it again afterwards
#   3. This shell must hold the outpost-run group (the suite's preflight says what to do if not).
#
# The green run prints how long the cancel of the running job took (about 5s plus a little means the container
# removal did not stop act and the grace period's kill did; sooner means act exited on its own).
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAILHEAD="$(cd "$HERE/.." && pwd)"
OUTPOST="${OUTPOST:-$TRAILHEAD/../outpost}"
BASE_OUTPOST=78c1effe
BASE_TRAILHEAD=cef24f133
WRAPPER=/usr/local/lib/outpost-runner/act-run
TESTFILE=trailhead/tests/test_gateway_e2e_andromeda.py
PYTEST_ARGS=(-n 0 -s -rf --tb=short "$TESTFILE" -k cancel_scenario)

if ! grep -q 'setpriv --pdeathsig' "$WRAPPER" 2>/dev/null; then
  echo "PREFLIGHT FAILED: the installed act wrapper $WRAPPER is missing or does not use 'setpriv --pdeathsig'." >&2
  echo "Re-provision first: sudo ~/.local/share/chezmoi/scripts/provision-outpost-runner" >&2
  exit 2
fi

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/e2e-cancel-red-XXXXXX")"
cleanup() {
  git -C "$OUTPOST" worktree remove --force "$SCRATCH/outpost" 2>/dev/null
  git -C "$TRAILHEAD" worktree remove --force "$SCRATCH/trailhead" 2>/dev/null
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

git -C "$OUTPOST" worktree add --detach "$SCRATCH/outpost" "$BASE_OUTPOST" >/dev/null || exit 2
git -C "$TRAILHEAD" worktree add --detach "$SCRATCH/trailhead" "$BASE_TRAILHEAD" >/dev/null || exit 2
ln -s "$OUTPOST/node_modules" "$SCRATCH/outpost/node_modules"

cd "$TRAILHEAD" || exit 2
N_CANCEL=$(TRAILHEAD_GATEWAY_E2E=1 .venv/bin/python -m pytest -n 0 --collect-only -q -k cancel_scenario "$TESTFILE" | grep -c '::')
EXPECT_FAIL=$((N_CANCEL - 1))
RED_XML="$SCRATCH/red.xml"
RED_LOG="$SCRATCH/red.log"

echo "=== RED: tests from this branch, Outpost at $BASE_OUTPOST and trailhead at $BASE_TRAILHEAD ==="
echo "    expecting $EXPECT_FAIL failures of $N_CANCEL cancel_scenario tests (all but the credential scan, clean on any commit that leaks nothing), and no errors"
TRAILHEAD_GATEWAY_E2E=1 \
TRAILHEAD_GATEWAY_E2E_OUTPOST="$SCRATCH/outpost" \
TRAILHEAD_GATEWAY_E2E_TRAILHEAD="$SCRATCH/trailhead" \
  .venv/bin/python -m pytest "${PYTEST_ARGS[@]}" --junitxml="$RED_XML" 2>&1 | tee "$RED_LOG"

# Each failing test must have failed because this branch's command surface is missing: `run cancel` unknown, or
# `run view --log` unknown. The one test allowed to pass is the credential scan, clean on any commit that leaks nothing.
red_verdict_text="$(.venv/bin/python -m trailhead.tests._gateway_e2e_support red-verdict --xml "$RED_XML" --log "$RED_LOG" \
  --expect-fail "$EXPECT_FAIL" --marker "invalid choice: 'cancel'" --marker "unrecognized arguments: --log" \
  --per-failure --allow-pass no_secret_or_token)"
red_ok=$?
echo "RED VERDICT: $red_verdict_text"

cleanup
trap - EXIT

echo
echo "=== GREEN: this branch ==="
TRAILHEAD_GATEWAY_E2E=1 .venv/bin/python -m pytest "${PYTEST_ARGS[@]}"
green=$?

echo
echo "=== Outpost socket-bound startup tests (this branch) ==="
(cd "$OUTPOST" && npx vitest run tests/startup-check.test.ts)
startup=$?

echo
echo "red verdict: $red_verdict_text; green run exit: $green (expected 0); outpost startup-check exit: $startup (expected 0)"
echo "opt-in tiers this run skipped: sandbox denial probes (run-e2e-sandbox-denials.sh), run scenario (run-e2e-red-then-green.sh), ritual harness (run-e2e-ritual-harness-red-then-green.sh), tool-server header (run-e2e-tool-server-auth-header.sh), UI open in a real browser (run-e2e-ui-open.sh)"
[ "$red_ok" -eq 0 ] && [ "$green" -eq 0 ] && [ "$startup" -eq 0 ]
