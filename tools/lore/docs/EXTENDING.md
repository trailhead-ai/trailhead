# Extending lore for your project

This is the adopter cookbook: how to take the two portable plugins — **lore**
(a git-backed second brain) and **forge** (portable dev agents) — and bolt your
own project-specific layer on top, without forking either plugin.

If you only want to install and use lore as-is, see the
[README](../README.md). This guide is for when you want forge's planning and
review skills to talk to *your* feature-flag provider, *your* issue tracker,
*your* metrics stack, or *your* test commands.

> **Architecture as of 2026-06-04.** All three layers described below ship today:
> lore and forge are published plugins, and the "app layer" pattern is a thin
> project-scoped `.claude/` directory you write. This guide describes the
> shipped state, not a roadmap. The extension points named here are sourced from
> the actual `extension point — X` seams in the shipped skills and agents; the
> forge `plugins/forge/docs/DEGRADATION.md` and
> [lore DEGRADATION.md](DEGRADATION.md) are the canonical re-add references and
> the source of truth if this guide and they ever disagree.

---

## 1. The three layers

Adoption is a stack of three layers with a strict, one-directional dependency:

```
┌─────────────────────────────────────────────────────────┐
│  your app layer  — project-scoped .claude/{agents,       │
│                    skills,hooks} + a thin config + any    │
│                    app-specific tail                      │
├─────────────────────────────────────────────────────────┤
│  forge           — portable dev agents (planning, review, │
│                    subagent-driven dev, test-runner, …)   │
├─────────────────────────────────────────────────────────┤
│  lore            — portable PKM (capture, session notes,  │
│                    recall, status guard, the `lore` CLI)  │
└─────────────────────────────────────────────────────────┘
```

**lore** is the bottom, fully portable layer: capture commands, automatic
session notes, branch/keyword recall, and a vault with a status guard. It has
**no** dependency on forge and no knowledge of your app.

**forge** is the dev-agent layer: planning, code review, subagent-driven
development, and the helper agents (`test-runner`, `log-sifter`,
`pr-summarizer`, `researcher`, the council). forge **may** use lore — several of
its skills and agents write durable notes to the vault through the `lore` CLI,
and the council agents will consult a knowledge-synthesis subagent
(`lore:lore-librarian`) if one is installed. This dependency only ever points
**down**.

**The dependency rule:**

> forge MAY use lore. lore MUST NOT need forge.

That is what makes lore adoptable on its own — you can install lore in a repo
that has no dev-agent workflow at all, and nothing breaks. forge degrades
gracefully when lore (or any other optional integration) is absent: it prints a
visible-skip notice instead of failing.

**Your app layer** is everything specific to your project: the agents, skills,
and hooks that know about *your* stack. It lives in the project's own
`.claude/` directory (project-scoped, committed to your app repo), plus a thin
config file and whatever app-specific tail you need. This is where you wire the
extension points (Section 3) to your real providers. You never edit the lore or
forge plugins to do this — you add a project layer that fills their seams.

---

## 2. Wiring it up

### 2.1 Add the marketplace and install the plugins

```
/plugin marketplace add https://github.com/<lore-repo>
/plugin install lore@lore-local

/plugin marketplace add https://github.com/<forge-repo>
/plugin install forge@forge-local
```

