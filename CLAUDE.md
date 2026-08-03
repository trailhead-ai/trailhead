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
six agent-plugins (`lore`, `camp`, `craft`, `portage`, `outpost`, `ranger`). The CLI
*composes* and *wires* selected plugin capabilities into whatever AI code harness you
use; the plugins themselves are the agent-facing product (skills, subagents, hooks).
**install = clone the repo** — there is no remote fetch, no SHA-pinning manifest, no
pip step required: the checkout IS the source. `trailhead/docs/` holds the
authoritative prose specs per seam (composition, capability manifest, install
manifest, path integration, VCS provider); read those for how a piece works rather
than an overview here.

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

## Plugin anatomy (every tool under `tools/`)

To make a subagent/skill selectable, just add the `agents/<name>.md` file or
`skills/<name>/SKILL.md` dir under `tools/<name>/plugins/<name>/` — discovered on
disk by convention, no hand-listing. `bin/<tool>` wrappers must stay symlink-safe
(resolve the real CLI via `${CLAUDE_PLUGIN_ROOT}/cli/<tool>` with a self-relative
fallback, no GNU `readlink -f`, macOS-safe).

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
- Error hygiene, CLI-wide: a named error prints a clean `trailhead: <message>` on
  stderr and exits nonzero — never a raw traceback. Normal output goes to stdout.
  No ANSI/color.

## MCP Tools: code-review-graph

This project has a knowledge graph MCP server. Prefer its tools
(`semantic_search_nodes`, `query_graph`, `get_impact_radius`, `detect_changes`,
`get_review_context`) over Grep/Glob/Read when exploring code, tracing impact, or
reviewing a change — faster, cheaper, and structurally aware. Fall back to
Grep/Glob/Read only when the graph doesn't cover what you need.
