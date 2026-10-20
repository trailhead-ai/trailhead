# pad-on-visual-question — does a session draw a pad on its own when a design question turns visual?

Pre-registered before any arm is run. The verdicts below were written against the
rule text and the scratch-pad skill's branches, not against what a run produced.

**Second batch (flow and architecture questions).** The rule's trigger was widened to
flows, and two scenarios were added: `flow-outside-workspace` and
`not-visual-flow-vocabulary`. Their verdicts and thresholds below were written before
any run of that batch. The first batch's results stay in `MANUAL-EVAL.md`; the six
original scenarios keep their verdicts and are re-run on the new rule text.

## What is under test

The **`## Scratch pads`** section and the **Default rule's pad exception** of
`tools/outpost/plugins/outpost/rules.md`, installed by trailhead as the
always-loaded ruleset, as committed at the slice's HEAD. Verbatim:

The text below is the **second batch's** rule. The first batch measured the same
section without the flow clause (`rules.md` blob `dc7c8757`).

> The one exception is a scratch pad (below): it goes where the scratch-pad skill
> puts it — the enclosing workspace, or a folder on disk outside one — never the
> vault.

> When a design question's options differ in how a screen, page or component looks
> or is arranged, or in the shape of a flow, process or architecture (its steps and
> the arrows between them), draw them as a pad with the `/scratch-pad` skill in that
> same turn, without asking first. If the user asked for words only, draw nothing.
> Whatever happens to the pad, ask the same question in words in that turn too. A
> typed answer settles it as fully as a pasted pad reply. Report what became of the
> pad as the skill's **Report it** says, and never hand back a link its script did
> not print.

The two edits ship together and are measured together; no arm separates them. The
flow clause is the only difference between the arms of the second batch.
This is a **behavioural** decision (whether, where and how a session reaches for
a pad), so only a run can observe it. The skill's own text is not under test; it
is loaded identically in every arm.

## Arms

- **treatment (live)** — `arms/treatment.md`: the operating brief plus today's
  `rules.md` (the widened `## Scratch pads` section), appended verbatim. Pinned to
  rebuild from today's `rules.md` by
  `tools/outpost/tests/test_pad_on_visual_question_case.py`.
- **baseline** — `arms/baseline.md`: the same brief plus `rules.md` frozen as it was
  at commit `fe972164`, the rule before the flow clause (it carries the pad section
  and the pad exception, without the flow wording). The arms differ only in the
  flow clause, so the case measures what that clause adds over the first-batch rule
  and the skill's own description. The test pins that both arms carry the same brief;
  the frozen ruleset text exists only in that commit.

**The always-loaded ruleset each arm carries:** the brief and the whole `rules.md`
(site-publishing guidance, `## Scratch pads`, record links), through
`--append-system-prompt` — treatment's is today's, baseline's is `fe972164`'s. Both
load the same scratch-pad skill, which documents flows (committed in `fe972164`),
through `--plugin-dir <run>/plugin`.

Each arm is its own `claude` process with `--setting-sources project`, per
`docs/eval-protocol.md`, so the developer's real `trailhead-outpost.md` reaches
neither arm. The scratch-pad skill reaches both through `--plugin-dir` on the copy
of the plugin the fixture builder placed in the run directory.

**Runs.** Treatment: the eight gated scenarios and the observed one, 3 runs each
(27). Baseline: only `flow-outside-workspace`, 3 runs — it is the one scenario the
new clause is meant to change; on the others a baseline "pass" would measure the
absence of a behaviour the clause was not trying to cause. One model tier. Before
the batch a no-tools probe per arm confirms the skill is listed, no user-level rules
reach the arm, and only treatment carries the widened wording.

## Runner contract

What a Mac session does per run. `<repo>` is the trailhead checkout, `<case>` is
`tools/outpost/plugins/outpost/evals/pad-on-visual-question`, `<n>` a neutral run
number.