Substitute the published repository URLs for your fork (or this repo). For local
development, `add` a filesystem path instead of a URL — see the
[README](../README.md#install).

### 2.2 Point lore at a vault

```bash
export LORE_VAULT=~/lore        # add to ~/.bashrc or ~/.zshrc
lore init ~/lore
```

`lore init` scaffolds the vault taxonomy, copies the starter docs, initializes a
git repo, and installs a pre-commit guard that enforces the canonical status
vocabulary. `$LORE_VAULT` tells every hook and the `lore` CLI where the vault
lives. If it is unset, lore defaults to `~/lore` and warns once at session
start.

### 2.3 Add your app layer as a project-scoped `.claude/`

In your application repo, create a `.claude/` directory with your own agents,
skills, and hooks. This is the layer that knows about your stack. Register
project hooks in `.claude/settings.json`.

#### Anchor every hook command at `$CLAUDE_PROJECT_DIR`

A project hook's command path **must** be absolute via the `$CLAUDE_PROJECT_DIR`
environment variable that Claude Code exports. Here is a correct
`.claude/settings.json` snippet registering a project SessionStart hook:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/app-context.py\""
          }
        ]
      }
    ]
  }
}
```

> **Footgun — a relative hook path silently exits 0.** If you write the command
> as a relative path (e.g. `python3 .claude/hooks/app-context.py` or
> `./hooks/app-context.py`), the hook will appear registered but **never fire**:
> depending on the session's working directory the interpreter can't find the
> file, and the hook just **exits 0 silently** — no error, no output, nothing in
> context. You get no signal that anything is wrong. Always anchor the command
> at `$CLAUDE_PROJECT_DIR` so it resolves regardless of cwd.

This is the single most common wiring mistake, because a relative path *looks*
fine and fails invisibly. If a hook you added "isn't doing anything," check the
command path first.

---

## 3. Extension-point cookbook

forge's dev skills and lore's planning-adjacent skills carry **extension
points** — named seams where the generic skill defers to a provider you wire in
your app layer. When a seam is unconfigured, the skill emits a **visible-skip**
notice (it announces the omission rather than silently dropping the step) and
continues. You fill a seam by adding a project skill/agent in your `.claude/`
layer and wiring it at the named step.

**The re-add paths below are summarized — the canonical, never-stale source of
truth is the DEGRADATION reference, which you should link to and follow:**

- **forge `plugins/forge/docs/DEGRADATION.md`** — the formal table for
  `feature_flags`, `observability`, and `issue_tracker`, with the exact
  visible-skip phrase and re-add path for each.
- [lore DEGRADATION.md](DEGRADATION.md) — lore's own degradations (see
  Section 3.2).

### 3.1 The shipped extension points

| Extension point | What it gates | Default when unconfigured (visible-skip) | How you fill it |
|---|---|---|---|
| **`feature_flags`** | Provider-specific flag naming + the flag-configuration dispatch in `planning` and `subagent-driven-development`. The flag-touchpoint *decision* still happens; only provider wire-up is skipped. | `no feature-flag provider configured — see the extend guide` / `flag setup skipped` | Add a flag-configuration skill to your app layer that knows your provider's SDK and naming conventions; dispatch it at the Pre-Loop flag-setup step. |
| **`observability`** | Provider-specific metric naming, alert-rule generation, and health-check wiring in `planning` and the `planner` agent. The Observability & Failure Visibility *decision* still happens. | `no observability provider configured — see the extend guide` | Add an alert/metric-configuration skill that knows your metrics/alerting provider's conventions; dispatch it at the provider step. |
| **`issue_tracker`** | Advancing your work item's status (in-progress / complete transitions) from `planning`, `subagent-driven-development`, and `intake`. The plan is always written to the vault. | `no issue tracker configured — status sync skipped` / `status transitions skipped` | Add a tracker-sync skill that calls your tracker's API; hook it into the plan-write, loop-entry, and after-all-slices steps. |
| **`design_mockup`** | The mockup-generation step in lore's `brainstorm` skill, for ideas with a user-facing surface. | `the mockup step is skipped` (announced when no `design-mockup tool is configured`) | Add a design-mockup tool/skill to your app layer; `brainstorm` dispatches it with a structured prompt when present. |
| **`build_test_commands`** | The exact build/test/lint command the `test-runner` agent runs. The agent is stack-agnostic — it runs whatever command it is given. | (no provider banner — the caller simply supplies the command per invocation) | Pass your project's test runner, lint tool, or CI script as the command when you dispatch `test-runner`, or wrap it in an app skill that always supplies your stack's commands. |

Describe these generically for your own stack: `issue_tracker` → *your* tracker's
API; `observability` → *your* metrics/alerting provider; `build_test_commands` →
*your* test/lint commands. There is nothing provider-specific baked into the
plugins.

> **Not extension points.** The shipped agents `log-sifter` (a log path is just
> passed in) and `pr-summarizer` (it summarizes whatever review-bot comments
> exist) are stack-agnostic by design — they have **no** configurable seam, no
> visible-skip, and no re-add path. Don't wire them as extension points; just
> call them with the inputs you have.

### 3.2 lore's own degradations

Separately from the dev-skill seams above, lore itself ships a few capabilities
in a degraded state — these are about lore's own behavior, not your app
integration. The canonical reference is [lore's DEGRADATION.md](DEGRADATION.md);
in brief:

- **Mid-conversation subsystem recall** (the UserPromptSubmit classifier) is
  deferred. Branch/keyword recall still fires at SessionStart; a banner
  announces that mid-conversation recall is off. Re-add path: port the
  classifier hook and flip the capability flag.
- **`/lore:reflect`** does not gather app-layer ai-memory or daily briefings
  (those live outside the vault); it synthesizes vault content only.
- **`/lore:check-radar`** skips legacy `snoozed` radar notes (not in lore's
  canonical status vocabulary) and flags them for manual cleanup.

Follow the linked DEGRADATION.md for the exact turn-on steps; this guide does not
duplicate them, so they cannot drift.

---

## 4. Worked example — a reference adopter

Here is what a real adopter stack looks like, abstracted to vendor-neutral terms.
A reference adopter installs **lore + forge** and adds a project `.claude/`
layer plus a `project.config.json`-style tail:

```
your-app/
  .claude/
    settings.json          # registers project hooks ($CLAUDE_PROJECT_DIR-anchored)
    hooks/
      app-context.py       # injects app-specific context at SessionStart
    skills/
      flag-config/         # fills the feature_flags seam (your flag provider)
      tracker-sync/        # fills the issue_tracker seam (your tracker's API)
      alert-config/        # fills the observability seam (your metrics provider)
    agents/
      <app-specific agents your workflow needs>
  project.config.json      # thin config: vault path overrides, command defaults
