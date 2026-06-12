# forge

A portable **software-development** plugin for Claude Code: dev agents and
dev-ritual skills that work in any project, with no app-specific assumptions
baked in.

forge is the dev-tooling sibling of [lore](../lore) (portable knowledge
management). Where lore owns *what you know*, forge owns *how you build*:
the reusable agents and rituals a developer reaches for regardless of which
codebase they're in.

## Capabilities

forge ships six capability groups, each with agents and skills. forge owns the
**plan → execute → review** development loop; shipping (PR lifecycle) and deploy
(post-merge soak) live in the sibling [portage](../portage) and
[landing](../landing) plugins.

| Capability | What it covers |
|---|---|
| `planning` | Turn fuzzy ideas into specs and implementation plans |
| `execute` | TDD subagent-driven implementation, slice by slice |
| `review` | Structured code review after implementation |
| `council` | Four-lens review panel (builder / reliability / security / advocate) |
| `design` | Design-doc authoring and structured spec artifacts |
| `helpers` | Cheap specialist subagents for docs, logs, research, tests, security |

## Agents

**Planning:** `forge:planner`, `forge:architect`

**Execute:** `forge:assumption-prover` (resolves unknowns via throwaway TDD
tests), `forge:executor` (TDD implementer)

**Review:** `forge:code-reviewer`

**Council** — four-lens review panel dispatched as a parallel quartet by a
planning skill's council review step, and each member is also dispatchable
standalone:
- `forge:builder` (architecture)
- `forge:breaker` (tests/failure modes)
- `forge:attacker` (threat model)
- `forge:advocate` (UX/user perspective)

**Design:** `forge:artist`

**Helpers:** `forge:researcher`, `forge:troubleshooter`, `forge:doc-finder`,
`forge:test-runner`, `forge:log-sifter`, `forge:security-auditor`

Nothing app-specific belongs in forge; per-project automation stays in that
project's own repo.

## Skills

Base skills (always available): `/forge:shelve`, `/forge:pickup`,
`/forge:polish`

**Planning:** `/forge:plan`

**Council:** `/forge:consult` — convene the four-lens panel on a question and
synthesize. The standalone form of the planning skill's council-review step;
membership is single-sourced from `skills/_shared/council.md`.

**Execute:** `/forge:execute`

**Review:** `/forge:review`

`/forge:shelve` and `/forge:pickup` are a symmetric shelve/resume pair — record
read-only git state and shelve a session note with pickup hints so a future
session can resume. The git capture is strictly **read-only** — these rituals
record state, they do not commit, push, or rebase your code.

**lore-optional coupling:** the handoff/pickup pair drives the [lore](../lore)
CLI when it's available, and degrades to a local forge handoff file at
`~/.forge/handoffs/<slug>.md` when lore is absent, `$LORE_VAULT` is unset, or
`lore stats` fails. This is the same one-directional optional dependency forge
already has on lore (forge → lore is allowed; lore never depends on forge).

## Moved commands — shipping & deploy now live in portage / landing

forge used to ship a `release` capability for the PR-lifecycle and post-merge
soak. That surface moved to the sibling [portage](../portage) (get it merged)
and [landing](../landing) (get it deployed) plugins. If you reach for a removed
`forge` command, use its replacement:

| Removed | Replacement |
|---|---|
| `/forge:create-pr` | `/portage:open` |
| `/forge:update-pr` | `/portage:update` |
| `/forge:merge-pr` | `/portage:merge` |
| `/forge:watch-pr` | `/portage:monitor` |
| `/forge:watch-preview` | `/landing:soak` |
| `/forge:post-merge-decide` | `/landing:resolve` |

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

## Install

forge is installed as part of Trailhead — see the [root README](../../README.md)
for `trailhead install` instructions.

For local dev work on the plugin itself:

```
/plugin marketplace add /path/to/forge
/plugin install forge@forge-local
```

Then restart the session and confirm a forge agent dispatches as a
`forge:<name>` subagent_type. See [`MANUAL-SMOKE.md`](MANUAL-SMOKE.md) for the
full boundary smoke test.

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
frontmatter registrability, structural genericity (no host-project-specific seams), and
the deterministic handoff-capture helper (git-state survey, lore 3-state
detection, degraded-file write) including a real cross-repo integration test
that drives the actual `lore handoff` against a synthetic fixture vault. The
plugin-system boundary (actual install + dispatch) is covered by
`MANUAL-SMOKE.md`, which unit tests can't reach.
