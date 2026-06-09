#!/usr/bin/env bash
# pre-commit-regen-indices.sh — regenerate per-folder _index.md files and
# stage them so they ride along with the current commit.
#
# Shipped as part of the lore plugin. NOT installed directly into a vault's
# .git/hooks/ — install-vault-hooks.sh generates a wrapper there that sets
# LORE_PLUGIN_ROOT and LORE_VAULT, then delegates here.
#
# After regeneration, any _index.md files that changed are git-added with a
# scoped `git add --` so only the known index paths are staged (not `git add -A`).
# This prevents accidentally staging unrelated working-tree changes.
#
# Exits 0 always — a regen failure is logged but does not block the commit.
# Index staleness is recoverable; a blocked commit is not.

set -euo pipefail

if [ -z "${LORE_PLUGIN_ROOT:-}" ]; then
    if [ -n "${LORE_GUARD_STRICT:-}" ]; then
        echo "lore regen-indices: LORE_PLUGIN_ROOT not set — reinstall with \`lore init\`; commit blocked" >&2
        exit 1
    fi
    echo "lore regen-indices: LORE_PLUGIN_ROOT not set — skipping index regen" >&2
    exit 0
fi

REGEN_SCRIPT="$LORE_PLUGIN_ROOT/scripts/regenerate_indices.py"

if [ ! -f "$REGEN_SCRIPT" ]; then
    if [ -n "${LORE_GUARD_STRICT:-}" ]; then
        echo "lore regen-indices: script not found at $REGEN_SCRIPT — reinstall with \`lore init\`; commit blocked" >&2
        exit 1
    fi
    echo "lore regen-indices: script not found at $REGEN_SCRIPT — skipping" >&2
    exit 0
fi

# LORE_VAULT is set by the installer wrapper to the vault path.
VAULT="${LORE_VAULT:-}"
if [ -z "$VAULT" ]; then
    echo "lore regen-indices: LORE_VAULT not set — skipping index regen" >&2
    exit 0
fi

# Run the regenerator. Capture output: one vault-relative path per changed index.
regen_out=""
if ! regen_out="$(LORE_VAULT="$VAULT" python3 "$REGEN_SCRIPT" 2>&1)"; then
    echo "lore regen-indices: regenerate_indices.py failed:" >&2
    echo "$regen_out" | sed 's/^/  /' >&2
    exit 0
fi

# Stage each changed index file (scoped add — not git add -A).
if [ -n "$regen_out" ]; then
    while IFS= read -r rel_path; do
        [ -z "$rel_path" ] && continue
        abs_path="$VAULT/$rel_path"
        if [ -f "$abs_path" ]; then
            git -C "$VAULT" add -- "$abs_path"
        fi
    done <<< "$regen_out"
fi

exit 0
