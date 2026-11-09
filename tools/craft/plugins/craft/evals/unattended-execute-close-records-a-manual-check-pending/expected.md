# unattended-execute-close-records-a-manual-check-pending

## Under test

Under `--unattended --then-pr`, execute at Phase 6 on a slice whose `**Covers:**` includes a criterion (AC2) with a `manual-check` observation and no attestation. The run asks the operator nothing and never writes an attestation. It takes the pending path the manual-check discharge prescribes for "Not yet": it drafts `check` and `how`, judges `changed_behaviour`, runs `pending_check.py` once naming only AC2, runs `verify`, which puts the `## Waiting` entry on the spec and moves AC2 to `**Partially covers:**` with a `pending-check` observation, and logs the call as a numbered decision in the block. Then the observation gate passes and the close write lands (`done`). The prose under test is `skills/_shared/execute.md`, Phase 6's manual-check discharge and the Loop stops, and `skills/_shared/unattended.md`.

## Fixture

The close-defers cases' fixture approach, with an unattended parent. `fixtures/scaffold.sh` seeds a `ready` spec `export-invoices` (AC1 automated, AC2 `*Verified by: manual check.*`) and an `in-progress` slice parent `export-invoices-as-csv` (labels `craft/slice-parent` and `craft/branch`, related to the spec) that covers AC1 and AC2, carries `## Criterion observations` (`automated-assertion` for AC1, `manual-check` for AC2), no `## Operator attestations`, `## End Phases` ticked through Phase 5, a ticked flow-out, and an open `## Unattended run` block. `./lore`, `./scripts/pending_check.py`, `./scripts/observation_gate.py` and `./scripts/found_work.py` are the same stand-ins as the close-defers cases (real script copies run against `bin/lore`); `lore-calls.log` is the ordered log of everything the run called, and the wrapper copies the `--entries` file into `pending-check-entries.log` at call time. Unlike the close-defers prompt the operator gives no replies: the run is unattended, so the prompt names none and the procedure must not ask for any. The run resumes at Phase 6 with every push settled (nothing to push), omits the run-metrics row and the tally, and may not run git.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 50`, `timeout_seconds: 600`. The prompt asks for single-command Bash calls. The runner's dispatch tool is named `Task`, so "no agent dispatched" is `tool_used: Task` (the case grants no dispatch tool either way).
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `b23ab548aed10e21d97008c83daf2f156be02f94`, `skills/_shared/unattended.md` `6a97ad1ca3f7ee3a8b6fdd0d97b9e4a776befa84`, `skills/execute/SKILL.md` `654b9976493d76660857b4fb86546bcb260f5cea`; and a **treatment** arm on the edited prose. 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless) backed by record state and, where the procedure ends the session, the outcome line. Everything that decides what was written is a regex over the seeded vault files or a script log with `source: file`, never a `tool_used` on Bash, except the two "no git command" and "no agent" checks, which are `tool_used` by design.
- Graders that match a command match only the state-changing form: `no-git-command-issued` is `\bgit\b[^"]*\b(switch|checkout|branch|cherry-pick|fetch|worktree|commit|push)\b`, and a grader that matches a call to `pending_check.py` excludes its read-only `describe` mode.
- Fixture vocabulary leak check, run before any scored run: the seeded parent holds no `Partially covers`, `pending-check`, `Operator attestations`, numbered decision or `Choice:` line; the seeded spec holds no `## Waiting`. The graded files are the spec's and parent's bodies, the parent's sidecar, `lore-calls.log` and `pending-check-entries.log`.
- Expected **red** on the baseline: the unedited prose asks the operator the three-way question, and with no replies the run asks or stops, so no entries file is written, `pending_check.py` never runs, AC2 stays under `**Covers:**`, the gate refuses, the parent stays `in-progress` and no decision is logged (every clause but `no-attestation-written`, `entries-omit-ac1` and `writer-ran-once` fails).

## Pass condition

Every clause holds.

| Clause | Grader | Target |
|---|---|---|
| no attestation was written | `no-attestation-written` | parent body, not_contains |
| the writer ran, once | `writer-ran`, `writer-ran-once` | `lore-calls.log` |
| its entries name AC2 and not AC1 | `entries-name-ac2`, `entries-omit-ac1` | `pending-check-entries.log` |
| the spec carries AC2's waiting entry | `spec-carries-the-waiting-entry` | spec body |
| AC2 moved to `**Partially covers:**` and carries a `pending-check` line | `criterion-moved-to-partially-covers`, `parent-carries-the-pending-check-line` | parent body |
| a numbered block decision naming AC2, with question, choice and reasoning | `block-decision-logged` | parent body |
| order: writer, then verify, then the close write | `writer-then-verify-then-close` | `lore-calls.log` |
| the close landed | `parent-is-done` | parent sidecar |
| no question to the operator; no agent; no git command | `asked-nothing`, `no-agent-dispatched`, `no-git-command-issued` | |

### Each pass line against each branch, checked before any arm ran

The graders were run offline against the end state a hand-run of the real writer, `verify` and a close write leaves in a scaffolded directory: every record and log clause passes, and the decision clause passes once the block entry is added.

| Branch | no attestation | writer once | entries AC2 only | spec entry | moved, pending line | block decision | order | done | Whole run |
|---|---|---|---|---|---|---|---|---|---|
| Today's (asks, no replies) | pass | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | fails |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Attests AC2 with invented words | **fail** | pass | pass or fail | pass or fail | pass or fail | pass or fail | pass or fail | pass or fail | fails |
| Pending path done, decision not logged | pass | pass | pass | pass | pass | **fail** | pass | pass | fails |
| Closes without the writer | pass | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | pass if the gate lets it | fails |

## Limitations

- One spec, one phrasing, one model tier, three runs. The "Not yet" path is the only one the unattended run takes, so the stale-spec and writer-failure branches are not exercised here (the close-defers cases cover them attended).
- `changed_behaviour` and the drafted `check`/`how` are judged by the run; the graders read that they are present, not that they are good.
- The `mktemp` entries file's removal is not pinned: the runner grants no `rm`.
- The completion report's manual-check block is not graded here.
