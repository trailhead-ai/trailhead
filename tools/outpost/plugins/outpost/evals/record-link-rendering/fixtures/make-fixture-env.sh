#!/usr/bin/env bash
# Builds a self-contained scratch fixture environment for the
# record-link-rendering eval case: copies the vault trees and templates
# into a run directory and substitutes the run directory's own absolute
# path in for the FIXTURE_ROOT placeholder, so the vault listing and task
# prompt name real, resolvable paths.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $0 <run-dir>" >&2
    exit 1
fi

RUN_DIR="$(mkdir -p "$1" && cd "$1" && pwd)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp -R "$HERE/vaults-root" "$RUN_DIR/vaults-root"
cp -R "$HERE/other-storage" "$RUN_DIR/other-storage"
cp -R "$HERE/untracked" "$RUN_DIR/untracked"

sed "s#FIXTURE_ROOT#$RUN_DIR#g" "$HERE/vault-ls.txt.in" > "$RUN_DIR/vault-ls.txt"
sed "s#FIXTURE_ROOT#$RUN_DIR#g" "$HERE/task.md.in" > "$RUN_DIR/task.md"

echo "$RUN_DIR"
