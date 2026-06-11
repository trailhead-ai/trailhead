# trailhead

Agent-native project memory that works with your existing setup.

---

## Start with lore — change nothing about how you work

Most knowledge management tools ask you to change your workflow to get value.
**lore doesn't.** It hooks into the Claude Code sessions you're already running
and captures the things that matter — decisions, dead-ends, work set aside,
codebase mental models — then loads what's relevant the next time you open the
project.

Your next session starts warm. The agent recalls what it needs without you
asking. You get compounding memory across sessions without a new ritual or a
new tool to learn.

```sh
# confirm lore is wired and your vault is ready
trailhead doctor
```

Then open any project in Claude Code. Run:

```sh
# pull area-scoped memory into the session when starting a task
lore recall --areas <topic>
```

The agent loads decisions, dead-ends, and open deferred items from that area —
giving it the non-obvious context that usually lives only in your head.

---

## The opt-in growth arc

lore is the lowest-buy-in entry point. Once it's part of how you work, two
siblings are available when you're ready:

**→ Add camp** when you have multiple repos to coordinate. camp gives Claude
structured primitives for managing git worktrees across a group of repositories —
the "where is the work happening" question, answered automatically.

**→ Add forge** when you want the dev rituals: structured planning, TDD
subagent-driven implementation slice by slice, assumption-proving scouts, and a
four-lens circle review. forge owns *how you build*; lore owns *what you know*.

**You never have to adopt the whole suite.** Each tool is independently
adoptable. Wire only what you need.

---

## What makes lore different

lore is positioned against the other lightweight memory tools for Claude Code —
Basic Memory, claude-mem, native Claude Code memory:

| What                     | How lore handles it                              |
|---|---|
| **Git-native versioning** | Every capture is a vault commit — PR-reviewable, diffable, rollbackable |
| **Typed taxonomy**        | `dead-end` / `deferred` / `radar` capture negative + future-conditional knowledge that pure text search misses |
| **Multi-repo scoping**    | A single vault tracks memory across however many repos your project spans |
| **Local / no-egress**     | The vault is a plain git repo on your machine; no data leaves without a push |
| **Explainable recall**    | Area-mediated: the agent says *why* it recalled, not just "here is some context" |

**Not trying to replace your note app.** lore is your agent's working memory,
not a personal knowledge base. It captures what the *agent* needs to remember
across sessions, in a form the agent can reason about.

**Semantic (embedding) recall** is planned as an opt-in Tier-2 layer — not yet
built. Today's recall is area-mediated: fast, explainable, and zero
infrastructure.

---

## What's included

One `trailhead` repo ships all four tools, pinned at a single commit SHA:

| Tool         | What it covers |
|---|---|
| `trailhead`  | Management CLI — install, update, doctor, config |
| `lore`       | Agent-native project memory — capture, recall, sessions |
| `camp`       | Worktree + group orchestration across repos |
| `forge`      | Dev rituals — planning, TDD execute, review, release |

**outpost** (a personal dev-env and PR review dashboard) is a companion tool
in its own repo — forward-declared in the install manifest for a future install
step, not wired today.

---

## Install

### Try it today (from a local checkout)

The tools are real and run today. Clone the repo and do an editable install:

```sh
pip install -e .
trailhead install
```

`trailhead install` prompts for a preset and prints what it wired, where config
lives, and the next command to run. Pick a preset:

```sh
# minimal: lore only — lowest buy-in
trailhead install --preset minimal

# standard: lore + camp + forge subset — the common loop
trailhead install --preset standard

# full: every capability in every tool
trailhead install --preset full
```

After install, start a fresh Claude Code session to load the wired tools.

### When the public registry lands

The public home for this repo lands with the org/repo-homing work (WS-10). Once
it's live, the canonical install path will be:

```sh
trailhead config registry <registry-url>
trailhead install
```

The tools are real now; the public registry address is the only thing that isn't
wired yet. Install notes are an invitation to what you can do today from a
checkout, not an apology for what's missing.

---

## Start here

Once install finishes, your first lore moment:

```sh
lore recall --areas <topic>
```

Open Claude Code in a project, describe your task, and ask the agent to recall
the relevant area. It loads what it knows — decisions made, approaches that
didn't work, work set aside — and starts warm.

The first time you `/lore:decision` something that saves you half an hour of
re-investigation, lore has paid for itself.

---

## Tool READMEs

- [lore](./tools/lore/README.md) — skills, recall mechanics, vault layout
- [forge](./tools/forge/README.md) — agents, skills, capability groups
- [camp](./tools/camp/README.md) — worktree commands, group config

## License

Apache-2.0. See [LICENSE](LICENSE).
