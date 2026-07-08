# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.
trailhead is driven by agents; you are likely one.

## Read this first — design axioms

The project's design axioms are binding constraints, not background. They live in a
harness-neutral doc so every harness can load them; for Claude Code this file
imports it:

@docs/vision.md

The short version: (1) **harness-agnostic** — no harness-specific code outside
`trailhead/harness/<name>.py`; (2) **take full advantage of the harness** by
widening the seam, not special-casing the core; (3) **design for full adoption,
support vanilla usage**; (4) **full basedir/XDG paths on every OS incl. macOS** —
always go through `trailhead/paths.py`; (5) **agents are the primary developers** —
write for agent comprehension; (6) **trailhead is built using trailhead** — never
let tests/experiments touch the live install.

## What this repo is

`trailhead` is a **monorepo** shipping a harness-agnostic Python management CLI plus
six agent-plugins. The CLI *composes* and *wires* selected plugin capabilities into
whatever AI code harness you use; the plugins themselves are the agent-facing
product (skills, subagents, hooks). **install = clone the repo** — there is no
remote fetch, no SHA-pinning manifest, no pip step required: the checkout IS the
source.

- `trailhead/` — the management CLI package (harness-agnostic core).
- `trailhead/harness/` — the harness seam (`base.py`) + per-harness impls
  (`claude_code.py` today). Adding a harness = implement the interface + register
  it; zero changes to the shared install/compose/wire path (Axiom 1).
- `tools/<name>/` — the six plugins: `lore`, `camp`, `craft`, `portage`, `landing`
  (+ `outpost` is forward-declared, not wired).
- `bin/trailhead` — git-only entry point (puts repo root on `sys.path`, no pip needed).
- `trailhead/docs/` — authoritative prose specs per seam. `docs/vision.md` — the axioms.

User-facing framing (README): **lore** (agent project memory) is the entry point;
**camp** (multi-repo worktree orchestration) and **craft** (dev rituals: plan / TDD
execute / review / council) are siblings; **portage** (PR lifecycle) and **landing**
(deploy soak) depend on the `trailhead.vcs` library. `camp` and `lore` also ship
standalone CLIs so they work outside a full install (Axiom 3).

## Commands

Requires **Python 3.11+**, **zero third-party runtime deps** (stdlib only; system
`python3` may be too old — `direnv` provisions a 3.11+ `.venv` carrying just pytest).

```sh
pip install -e .                  # editable install (optional; bin/trailhead works without it)
python -m pytest                  # whole suite (root pyproject testpaths span CLI + all tool tests)
python -m pytest trailhead/tests/test_compose.py             # one CLI test file
python -m pytest trailhead/tests/test_paths.py::TestMacosBranch  # one class/test
python -m pytest tools/lore/tests                            # one tool's suite

bin/trailhead install             # auto-detect harness, install all plugins + camp/lore CLIs
bin/trailhead install --harness claude_code --plugin lore --plugin craft
bin/trailhead doctor              # read-only health roll-up
bin/trailhead uninstall           # remove the whole install (keeps your data)
```

There is no separate lint step configured in-repo. Path resolvers accept injected
`platform=` / `env=` — use those plus `tmp_path` so tests never touch real
`~/.config`/`~/Library` data (Axiom 6).

## CLI architecture (the part that spans files)

`trailhead install` runs a config-driven, non-interactive pipeline
(`trailhead/install.py`): **detect** harnesses → **resolve config** (file + CLI
overrides) into `{harness: [plugins → subagents/skills + overrides]}` → for each
harness **compose + wire** the selection under the wire lock → build the camp/lore
**CLI shims** (additive; never edits your shell rc — prints `eval "$(… shellenv)"`)
→ print summary. The pieces, and why they're separate:

- **`install_config.py`** — resolves `config/default.toml` (+ `--config`) with CLI
  overrides (`--harness`, `--plugin`, `--no-camp/--no-lore`). A bare plugin string
  expands to all its subagents+skills; map form selects by name; override form
  points a name at a custom file/dir. (Replaced the old preset/SHA-manifest model.)
- **`harness/base.py`** — the harness seam (Axioms 1 & 2). `Harness` subclasses
  implement detect + the register/install/rewire/unregister tail; the shared core
  never branches on a harness name. Each harness composes into its OWN root
  (`state_dir/composed/<name>/`) so multiple harnesses never collide.
