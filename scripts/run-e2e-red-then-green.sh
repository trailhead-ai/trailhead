#!/usr/bin/env bash
# Runs the gateway run scenario twice: first against the commits before the run slice (every run
# assertion should fail, mostly at `run start` being unknown), then against this branch (all should pass).
# Run it in a normal terminal (not a sandboxed session or a `!` command): the suite needs unix sockets and sudo.
#
# Before running: your Outpost daemon must be stopped (it holds the engine filter socket):
#   systemctl --user stop outpost.service      # and start it again afterwards
# and this shell must hold the outpost-run group (the suite's preflight says what to do if not).
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAILHEAD="$(cd "$HERE/.." && pwd)"
OUTPOST="${OUTPOST:-$TRAILHEAD/../outpost}"
BASE_OUTPOST=124c372b
BASE_TRAILHEAD=e7f48765
PYTEST_ARGS=(-n 0 -rf --tb=short trailhead/tests/test_gateway_e2e_andromeda.py)

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/e2e-red-XXXXXX")"
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
TESTFILE=trailhead/tests/test_gateway_e2e_andromeda.py
N_RUN=$(TRAILHEAD_GATEWAY_E2E=1 .venv/bin/python -m pytest -n 0 --collect-only -q -k run_scenario "$TESTFILE" | grep -c '::')
EXPECT_FAIL=$((N_RUN - 2))   # every run_scenario test but the sudo one and the credential scan (clean on any commit that leaks nothing) depends on the run slice
RED_XML="$SCRATCH/red.xml"
RED_LOG="$SCRATCH/red.log"

echo "=== RED: tests from this branch, Outpost at $BASE_OUTPOST and trailhead at $BASE_TRAILHEAD ==="
echo "    expecting $EXPECT_FAIL failures of $N_RUN run_scenario tests, and no errors"
TRAILHEAD_GATEWAY_E2E=1 \
TRAILHEAD_GATEWAY_E2E_OUTPOST="$SCRATCH/outpost" \
TRAILHEAD_GATEWAY_E2E_TRAILHEAD="$SCRATCH/trailhead" \
  .venv/bin/python -m pytest "${PYTEST_ARGS[@]}" -k run_scenario --junitxml="$RED_XML" 2>&1 | tee "$RED_LOG"

VERDICT=(.venv/bin/python -m trailhead.tests._gateway_e2e_support red-verdict --xml "$RED_XML" --log "$RED_LOG" --expect-fail "$EXPECT_FAIL" --marker "invalid choice: 'start'")
red_verdict_text="$("${VERDICT[@]}")"
red_ok=$?
echo "RED VERDICT: $red_verdict_text"

cleanup
trap - EXIT

echo
echo "=== GREEN: this branch ==="
TRAILHEAD_GATEWAY_E2E=1 .venv/bin/python -m pytest "${PYTEST_ARGS[@]}"
green=$?

echo
echo "red verdict: $red_verdict_text; green run exit: $green (expected 0)"
echo "opt-in tiers this run skipped: sandbox denial probes (run-e2e-sandbox-denials.sh), cancel scenario (trailhead/scripts/run-e2e-cancel-red-then-green.sh), ritual harness (run-e2e-ritual-harness-red-then-green.sh), tool-server header (run-e2e-tool-server-auth-header.sh), UI open in a real browser (run-e2e-ui-open.sh)"
[ "$red_ok" -eq 0 ] && [ "$green" -eq 0 ]