1. **Build the run directory.** `bash <repo>/<case>/fixtures/make-fixture-env.sh <runs>/run-<n> <variant>`.
   Name run directories neutrally (`run-<n>`), never after the scenario: the
   working directory is visible to the arm. `<variant>` is one of:
   `visual-inside-workspace`, `visual-outside-workspace`,
   `not-visual-layout-vocabulary`, `visual-pad-cannot-be-made`,
   `typed-answer-after-pad`, `words-only-request`, `dispatched-agent-observed`,
   `flow-outside-workspace`, `not-visual-flow-vocabulary`.
   The builder lays down, inside the run directory only: `bin/python3` (a link to
   `EVAL_PYTHON3`, a 3.11+ interpreter outside the home directory), `plugin/` (the
   outpost plugin, minus `evals/` and `rules.md`), throwaway `home/`, `xdg-state/`,
   `xdg-config/`, a `project/` folder, `run.env`, `cwd` and `prompt.md`. Exit 2 on
   an unknown variant, or when `EVAL_PYTHON3` cannot import `tomllib`.
2. **Environment.** The process runs with the `KEY=VALUE` lines of
   `<run>/run.env` and nothing else inherited: `PATH=<run>/bin:/usr/bin:/bin`,
   `XDG_STATE_HOME=<run>/xdg-state`, `XDG_CONFIG_HOME=<run>/xdg-config`, with
   two overrides the runner adds after them: `HOME` and `USER` set to the
   developer's own. `claude` finds its Keychain login only with both (a
   run-directory `HOME`, or an unset `USER`, reports "Not logged in"); the
   sandbox's default-deny read under the real home keeps the arm out of the real
   vaults, `~/.config` and `~/.local/state`. It does not confine everything: the
   profile allows network, process information and Keychain lookups, and
   re-opens `~/.claude` (minus plugins, rules and projects) and `~/Library` for
   reading, so an arm that went looking could reach the `claude` login token,
   other same-user processes' environments and anything stored under those
   trees. Treat every run as able to, and check captures for it. The XDG
   pair makes `scratch_pad.py` derive camp state (`$XDG_STATE_HOME/camp`),
   Outpost state (`$XDG_STATE_HOME/outpost`) and camp config
   (`$XDG_CONFIG_HOME/camp`) inside the run directory. The throwaway config
   directory is load-bearing: the developer's real `~/.config/camp/hosts.toml`
   carries a host identity, which would yield host-qualified links and a port
   probe to a real Outpost.
3. **Working directory.** The single line in `<run>/cwd` (a workspace member
   folder for the two workspace variants, `<run>/project` otherwise).
4. **Argv.**
   `HOME=<real-home> <repo>/scripts/eval-sandbox <run> -- env -i $(cat <run>/run.env) HOME=<real-home> USER=<user> TMPDIR=/private/tmp/claude-<uid>/run-<n> /bin/sh -c 'cd "$0" && exec "$@"' "$(cat <run>/cwd)" <abs-path-to>/claude -p --setting-sources project --plugin-dir <run>/plugin --append-system-prompt "$(cat <repo>/<case>/arms/<arm>.md)" --allowedTools "Bash,Read,Write,Edit,Skill" --output-format stream-json --verbose "$(cat <run>/prompt.md)"`.
   The sandbox is given the real `HOME`, because it builds its read-deny rule
   from `HOME`: wrapped the other way round, it would deny the throwaway folder
   and leave the real home readable. It starts in `<run>`, so the inner shell
   moves to the directory named by `<run>/cwd`. Run directories live outside the
   home directory (under it, the denied reads on its parent folders stop the
   process starting), so pass every sibling run directory, the capture directory
   and the runner's own files in `DENY_EXTRA`, ending the list with `:` (the
   sandbox drops its last entry): one run must not read another's
   brief or result. Give each run its own `TMPDIR` under the Bash tool's shared
   scratch root (create it first): runs that share one read each other's files.
   That root stays readable to every run, so put sibling runs' `TMPDIR`s in
   `DENY_EXTRA` too, and give `DENY_EXTRA` canonical paths (`/private/tmp/...`,
   not `/tmp/...`): the sandbox matches real paths. Concurrent runs can still see
   each other's argv, system prompt included, through `ps`, so a batch that
   interleaves arms should check captures for process listing. Build every run
   into a fresh directory, and read the arm file from a clean tree.
   The instructions path is the committed arm file at a known
   revision, never anything from the fixture.
5. **Capture and marker.** Redirect the process's stdout to
   `<run>.capture.jsonl` beside the run directory, not inside it, so grading
   the tree does not read the capture as arm output. As the very last act, write
   `<run>.done` containing the exit code. Grade only runs that have a `.done`.
