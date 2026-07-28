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
**plan → execute → review** development loop; shipping (PR lifecycle) lives in
the sibling [portage](../portage) plugin. (These are conceptual areas, not
install units — `trailhead install` selects individual subagents and skills,
named below, by name; the default installs them all.)

| Area | What it covers |
|---|---|
| Planning | Turn fuzzy ideas into specs and implementation plans |
| Execute | TDD subagent-driven implementation, slice by slice, gated per-slice by a conformance check; once every slice lands, a whole-change simplify → correctness → conditional-security pipeline runs before close |
| Review | Whole-change/PR adversarial review, dispatched standalone before merge or as execute's correctness phase |
| Council | Four-lens review panel (builder / reliability / security / advocate) |
| Design | Design-doc authoring and structured spec artifacts |
| Helpers | Cheap specialist subagents for docs, logs, research, tests, security |

## Agents

**Planning:** `craft:planner`, `craft:architect`

**Execute:** `craft:assumption-prover` (resolves unknowns via throwaway TDD
tests), `craft:executor` (TDD implementer), `craft:drift-gate` (per-slice
conformance gate — plan delivered, executor's status claim holds, next slice
unblocked; quality and style are explicitly out of scope), `craft:simplifier`
(whole-change simplify-mutation phase in execute's After All Slices pipeline —
removes cross-slice duplication and dead scaffolding, write-scope mechanically
enforced by `footprint_guard.py`)

**Review:** `craft:code-reviewer` — whole-change/PR reviewer. Dispatched
standalone via `/craft:review` before merge, and again as execute's
After-All-Slices correctness phase against the full `base..HEAD` diff. Not a
per-slice reviewer; per-slice conformance is `drift-gate`'s job.

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

**Refine:** `/craft:refine` — promote a standalone (childless, parentless) task
from `open` to `ready`: draft its Delivers / Test contract / Files payload from
the code and the vault, cite every derived answer, and escalate only an
irreducible operator decision. A thin wrapper over
`skills/_shared/refine.md`, the same procedure `/craft:execute` runs inline when
it is handed a standalone `open` task.

**Review:** `/craft:review`

## Related plugin — PR lifecycle

PR lifecycle lives in the sibling [portage](../portage) (get it merged) plugin:

| Task | Command |
|---|---|
| Open a PR | `/portage:pull_request create` |
| Update a PR | `/portage:pull_request update` |
| Merge a PR | `/portage:pull_request merge` |
| Watch CI to green | `/portage:pull_request monitor` |
| Resume a shelved session | camp session-resume (`claude -r <slug>`) |

## Layout

```
plugins/craft/
  .claude-plugin/plugin.json      # plugin manifest
  agents/                         # dispatchable subagents
  skills/                         # /craft: ritual skills
  templates/                      # spec/plan/task body skeletons the skills
                                  #   render at runtime via ${CLAUDE_PLUGIN_ROOT}
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