- **`capabilities.py`** — per-tool `capabilities.toml` loader. The capability-GROUP
  concept is **gone**: selection is per-subagent/per-skill by NAME, and the
  selectable inventory is **discovered on disk by convention** (`agents/<name>.md`,
  `skills/<name>/SKILL.md`), never hand-listed. The manifest only declares the
  always-on set: `base` dirs + optional `hooks_json`.
- **`compose.py`** — pure planner (`compose_plan`, stats override paths but writes
  nothing) + sole writer (`apply_plan`). Composing = copying selected sources into a
  dest plugin dir. Always includes `.claude-plugin/`, every `base` dir, and the
  `hooks_json` dir. Two different srcs → same dest is a `CollisionError` raised
  *before any write*. Dual-end confinement: every src under the plugin root, every
  dest under the dest dir.
- **`wire.py`** — harness-agnostic per-tool orchestrator: compose into a **staging
  dir**, **atomic promote** into the live dest, record promotion, then ONCE delegate
  the registration tail to the injected `Harness`. **Best-effort sequential**: a
  failure on tool N leaves 0…N-1 wired and names it (`WireError(tool, stage, cause)`);
  no full rollback. Knows nothing about `claude plugin` or `marketplace.json`.
- **`paths.py`** — OS-aware config/state/cache resolver. Resolvers are **pure**
  (never create dirs — use `ensure_dir`); honor `XDG_*` and per-app
  `<APP>_STATE_DIR`-style overrides; full basedir/XDG on every OS incl. macOS, with
  a read-only legacy `~/Library` fallback (Axiom 4; see `trailhead/docs/paths.md`).
- **`vcs/`** — provider-agnostic VCS seam (`repos`/`pr`/`ci`/`deploy` surfaces) that
  portage and landing build on. GitHub-backed today; designed so a GitLab backend
  maps onto the same method set.

Error hygiene (CLI-wide): named errors → clean `trailhead: <message>` on stderr +
nonzero exit (never a raw traceback); normal output → stdout; no ANSI/color.
`trailhead/docs/` holds the authoritative specs (`composition-seam.md`,
`capability-manifest.md`, `install-manifest.md`, `path-integration.md`,
`vcs-provider.md`).

## Plugin anatomy (every tool under `tools/`)

```
tools/<name>/
  capabilities.toml            # [tool] base/hooks_json; inventory discovered on disk
  .claude-plugin/marketplace.json
  plugins/<name>/
    .claude-plugin/plugin.json
    skills/<skill>/SKILL.md    # user-invocable skills  → selectable by name
    agents/<agent>.md          # subagent definitions    → selectable by name
    <name>/                    # package: domain subpackages + cli/ (importable, testable)
    hooks/                     # lifecycle hooks (lore)
    bin/ + cli/                # PATH wrapper → thin shim delegating to <name>.cli.dispatch
  tests/                       # pytest, stdlib-only
```

To make a subagent/skill selectable, just add the `agents/<name>.md` file or
`skills/<name>/SKILL.md` dir — `capabilities.py` discovers it; the config and
installer pick it up automatically. `bin/<tool>` wrappers resolve the real CLI via
`${CLAUDE_PLUGIN_ROOT}/cli/<tool>` with a symlink-safe self-relative fallback (no
GNU `readlink -f`, macOS-safe); `cli/<tool>` itself is a thin shim that bootstraps
`sys.path` then delegates to `<name>.cli.dispatch.main()`. Domain logic lives in
real subpackages under `<name>/<domain>/*.py` (e.g. `lore/vault/`, `lore/record/`,
`camp/group/`, `camp/provision/`), one directory per domain with relative imports
between them — keeping it unit-testable independent of the CLI shim.

## Conventions worth matching

- Commit subjects are scoped conventional commits: `feat(trailhead):`, `fix(craft):`,
  `refactor(camp):`, `docs(...)`, `test(...)`.
- Comments, docstrings, and tests must stand on their own — explain intent and
  contracts in terms a reader of the code can verify directly. Do **not** reference
  internal planning artifacts (development "slices", lettered "specs" or invariant
  tags like `D-1`/`S-3`, resolved "unknowns", plan documents, or council reviews);
  those live in the project's working notes, not in the shipped code.
- Modules carry long contract docstrings stating their invariants and security
  posture — read them before changing behavior; update them when the contract
  changes (Axiom 5).
- Code is written for testability via dependency injection: real collaborators are
  imported at module level so tests patch them (e.g. `patch("trailhead.install.wire")`),
  and resolvers/pipelines take injectable `env=` / `runner=` / `platform=`.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
