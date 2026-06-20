# craft

A portable **software-development** plugin for Claude Code: dev agents and
dev-ritual skills that work in any project, with no app-specific assumptions
baked in.

craft is the dev-tooling sibling of [lore](../lore) (portable knowledge
management). Where lore owns *what you know*, craft owns *how you build*:
the reusable agents and rituals a developer reaches for regardless of which
codebase they're in.

## What craft covers

craft's agents and skills organize into six areas. craft owns the
**plan → execute → review** development loop; shipping (PR lifecycle) and deploy
(post-merge soak) live in the sibling [portage](../portage) and
[landing](../landing) plugins. (These are conceptual areas, not install units —
`trailhead install` selects individual subagents and skills, named below, by
name; the default installs them all.)

| Area | What it covers |
|---|---|
| Planning | Turn fuzzy ideas into specs and implementation plans |
| Execute | TDD subagent-driven implementation, slice by slice |
| Review | Structured code review after implementation |
| Council | Four-lens review panel (builder / reliability / security / advocate) |
| Design | Design-doc authoring and structured spec artifacts |
| Helpers | Cheap specialist subagents for docs, logs, research, tests, security |

## Agents

**Planning:** `craft:planner`, `craft:architect`

**Execute:** `craft:assumption-prover` (resolves unknowns via throwaway TDD
tests), `craft:executor` (TDD implementer)

**Review:** `craft:code-reviewer`

**Council** — four-lens review panel dispatched as a parallel quartet by a
planning skill's council review step, and each member is also dispatchable
standalone:
- `craft:builder` (architecture)
- `craft:breaker` (tests/failure modes)
- `craft:attacker` (threat model)
- `craft:advocate` (UX/user perspective)

**Design:** `craft:artist`

**Helpers:** `craft:researcher`, `craft:troubleshooter`, `craft:doc-finder`,
`craft:test-runner`, `craft:log-sifter`, `craft:security-auditor`

Nothing app-specific belongs in craft; per-project automation stays in that
project's own repo.

## Skills

Base skills (always available): `/craft:polish`

**Planning:** `/craft:plan`

**Council:** `/craft:consult` — convene the four-lens panel on a question and
synthesize. The standalone form of the planning skill's council-review step;
membership is single-sourced from `skills/_shared/council.md`.

**Execute:** `/craft:execute`

**Review:** `/craft:review`

## Moved commands — shipping & deploy now live in portage / landing

craft used to ship `release` commands for the PR-lifecycle and post-merge
soak. That surface moved to the sibling [portage](../portage) (get it merged)
and [landing](../landing) (get it deployed) plugins. If you reach for a removed
`craft` command, use its replacement:

| Removed | Replacement |
|---|---|
| `/craft:create-pr` | `/portage:open` |
| `/craft:update-pr` | `/portage:update` |
| `/craft:merge-pr` | `/portage:merge` |
| `/craft:watch-pr` | `/portage:monitor` |
| `/craft:watch-preview` | `/landing:soak` |
| `/craft:post-merge-decide` | `/landing:resolve` |
| `/craft:shelve` | camp session-resume (`claude -r <slug>`) |
| `/craft:pickup` | camp session-resume (`claude -r <slug>`) |

## Layout

```
plugins/craft/
  .claude-plugin/plugin.json      # plugin manifest
  agents/                         # dispatchable subagents
  skills/                         # /craft: ritual skills
tests/                            # packaging + registrability invariants
```

Claude Code rejects `source: "."` — the plugin must live in a `plugins/craft/`
subdir referenced by `source: "./tools/craft/plugins/craft"` in the root marketplace.

## Install

craft is installed as part of Trailhead — see the [root README](../../README.md)
for `trailhead install` instructions.

For local dev work on the plugin itself:

```
/plugin marketplace add <repo-root>
/plugin install craft@trailhead-local
```

Then restart the session and confirm a craft agent dispatches as a
`craft:<name>` subagent_type. See [`MANUAL-SMOKE.md`](MANUAL-SMOKE.md) for the
full boundary smoke test.

## Leak gate

A generic, denylist-driven pre-publish check that blocks a commit when a
private string would ship into a publishable repo. The mechanism ships **zero**
private strings — every forbidden token lives in a machine-local denylist that
is never tracked in any repo:

```
plugins/craft/scripts/leak_gate.py        # the gate (denylist-driven, fail-closed)
plugins/craft/scripts/install-hooks.sh    # chain-safe pre-commit installer
~/.claude/leak-gate.denylist              # machine-local denylist (UNTRACKED)
```

Run it directly:

```bash
python3 plugins/craft/scripts/leak_gate.py <tree> --denylist ~/.claude/leak-gate.denylist
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
plugins/craft/scripts/install-hooks.sh <repo> plugins/<name> tests
```

The installer is idempotent and chain-safe — an existing pre-commit hook is
preserved and run first. `.git/hooks/` is never committed, so the absolute paths
baked into the generated hook stay machine-local.

## Tests

```bash
python3 -m pytest -q
```

Covers manifest validity (`marketplace.json` / `plugin.json`), agent + skill
frontmatter registrability, and structural genericity (no host-project-specific
seams). The plugin-system boundary (actual install + dispatch) is covered by
`MANUAL-SMOKE.md`, which unit tests can't reach.
