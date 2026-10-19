#!/usr/bin/env bash
# Run one arm of this case inside scripts/eval-sandbox and leave its capture and a completion marker.
#
# Usage: run-arm.sh <repo> <runs-root> <run-id> <variant> <arm>
#
# <arm> is baseline (the committed pre-change skill, arms/baseline-SKILL.md) or treatment (the
# live skill in the repo). Both use arms/system-prompt.md (the operating brief plus the live rules.md) as the system prompt. The instruction
# files are chosen here from the repo, never from the fixture.
#
# <runs-root>/ids.txt lists every run id of the batch, one per line; the other runs' directories,
# their TMPDIRs, the captures and ids.txt are denied for reading, so one run cannot read another.
# Run directories must live outside the home directory.
#
# Writes <runs-root>/run-<id>.capture.jsonl, run-<id>.stderr and, last, run-<id>.done holding the exit code.
set -uo pipefail

repo="${1:?repo}"; root="${2:?runs-root}"; id="${3:?run-id}"; variant="${4:?variant}"; arm="${5:?arm}"
case_dir="$repo/tools/outpost/plugins/outpost/evals/follow-up-round-on-an-open-pad"
claude_bin="${CLAUDE_BIN:-$(readlink -f "$(command -v claude)")}"
uid="$(id -u)"
run="$root/run-$id"
tmp="/private/tmp/claude-$uid/run-$id"
mkdir -p "$tmp"

case "$arm" in
  baseline) skill="$case_dir/arms/baseline-SKILL.md" ;;
  treatment) skill="" ;;
  *) echo "unknown arm $arm" >&2; exit 2 ;;
esac

bash "$case_dir/fixtures/make-fixture-env.sh" "$run" "$variant" $skill >/dev/null || { echo 2 > "$root/run-$id.done"; exit 2; }

deny=""
while read -r other; do
  [ "$other" = "$id" ] && continue
  deny="$deny$root/run-$other:/private/tmp/claude-$uid/run-$other:$root/run-$other.capture.jsonl:"
done < "$root/ids.txt"
deny="$deny$root/ids.txt:$root/manifest.tsv:"

HOME_REAL="$HOME" DENY_EXTRA="$deny" HOME="$HOME" "$repo/scripts/eval-sandbox" "$run" -- \
  env -i $(cat "$run/run.env") HOME="$HOME" USER="$USER" TMPDIR="$tmp" \
  /bin/sh -c 'cd "$0" && exec "$@"' "$(cat "$run/cwd")" \
  "$claude_bin" -p --setting-sources project --plugin-dir "$run/plugin" \
  --append-system-prompt "$(cat "$case_dir/arms/system-prompt.md")" \
  --allowedTools "Bash,Read,Write,Edit,Skill" --output-format stream-json --verbose \
  "$(cat "$run/prompt.md")" > "$root/run-$id.capture.jsonl" 2> "$root/run-$id.stderr"
code=$?
echo "$code" > "$root/run-$id.done"
