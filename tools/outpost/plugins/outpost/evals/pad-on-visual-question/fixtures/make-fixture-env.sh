#!/usr/bin/env bash
# Build the self-contained run directory one arm of this case runs in.
#
# Usage: make-fixture-env.sh <run-dir> <variant>
#
# EVAL_PYTHON3 names the interpreter the arm's bare `python3` resolves to; it
# defaults to the `python3` on this script's PATH and must be 3.11+ (the
# publisher imports tomllib), which a macOS /usr/bin/python3 is not.
#
# Variants (one per scenario):
#   visual-inside-workspace       cwd is a member folder of a throwaway camp workspace
#   visual-pad-cannot-be-made     same, with the workspace's sites/ made a symlink so a publish is refused
#   visual-outside-workspace      cwd is a plain project folder
#   not-visual-layout-vocabulary  plain project folder
#   typed-answer-after-pad        plain project folder
#   words-only-request            plain project folder
#   dispatched-agent-observed     plain project folder
#
# Produces, all under <run-dir>:
#   bin/python3   a link to EVAL_PYTHON3, the only thing run.env's PATH adds
#   plugin/       a copy of the outpost plugin for --plugin-dir, without evals/ and rules.md
#   home/ xdg-state/ xdg-config/   throwaway HOME and XDG roots (camp state is xdg-state/camp,
#                 outpost state is xdg-state/outpost, camp config is xdg-config/camp)
#   project/      the plain project folder (outside variants)
#   run.env       KEY=VALUE lines: the whole environment the arm's process runs with
#   cwd           one line: the directory the arm's process starts in
#   prompt.md     the scenario's brief, to pass as the user prompt
#
# Everything here is invented; no path resolves to real camp, Outpost or lore state.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
plugin_src="$(cd "$here/../../.." && pwd -P)"

run_dir="${1:?usage: make-fixture-env.sh <run-dir> <variant>}"
variant="${2:?usage: make-fixture-env.sh <run-dir> <variant>}"

brief="$here/brief-$variant.md"
if [ ! -f "$brief" ]; then
  echo "make-fixture-env.sh: unknown variant '$variant'" >&2
  exit 2
fi

python3="${EVAL_PYTHON3:-$(command -v python3 || true)}"
if [ -z "$python3" ] || ! "$python3" -c 'import tomllib' 2>/dev/null; then
  echo "make-fixture-env.sh: no python3 that can import tomllib (3.11+); set EVAL_PYTHON3" >&2
  exit 2
fi

mkdir -p "$run_dir"
run_dir="$(cd "$run_dir" && pwd -P)"
mkdir -p "$run_dir"/{bin,home,xdg-state,xdg-config,project}
ln -s "$python3" "$run_dir/bin/python3"

mkdir -p "$run_dir/plugin"
(cd "$plugin_src" && tar --exclude=./evals --exclude=./rules.md -cf - .) | (cd "$run_dir/plugin" && tar -xf -)

printf '# ledgerlight\n\nA small invoicing web app.\n' > "$run_dir/project/README.md"

camp_root="$run_dir/xdg-state/camp"
workspace="$camp_root/bramble/worktrees/ws-quill"
case "$variant" in
  visual-inside-workspace|visual-pad-cannot-be-made)
    mkdir -p "$workspace/app"
    printf '# ledgerlight\n\nA small invoicing web app.\n' > "$workspace/app/README.md"
    cwd="$workspace/app"
    ;;
  *)
    cwd="$run_dir/project"
    ;;
esac

if [ "$variant" = "visual-pad-cannot-be-made" ]; then
  mkdir -p "$run_dir/elsewhere"
  ln -s "$run_dir/elsewhere" "$workspace/sites"
fi

cat > "$run_dir/run.env" <<ENV
PATH=$run_dir/bin:/usr/bin:/bin
HOME=$run_dir/home
XDG_STATE_HOME=$run_dir/xdg-state
XDG_CONFIG_HOME=$run_dir/xdg-config
ENV
printf '%s\n' "$cwd" > "$run_dir/cwd"
cp "$brief" "$run_dir/prompt.md"

printf 'fixture env built at %s (variant %s, cwd %s)\n' "$run_dir" "$variant" "$cwd"