```

What this adds, and where:

- **`flag-config/`** — a project skill that knows the adopter's feature-flag
  provider SDK and naming conventions. Dispatched by `planning` /
  `subagent-driven-development` at the `feature_flags` step. Without it, those
  skills print `no feature-flag provider configured` and proceed.
- **`tracker-sync/`** — a project skill that calls the adopter's issue tracker's
  API to advance ticket status. Hooked into the `issue_tracker` steps. Without
  it, status sync is skipped (the plan still lands in the vault).
- **`alert-config/`** — a project skill that emits the adopter's metric/alert
  conventions. Dispatched at the `observability` step. Without it, the decision
  still happens but provider-specific wiring is skipped.
- **`test-runner` invocation** — the adopter passes their stack's test/lint
  command (the `build_test_commands` seam); no project skill is required, just
  the right command.
- **`app-context.py`** — a SessionStart hook (anchored at `$CLAUDE_PROJECT_DIR`)
  that injects app-specific context. This is the app-specific tail.

The adapters are described by what they connect to — "your issue tracker," "your
metrics/alerting provider," "your test/lint commands" — never by a specific
vendor name, because the plugins are vendor-neutral and so is this guide. Pick
the providers your team uses and write the thin skills that bridge to them.

---

## 5. Checklist

1. Install lore and forge from the marketplace.
2. `export LORE_VAULT` and `lore init` a vault.
3. Create a project `.claude/` with `settings.json` registering any hooks via
   **`$CLAUDE_PROJECT_DIR`-anchored** command paths (never relative).
4. For each extension point your workflow needs, add a project skill in
   `.claude/skills/` and wire it at the named step. Follow the
   [DEGRADATION.md](DEGRADATION.md) re-add path as the source of truth.
5. Leave the seams you don't need unconfigured — they degrade with a
   visible-skip, not a failure.

<!--
Content-accuracy note: the extension points named in this guide
(feature_flags, observability, issue_tracker, design_mockup,
build_test_commands) are cross-checked against the shipped `extension point — X`
seams by tests/test_extending_doc.py. If you add or rename a seam upstream,
that test forces this guide to keep pace. Manual cross-check last performed
2026-06-04 against the lore + forge shipped skills/agents and DEGRADATION.md.
-->
