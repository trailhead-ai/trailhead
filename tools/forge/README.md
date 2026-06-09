# forge

A portable **software-development** plugin for Claude Code: general-purpose dev
agents and dev-ritual skills that work in any project, with no app-specific
assumptions baked in.

forge is the dev-tooling sibling of [lore](../lore) (portable knowledge
management). Where lore owns *what you know*, forge owns *how you build*:
the reusable agents and rituals a developer reaches for regardless of which
codebase they're in.

## Status

**Agent fleet populated (v0.2.0).** The 15 general dev agents are present and
genericized — they carry zero app-specific or vault-specific strings and
register as `forge:<name>`:

- `architect`, `researcher`, `troubleshooter`
- `code-reviewer`, `security-auditor`
- `doc-finder`, `test-runner`, `log-sifter`, `pr-summarizer`
- `sdd-assumption-prover`, `sdd-implementer`
- `council-builder`, `council-reliability`, `council-security`, `council-advocate`

The four **council review-lens agents** (`council-*`) are dispatched as a
parallel quartet by a planning skill's council-lite review step — not
standalone. Each holds a single perspective (Builder=architecture,
Reliability=tests/failure modes, Security=threat model, Advocate=UX) and
returns a focused single-lens response rather than a synthesis. If you need
general architecture advice outside a planning context, use `architect` instead.

**First skills shipped.** `/forge:handoff` and `/forge:pickup` are the first
dev-ritual skills — a symmetric shelve/resume pair (see *What lives here*
below). They stood up forge's skill test harness
(`tests/test_skills_registrable.py` + `tests/test_skills_generic.py`).

A proof-of-life agent (`forge-ping`) also ships to confirm plugin agent
registration works. Two agents are intentionally **not** here: `planner` moves
in a later phase alongside the `/planning` skill, and `design-mockup-writer`
is bound to a specific app's visual aesthetic and lives in that app's own repo.
`code-simplifier` is a separate plugin and was never part of this fleet.
Nothing app-specific belongs in forge; per-project automation stays in that
project's own repo.

## What lives here

- **Agents** (`plugins/forge/agents/`) — general dev subagents, dispatchable as
  `forge:<name>` once installed.
- **Skills** (`plugins/forge/skills/`) — dev-ritual skills, invocable as
  `/forge:<name>`:
  - `handoff` — record read-only git state + shelve a session note with pickup
    hints so a future session can resume.
  - `pickup` — resume a shelved work chain (surface recorded git state + hints).

  **lore-optional coupling:** the handoff/pickup pair drives the [lore](../lore)
  CLI (`lore handoff` / the session-note finder) when it's available, and
  degrades to a local forge handoff file at `~/.forge/handoffs/<slug>.md` — out
  of any repo — when lore is absent, `$LORE_VAULT` is unset, or `lore stats`
  fails. This is the same one-directional optional dependency forge already has
  on lore (forge → lore is allowed; lore never depends on forge). The git
  capture is strictly **read-only** — these rituals record state, they do not
  commit, push, or rebase your code.

## Layout

```
.claude-plugin/marketplace.json   # local dev marketplace (source: ./plugins/forge)
plugins/forge/
  .claude-plugin/plugin.json      # plugin manifest
  agents/                         # dispatchable subagents
  skills/                         # /forge: ritual skills
tests/                            # packaging + registrability invariants
```

Claude Code rejects `source: "."` — the plugin must live in a `plugins/forge/`
subdir referenced by `source: "./plugins/forge"` in the root marketplace.

## Install (local dev)

```
/plugin marketplace add /path/to/forge
/plugin install forge@forge-local
```

Then restart the session and confirm with the `forge-ping` agent. See
[`MANUAL-SMOKE.md`](MANUAL-SMOKE.md) for the full boundary smoke test.

## Leak gate

A generic, denylist-driven pre-publish check that blocks a commit when a
private string would ship into a publishable repo. The mechanism ships **zero**
private strings — every forbidden token lives in a machine-local denylist that
is never tracked in any repo:

```
plugins/forge/scripts/leak_gate.py        # the gate (denylist-driven, fail-closed)
plugins/forge/scripts/install-hooks.sh    # chain-safe pre-commit installer
~/.claude/leak-gate.denylist              # machine-local denylist (UNTRACKED)
```

Run it directly:

```bash
python3 plugins/forge/scripts/leak_gate.py <tree> --denylist ~/.claude/leak-gate.denylist
# exit 0 clean · 1 leak (prints relpath:lineno:token) · 2 fail-closed
```

**Fail-closed:** a missing, unreadable, or pattern-empty denylist makes the gate
exit `2` (error) — it never exits `0` when it could not actually certify the
tree clean. The denylist format is one Python regex per line (`#` comments),
matched case-insensitively; use `\b` word-boundary anchors.

Install as a pre-commit hook. Pass every tree that ships publicly — the
shippable surface **and** `tests/` (test fixtures go public too) — but not
author-controlled root docs, which may legitimately carry the public repo-owner
URL:

```bash
plugins/forge/scripts/install-hooks.sh <repo> plugins/<name> tests
```

The installer is idempotent and chain-safe — an existing pre-commit hook is
preserved and run first. `.git/hooks/` is never committed, so the absolute paths
baked into the generated hook stay machine-local.

## Tests

```bash
python3 -m pytest -q
```

Covers manifest validity (`marketplace.json` / `plugin.json`), agent + skill
frontmatter registrability, structural genericity (no brain-vault seams), and
the deterministic handoff-capture helper (git-state survey, lore 3-state
detection, degraded-file write) including a real cross-repo integration test
that drives the actual `lore handoff` against a synthetic fixture vault. The
plugin-system boundary (actual install + dispatch) is covered by
`MANUAL-SMOKE.md`, which unit tests can't reach.
