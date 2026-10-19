#!/usr/bin/env bash
# Build the self-contained run directory one arm of this case runs in: a project folder
# outside any camp workspace, with a one-round pad already built by the real scratch_pad.py
# into throwaway state.
#
# Usage: make-fixture-env.sh <run-dir> <variant> [<skill-md>]
#
# <variant> is follow-up-same-design or follow-up-new-topic. <skill-md>, when given, is an
# absolute path to a file installed in place of the plugin copy's scratch-pad/SKILL.md; the
# runner passes only committed in-repo files here, never a path read from a fixture.
#
# <run-dir> must be missing or an empty directory, and its physical path may contain only
# [A-Za-z0-9._/-], because run.env holds it unquoted and the runner word-splits it. Either
# refusal exits 2 with nothing written.
#
# EVAL_PYTHON3 names the interpreter the arm's bare `python3` resolves to (absolute path or
# a bare name on this script's PATH; default the `python3` on this script's PATH). It must be
# 3.11+ and live outside the home directory, which a sandbox denies reads under.
#
# Produces, all under <run-dir>:
#   bin/python3   a link to the real path of EVAL_PYTHON3
#   plugin/       a copy of the outpost plugin without evals/, rules.md and __pycache__
#   home/ xdg-state/ xdg-config/   throwaway HOME and XDG roots; the pad "audit-filters" sits
#                 in xdg-state/outpost/pads/ with its first round
#   project/      the working directory
#   run.env cwd prompt.md
#
# Everything here is invented; no path resolves to real camp, Outpost or lore state.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
plugin_src="$(cd "$here/../../.." && pwd -P)"

run_dir="${1:?usage: make-fixture-env.sh <run-dir> <variant> [<skill-md>]}"
variant="${2:?usage: make-fixture-env.sh <run-dir> <variant> [<skill-md>]}"
skill_override="${3:-}"

brief="$here/brief-$variant.md"
if [ ! -f "$brief" ]; then
  echo "make-fixture-env.sh: unknown variant '$variant'" >&2
  exit 2
fi
if [ -n "$skill_override" ] && [ ! -f "$skill_override" ]; then
  echo "make-fixture-env.sh: skill file '$skill_override' is not a file" >&2
  exit 2
fi

if [ -e "$run_dir" ] && { [ ! -d "$run_dir" ] || [ -n "$(find "$run_dir" -mindepth 1 -print -quit)" ]; }; then
  echo "make-fixture-env.sh: run directory '$run_dir' exists and is not an empty directory" >&2
  exit 2
fi

case "$run_dir" in /*) probe="$run_dir" ;; *) probe="$PWD/$run_dir" ;; esac
tail=""
while [ ! -e "$probe" ]; do
  tail="/$(basename "$probe")$tail"
  probe="$(dirname "$probe")"
done
physical="$(cd "$probe" && pwd -P)$tail"
if ! printf '%s' "$physical" | grep -Eq '^[A-Za-z0-9._/-]+$'; then
  echo "make-fixture-env.sh: run directory '$run_dir' resolves to '$physical', which has characters outside [A-Za-z0-9._/-]" >&2
  exit 2
fi

python3="${EVAL_PYTHON3:-$(command -v python3 || true)}"
if [ -z "$python3" ] || ! "$python3" -I -c 'import tomllib' 2>/dev/null; then
  echo "make-fixture-env.sh: no python3 that can import tomllib (3.11+); set EVAL_PYTHON3" >&2
  exit 2
fi

mkdir -p "$run_dir"
run_dir="$(cd "$run_dir" && pwd -P)"
mkdir -p "$run_dir"/{bin,home,xdg-state,xdg-config,project}
real_python3="$("$python3" -I -c 'import os,sys; print(os.path.realpath(sys.executable))')"
ln -s "$real_python3" "$run_dir/bin/python3"

mkdir -p "$run_dir/plugin"
(cd "$plugin_src" && tar --exclude=./evals --exclude=./rules.md --exclude=__pycache__ -cf - .) | (cd "$run_dir/plugin" && tar -xf -)
if [ -n "$skill_override" ]; then
  cp "$skill_override" "$run_dir/plugin/skills/scratch-pad/SKILL.md"
fi

printf '# ledgerlight\n\nA small invoicing web app.\n' > "$run_dir/project/README.md"

cat > "$run_dir/run.env" <<ENV
PATH=$run_dir/bin:/usr/bin:/bin
HOME=$run_dir/home
XDG_STATE_HOME=$run_dir/xdg-state
XDG_CONFIG_HOME=$run_dir/xdg-config
ENV
printf '%s\n' "$run_dir/project" > "$run_dir/cwd"
cp "$brief" "$run_dir/prompt.md"

(
  cd "$run_dir/project"
  env -i PATH="$run_dir/bin:/usr/bin:/bin" HOME="$run_dir/home" \
    XDG_STATE_HOME="$run_dir/xdg-state" XDG_CONFIG_HOME="$run_dir/xdg-config" \
    "$run_dir/bin/python3" "$run_dir/plugin/skills/scratch-pad/scratch_pad.py" \
    --pad audit-filters --round "$here/round-1.json" > /dev/null
)

printf 'fixture env built at %s (variant %s, cwd %s/project)\n' "$run_dir" "$variant" "$run_dir"
