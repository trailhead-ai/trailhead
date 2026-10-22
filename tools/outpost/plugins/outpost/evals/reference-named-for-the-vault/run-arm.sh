#!/usr/bin/env bash
# Run one arm of this case inside scripts/eval-sandbox and leave its captures and a completion marker.
#
# Usage: run-arm.sh <repo> <runs-root> <run-id> <variant> <arm>
#
# <arm> is baseline (the committed pre-change skill, arms/baseline-SKILL.md, and the committed pre-change
# ruleset, arms/baseline-system-prompt.md) or treatment (the live skill in the repo and
# arms/system-prompt.md, the operating brief plus the live rules.md). The instruction files are chosen
# here from the repo, never from the fixture.
#
# A variant with a next turn (the builder leaves prompt-2.md) runs both turns in one process through
# drive-turns.py (the sandbox cannot read the session store, so --resume is not available), recording the
# vault's sites/ listing between them.
#
# <runs-root>/ids.txt lists every run id of the batch, one per line; the other runs' directories, their
# TMPDIRs, the captures and ids.txt are denied for reading, so one run cannot read another. Run
# directories must live outside the home directory.
#
# Writes <runs-root>/run-<id>.capture.jsonl (and .capture-2.jsonl), run-<id>.stderr, run-<id>.after-1.txt
# (the vault's sites/ listing after the first turn) and, last, run-<id>.done holding the last exit code.
set -uo pipefail

repo="${1:?repo}"; root="${2:?runs-root}"; id="${3:?run-id}"; variant="${4:?variant}"; arm="${5:?arm}"
case_dir="$repo/tools/outpost/plugins/outpost/evals/reference-named-for-the-vault"
claude_bin="${CLAUDE_BIN:-$(readlink -f "$(command -v claude)")}"
uid="$(id -u)"
run="$root/run-$id"
tmp="/private/tmp/claude-$uid/run-$id"
mkdir -p "$tmp"

case "$arm" in
  baseline) skill="$case_dir/arms/baseline-SKILL.md"; system="$case_dir/arms/baseline-system-prompt.md" ;;
  treatment) skill=""; system="$case_dir/arms/system-prompt.md" ;;
  *) echo "unknown arm $arm" >&2; exit 2 ;;
esac

bash "$case_dir/fixtures/make-fixture-env.sh" "$run" "$variant" $skill >/dev/null || { echo 2 > "$root/run-$id.done"; exit 2; }

deny=""
while read -r other; do
  [ "$other" = "$id" ] && continue
  deny="$deny$root/run-$other:/private/tmp/claude-$uid/run-$other:$root/run-$other.capture.jsonl:$root/run-$other.capture-2.jsonl:"
done < "$root/ids.txt"
deny="$deny$root/ids.txt:$root/manifest.tsv:"

sandboxed() {
  # sandboxed <claude args...>: the confined command line, run from the run's project folder
  "$repo/scripts/eval-sandbox" "$run" -- \
    env -i $(cat "$run/run.env") HOME="$HOME" USER="$USER" TMPDIR="$tmp" \
    /bin/sh -c 'cd "$0" && exec "$@"' "$(cat "$run/cwd")" \
    "$claude_bin" -p --setting-sources project --plugin-dir "$run/plugin" \
    --append-system-prompt "$(cat "$system")" \
    --allowedTools "Bash,Read,Write,Edit,Skill" --output-format stream-json --verbose "$@"
}

export HOME_REAL="$HOME" DENY_EXTRA="$deny"
if [ -f "$run/prompt-2.md" ]; then
  python3 "$case_dir/drive-turns.py" \
    "$root/run-$id.capture.jsonl" "$root/run-$id.capture-2.jsonl" "$root/run-$id.after-1.txt" \
    "$run/xdg-state/lore/vaults/acme/sites" "$run/prompt.md" "$run/prompt-2.md" -- \
    "$repo/scripts/eval-sandbox" "$run" -- \
    env -i $(cat "$run/run.env") HOME="$HOME" USER="$USER" TMPDIR="$tmp" \
    /bin/sh -c 'cd "$0" && exec "$@"' "$(cat "$run/cwd")" \
    "$claude_bin" -p --setting-sources project --plugin-dir "$run/plugin" \
    --append-system-prompt "$(cat "$system")" \
    --allowedTools "Bash,Read,Write,Edit,Skill" --input-format stream-json \
    --output-format stream-json --verbose 2> "$root/run-$id.stderr"
  code=$?
else
  sandboxed "$(cat "$run/prompt.md")" > "$root/run-$id.capture.jsonl" 2> "$root/run-$id.stderr"
  code=$?
  (cd "$run/xdg-state/lore/vaults/acme/sites" && find . -mindepth 1 | sort) > "$root/run-$id.after-1.txt" 2>&1
fi
echo "$code" > "$root/run-$id.done"
