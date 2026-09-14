#!/usr/bin/env bash
# Builds a self-contained scratch fixture environment for the
# record-link-rendering eval case: copies the vault trees and templates
# into a run directory and substitutes the run directory's own absolute
# path in for the FIXTURE_ROOT placeholder, so the vault listing and task
# prompt name real, resolvable paths.
#
# The standard-path vault (`gearshed`) is laid down at <run-dir>/vaults/gearshed.
# The caller MUST set LORE_STATE_DIR=<run-dir> in each arm process's own
# environment so the rule's vaults-root resolution lands on exactly
# <run-dir>/vaults — without it, gearshed is not a direct child of the
# resolved vaults root and the fixture no longer exercises that clause.
#
# The second argument selects the rendering context the task prompt asks
# for: `paragraph` (the identifiers land in prose) or `table` (the
# identifiers land in table cells). Both conditions share the exact same
# five-record set and vault listing — only the requested output shape
# differs — so a divergence in how a run renders record 1 cannot come from
# the records themselves differing. An unrecognized condition is refused,
# never silently defaulted.
set -euo pipefail

if [ $# -ne 2 ]; then
    echo "usage: $0 <run-dir> <table|paragraph>" >&2
    exit 1
fi

CONDITION="$2"
case "$CONDITION" in
    paragraph) TEMPLATE="task.md.in" ;;
    table) TEMPLATE="table-task.md.in" ;;
    *)
        echo "error: unrecognized condition '$CONDITION' — expected 'table' or 'paragraph'" >&2
        exit 1
        ;;
esac

RUN_DIR="$(mkdir -p "$1" && cd "$1" && pwd)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp -R "$HERE/vaults" "$RUN_DIR/vaults"
cp -R "$HERE/other-storage" "$RUN_DIR/other-storage"
cp -R "$HERE/untracked" "$RUN_DIR/untracked"

sed "s#FIXTURE_ROOT#$RUN_DIR#g" "$HERE/vault-ls.txt.in" > "$RUN_DIR/vault-ls.txt"
sed "s#FIXTURE_ROOT#$RUN_DIR#g" "$HERE/$TEMPLATE" > "$RUN_DIR/task.md"

echo "$RUN_DIR"
