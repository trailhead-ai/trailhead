#!/usr/bin/env bash
# ONE-SHOT dogfood-cutover script — NOT durable migration logic.
#
# Run this ONCE on a machine that was wired under the old per-tool marketplace
# layout (trailhead-lore / trailhead-camp / trailhead-craft each registered
# separately). It removes the stale per-tool registrations and the old
# composed/<tool>/ plugin dirs, then prints the command to re-register under
# the consolidated trailhead marketplace.
#
# After running this script, run:
#   trailhead install
# (or your wire entrypoint) to re-register under the consolidated trailhead
# marketplace, then restart your Claude Code session.
set -euo pipefail

echo "==> Removing stale per-tool marketplace registrations (tolerate 'not found')..."

for mkt in trailhead-lore trailhead-camp trailhead-craft; do
    if claude plugin marketplace remove "$mkt" 2>&1; then
        echo "    removed: $mkt"
    else
        echo "    not found (ok): $mkt"
    fi
done

echo ""
echo "==> Removing old composed/<tool>/ plugin directories..."

# Determine the trailhead state dir (default: ~/Library/Application Support/trailhead)
STATE_DIR="${TRAILHEAD_STATE_DIR:-$HOME/Library/Application Support/trailhead}"
COMPOSED_DIR="$STATE_DIR/composed"

for tool in lore camp craft; do
    tool_dir="$COMPOSED_DIR/$tool"
    if [ -d "$tool_dir" ]; then
        # Guard: only remove the per-tool subdir, not composed/ itself or composed/plugins/
        echo "    removing: $tool_dir"
        rm -rf "$tool_dir"
    else
        echo "    not present (ok): $tool_dir"
    fi
done

echo ""
echo "==> Done."
echo ""
echo "Next steps:"
echo "  1. Run your wire entrypoint (e.g. 'trailhead install') to re-register"
echo "     all tools under the consolidated 'trailhead' marketplace."
echo "  2. Restart your Claude Code session so the new registrations take effect."
