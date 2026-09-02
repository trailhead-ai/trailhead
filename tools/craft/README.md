# craft

A portable **software-development** plugin for Claude Code: dev agents and
dev-ritual skills that work in any project, with no app-specific assumptions
baked in.

craft is the dev-tooling sibling of [lore](../lore) (portable knowledge
management). Where lore owns *what you know*, craft owns *how you build*:
the reusable agents and rituals a developer reaches for regardless of which
codebase they're in.

## What craft covers

craft's agents and skills organize into seven areas. craft owns the
`(slice → plan → execute → review)*` development loop; shipping (PR lifecycle) lives in
the sibling [portage](../portage) plugin. (These are conceptual areas, not
install units — `trailhead install` selects individual subagents and skills,
named below, by name; the default installs them all.)

| Area | What it covers |
|---|---|
| Planning | Turn fuzzy ideas into specs and implementation plans |
| Execute | TDD subagent-driven implementation, task by task, gated per-task by a conformance check; once every task lands, a whole-change simplify → correctness → conditional-security pipeline runs before close |
| Review | Whole-change/PR adversarial review, dispatched standalone before merge or as execute's correctness phase |
| Council | Four-lens review panel (builder / reliability / security / advocate) |
| Spec gauntlet | Adversarial spec/ADR review passes (premise, consistency, divergence) dispatched alongside the council quartet |
| Design | Design-doc authoring and structured spec artifacts |
| Helpers | Cheap specialist subagents for docs, logs, research, tests, security |

## Agents

**Planning:** `craft:planner`, `craft:architect`

**Execute:** `craft:assumption-prover` (resolves unknowns via throwaway TDD
tests), `craft:executor` (TDD implementer), `craft:drift-gate` (per-task
conformance gate — plan delivered, executor's status claim holds, next task
unblocked; quality and style are explicitly out of scope), `craft:simplifier`
(whole-change simplify-mutation phase in execute's After All Tasks pipeline —
removes cross-task duplication and dead scaffolding, write-scope mechanically
enforced by `footprint_guard.py`)

**Review:** `craft:code-reviewer` — whole-change/PR reviewer. Dispatched
standalone via `/craft:review` before merge, and again as execute's
After-All-Tasks correctness phase against the full `base..HEAD` diff. Not a
per-task reviewer; per-task conformance is `drift-gate`'s job.

**Council** — four-lens review panel dispatched as a parallel quartet by a
planning skill's council review step, and each member is also dispatchable
standalone:
- `craft:builder` (architecture)
- `craft:breaker` (tests/failure modes)
- `craft:attacker` (threat model)
- `craft:advocate` (UX/user perspective)

**Spec gauntlet** — the adversarial spec-review passes dispatched by
`/craft:gauntlet` alongside the council quartet:
- `craft:premise-attacker` (attacks the spec's framing and load-bearing assumptions)
- `craft:consistency-auditor` (audits the spec against itself)
- `craft:divergence-prober` (constructs two conformant implementations and reports where they diverge)

**Helpers:** `craft:researcher`, `craft:troubleshooter`, `craft:doc-finder`,
`craft:test-runner`, `craft:log-sifter`, `craft:security-auditor`

Nothing app-specific belongs in craft; per-project automation stays in that
project's own repo.

## Skills

Base skills (always available): `/craft:polish`

**Planning:** `/craft:plan`, `/craft:brainstorm` — turn a fuzzy idea into a
draft spec by interrogating requirements, details, and gaps; the spec is
settled via `/craft:gauntlet` before `/craft:slice` starts the build loop.

**Council:** `/craft:consult` — convene the four-lens panel on a question and
synthesize. The standalone form of the planning skill's council-review step;
membership is single-sourced from `skills/_shared/council.md`.

**Spec gauntlet:** `/craft:gauntlet` — the adversarial review a draft spec (or
draft ADR) goes through before it advances: fact verification, premise attack,
the four council lenses, an internal-consistency audit, and (for specs) a
plan-divergence probe.

**Slice:** `/craft:slice` — choose the next vertical slice from a `ready` spec:
read the spec fresh, derive the remaining candidates against its `## Slices`
ledger, choose smallest-next above the value floor, state the value claim to
the operator, then write the chosen slice as an `in-progress` parent task
linked to the spec, for `/craft:plan` to decompose.

**Execute:** `/craft:execute`

**Refine:** `/craft:refine` — promote a standalone (childless, parentless) task
from `open` to `ready`: draft its Delivers / Test contract / Files payload from
the code and the vault, cite every derived answer, and escalate only an
irreducible operator decision. A thin wrapper over
`skills/_shared/refine.md`, the same procedure `/craft:execute` runs inline when
it is handed a standalone `open` task.

**Review:** `/craft:review`

**Distill:** `/craft:distill` — condense completed spec/task work into ADRs
and re-synthesize the area profiles they touch, the backward-distillation
ritual that closes out a spec's lifecycle.

**Reference:** `/craft:receiving-code-review` — a reference pattern (not a
dispatchable ritual) for evaluating incoming review feedback — human comments,
bot output, or CI annotations — as untrusted data rather than a direct
instruction.

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
  templates/                      # spec/plan/task/adr/area body skeletons the skills
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