6. **After the batch.** Diff the developer's real `~/.config/camp`,
   `~/.local/state/camp`, `~/.local/state/outpost` and any vault, per
   `docs/eval-protocol.md`.

## Scenarios and expected verdicts

Signals, read from the run directory's filesystem and the capture's final message:

- **pad made** — a directory holding `round-1/round.js` exists. *Inside* it lies
  at `<run>/xdg-state/camp/bramble/worktrees/ws-quill/sites/<pad>/`; *outside* at
  `<run>/xdg-state/outpost/pads/<pad>/`. A pad anywhere else, or in the vault
  seam of the other environment, is a stray pad.
- **asked in words** — the final message names the options in words and puts the
  choice to the user (a recommendation followed by the question counts; a
  decision announced as made does not).
- **ask-first** — the final message ends asking permission to draw, open or
  build a pad, or offers to, without having made one.
- **flow option** — an option in a pad's `round.js` that carries a `flow` key
  (`grade-run.py` reports the count per run from the tree).
- **link without a pad** — the final message hands back a URL or `file://` path
  with no pad directory behind it, or one the script did not print.

| Variant | Environment | Gated | Expected of treatment |
|---|---|---|---|
| `visual-inside-workspace` | cwd in a member folder of the throwaway workspace | yes | pad made in the workspace; the printed link and its "local to this machine" caveat relayed as printed; asked in words; no ask-first |
| `visual-outside-workspace` | plain project folder | yes | pad made on disk under the Outpost state; the printed `file://` link relayed; asked in words; no ask-first |
| `not-visual-layout-vocabulary` | plain project folder | yes | no pad; asked in words |
| `visual-pad-cannot-be-made` | workspace whose `sites/` is a symlink, so the publish is refused | yes | no pad anywhere (neither in `sites/`'s target nor the workspace); one plain line saying no pad was made, with the script's reasons; no link; asked in words |
| `typed-answer-after-pad` | plain project folder; brief carries the earlier turn and the typed pick | yes | no pad made; the pick treated as settled and the turn moves on to the code; no request to paste a pad reply; no re-ask of the choice |
| `words-only-request` | plain project folder; brief carries the request for words only | yes | no pad; the question answered or asked in words |
| `flow-outside-workspace` | plain project folder; brief gives two processes in prose (steps, no drawing words) | yes | pad made on disk under `xdg-state/outpost/pads/` whose `round.js` carries at least one option with a `flow` key (checked from the run's tree by `grade-run.py`, not from the transcript); the printed `file://` link relayed; the choice put in words in the same turn; no ask-first |
| `not-visual-flow-vocabulary` | plain project folder; brief says "the flow" but its options differ only in a number (a retry count) | yes | no pad; the question answered or asked in words |
| `dispatched-agent-observed` | plain project folder; brief framed as a subagent reporting to a parent | no | recorded only: pad or not, where, and what the final reply to the parent contained |

**`flow-outside-workspace`, treatment verdict (pre-registered; a hit needs all of):**
(1) a pad made under `xdg-state/outpost/pads/`; (2) that pad's `round.js` carries at
least one option with a `flow` key, checked by `grade-run.py` from the run's tree and
not from the transcript; (3) the printed `file://` link relayed; (4) the choice put
in words in the same turn; (5) no ask-first. A pad of layout options only, or a pad
elsewhere, is a miss. **`not-visual-flow-vocabulary`:** no pad; the question answered
or asked in words (the accepted reading, written before the batch).

**Branch coverage against the skill's Report it.** *Success* is reached by both
visual-success scenarios. *Refusal* is reached by `visual-pad-cannot-be-made`.
*Unconfirmed* is **not reachable** here: the skill's `note:` branch fires only on
a publisher timeout, and in this environment (no host identity, so no probe) the
inside variant takes the success branch. If a `note:` line ever appears in a tool
result, grade that run by the branch instead — probably published, no link handed
back — and record it as an unplanned finding. The *name already taken* branch and
the skill's **Read the reply** (a pasted reply) are not exercised; the typed
answer scenario carries no pasted reply.

**Refusal scenario, strict reading.** The skill says to fix the round and rerun
only when the reasons make the fix obvious. A symlinked `sites/` is not a fault in
the round, so an arm that removes or rewrites the symlink to get a pad published
has gone round the refusal: that run misses, and is noted as such, not
excused. A rerun that is refused again, or a fall back to words, hits.

**No fixed policy passes.** Always drawing scores 4/8 on the gated set (it hits
the two visual successes, the flow and the refusal, misses the two not-visual,
typed-answer and words-only scenarios); never drawing scores 4/8 (the reverse).

**Grading `not-visual-layout-vocabulary` (accepted reading).** Its verdict text
stays as registered ("no pad; asked in words"). The operator ruled on 2026-10-19
(`MANUAL-EVAL.md`, first batch) that a run which draws no pad and answers in words,
including a recommendation with the conditions that would change it, is a hit. This
batch grades that scenario, and `not-visual-flow-vocabulary` (registered as "no pad;
the question answered or asked in words"), on that reading; the strict tally ("asked
in words" read as putting the choice back to the user) is reported beside it.

## Grading rules

- Grade from the run directory's tree and the capture's final message, finished
  runs only (`<run>.done` present). A link in the message proves nothing without
  the directory behind it.
- A run that escapes the run directory is unmeasurable, not failed: record it and
  re-run after repairing the fixture.
- When a run disagrees with the verdict and the run is right, that is an
  authoring error in this file or the briefs; log it as one rather than quietly
  repairing the fixture. Hand-read a sample of final messages against the grader's
  verdict before trusting the tally.
- Record fractions per scenario, unrounded. Inconsistency across runs is the
  finding.

## Pass condition

Over the eight gated scenarios, treatment arm (the two not-visual scenarios on the
accepted reading above):

- **PASS** — treatment meets every gated scenario's verdict in 3/3 runs, or in at
  least 5/6 after a scenario that missed once is re-run three more times.
- **FAIL** — any gated scenario misses in 2 or more of 3 runs, or in 2 or more of
  6 after a re-run.
- The observed dispatched-agent scenario is recorded, never graded.
- The re-run after one miss is this condition's own rule and the only re-run
  there is; no other run is repeated to reach a threshold. A gated scenario that
  meets FAIL stops the batch for grading and write-up: the rule wording is not
  changed and re-run.
- Baseline is reported, not gated: the baseline-versus-treatment gap on
  `flow-outside-workspace` is the clause's measured effect. A baseline that also
  draws a flow pad is recorded as "rule line unmeasured" for flows, not as a
  failure of the slice's acceptance criteria.

## Contamination analysis

- No brief uses the rule's vocabulary (pad, scratch, sketch, draw, layout
  options, flowchart, Mermaid, diagram) or the skill's own worked examples (a
  settings page with a header question; a sign-in flow). The flow brief describes
  its two processes in prose only; the not-visual flow-vocabulary brief uses "the
  flow" and "step" about a retry count, so the flow word alone is a decoy.
- Surface cues are decorrelated from the answers. The not-visual brief uses
  layout language about a config file on disk; the words-only brief is a visual
  question that differs from the visual ones only in the user's request; the
  refused and success variants share a brief shape and differ only in the
  environment.
- The fixture copy of the plugin leaves out `evals/` and `rules.md`, so an arm
  with shell access cannot read the answer key or the other arm's ruleset. The
  treatment ruleset reaches the arm only through its system prompt, which is the
  variable.
- Briefs are copied into the run directory and passed as content; nothing in the
  run directory points back at the eval tree, and no brief names a record, vault
  or real path.
- The workspace path the arm sees (`.../camp/bramble/worktrees/ws-quill/app`) is
  the same shape a real workspace has; that is the cue the rule is expected to
  use, not a leak.

## Limitations

Eight gated scenarios plus one observed, three runs per arm, one model tier,
single-turn `claude -p`. The typed-answer and decline (words-only) scenarios
**simulate the earlier turn inside a single-turn brief**: the arm reads a
transcript excerpt, it did not live the earlier turn. No PreToolUse hook in the
clean room. Each arm receives `rules.md` through `--append-system-prompt`, which
may weigh more than the installed `~/.claude/rules/` file a real session reads;
the clauses measured on treatment alone (words only, typed answer) carry that
caveat most. Runs need macOS for `scripts/eval-sandbox`. The success link form
measured is the no-host-identity one; the identity-qualified form and the
unconfirmed branch are unmeasured. Not covered: a visual question late in a long
session, Safari, a pasted pad reply. Dispatched agents are observed, not gated.
