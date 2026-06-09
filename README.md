# trailhead

trailhead is a management CLI and monorepo for three agent-native Claude Code plugins:
**lore**, **forge**, and **camp**. Each tool is self-contained and independently
adoptable — wire only what you need.

## Tools

### lore — agent-native project memory

lore gives Claude structured primitives for decisions, dead-ends, deferred items,
radar entries, and session notes. Every project becomes a compounding knowledge store.

Adopt lore on its own if you only want the knowledge-layer primitives — forge and
camp are siblings, not dependencies.

### forge — structured planning and execution workflows

forge gives Claude structured primitives for test-driven development, assumption
proving, code review, and the council/circle design process. It provides the
subagent-driven development flow.

Adopt forge on its own if you only want planning and execution workflows — lore and
camp are optional.

### camp — worktree and dev-environment orchestration

camp gives Claude structured primitives for managing git worktrees, provisioning
dev environments, and coordinating multi-repo workspaces.

Adopt camp on its own if you only want worktree and dev-environment orchestration.

## Install

> **Warning:** bare `pip install trailhead` installs only the manager — run
> `trailhead install` to wire tools into your Claude Code harness. Without
> `trailhead install`, no tools are active even after pip install succeeds.

```sh
pip install trailhead
trailhead install          # wire all tools (Step 5 / WS-12)
trailhead install --preset minimal   # wire lore only
```

## Development

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).
