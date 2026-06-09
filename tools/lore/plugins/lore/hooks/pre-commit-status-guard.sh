#!/usr/bin/env bash
# pre-commit-status-guard.sh — reject commits that introduce non-canonical status values.
#
# Shipped as part of the lore plugin. This script is NOT installed directly
# into a vault's .git/hooks/ — install-vault-hooks.sh generates a thin wrapper
# there that sets LORE_PLUGIN_ROOT and delegates here.
#
# Validates staged .md files (diff-filter=ACM: Added, Copied, Modified).
# Skips deleted files and non-.md staged files.
# Exits 0 (no-op) when no .md files are staged.
# Exits 1 and prints offending file+status when any violation is found.

set -euo pipefail

# LORE_PLUGIN_ROOT is set by the installed wrapper — it points at the
# plugins/lore/ directory of the installed lore plugin.
#
# LORE_GUARD_STRICT is baked into installed hooks by install-vault-hooks.sh.
# When set, a missing/moved plugin root is a hard failure (fail closed) rather
# than a silent skip. Standalone invocations (no STRICT) remain lenient.

if [ -z "${LORE_PLUGIN_ROOT:-}" ]; then
    if [ -n "${LORE_GUARD_STRICT:-}" ]; then
        echo "lore status guard: LORE_PLUGIN_ROOT not set — reinstall with \`lore init\` or the hook installer; commit blocked" >&2
        exit 1
    fi
    echo "pre-commit-status-guard: LORE_PLUGIN_ROOT not set — skipping guard" >&2
    exit 0
fi

VALIDATOR="$LORE_PLUGIN_ROOT/scripts/status_validator.py"

if [ ! -f "$VALIDATOR" ]; then
    if [ -n "${LORE_GUARD_STRICT:-}" ]; then
        echo "lore status guard: validator not found at $VALIDATOR — reinstall with \`lore init\` or the hook installer; commit blocked" >&2
        exit 1
    fi
    echo "pre-commit-status-guard: validator not found at $VALIDATOR — skipping" >&2
    exit 0
fi

# Collect staged .md files (skip deletions), NUL-delimited to handle any filename.
staged_files=()
while IFS= read -r -d '' file; do
    case "$file" in
        *.md) staged_files+=("$file") ;;
    esac
done < <(git diff --cached -z --name-only --diff-filter=ACM 2>/dev/null || true)

if [ "${#staged_files[@]}" -eq 0 ]; then
    exit 0
fi

# Validate STAGED blob content (not working-tree files) so that fixing the
# working copy without re-staging cannot fool the guard.
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

validator_exit=0
violations=()

for f in "${staged_files[@]}"; do
    tmp_file="$tmp_dir/staged.md"
    if ! git show ":$f" > "$tmp_file" 2>/dev/null; then
        # File is staged but blob read failed — treat as unreadable, skip.
        continue
    fi
    rc=0
    raw_out="$(python3 "$VALIDATOR" "$tmp_file" 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        validator_exit=1
        # Extract the status/type portion from the validator output and
        # reattach the original staged path so the user sees the real filename.
        detail="$(echo "$raw_out" | grep -v '^status-validator' | sed "s|$tmp_file|$f|g" | sed '/^[[:space:]]*$/d')"
        violations+=("$detail")
    fi
done

if [ "$validator_exit" -ne 0 ]; then
    echo "" >&2
    echo "pre-commit rejected: non-canonical status value(s) found:" >&2
    echo "" >&2
    for v in "${violations[@]}"; do
        echo "$v" >&2
    done
    echo "" >&2
    echo "See the vault's glossary.md for the canonical status vocabulary." >&2
    echo "" >&2
    exit 1
fi

exit 0
