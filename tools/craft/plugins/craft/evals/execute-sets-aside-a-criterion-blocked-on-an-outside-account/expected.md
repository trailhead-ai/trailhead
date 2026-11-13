# execute-sets-aside-a-criterion-blocked-on-an-outside-account

## Under test

When a build session's executor reports `BLOCKED` because a criterion of the slice needs a real outside account, business setup or real-world event that no stand-in (fake, test double, sandbox) can replace, `skills/_shared/execute.md` "Handling Executor Status" sets that criterion aside rather than writing `blocked` on the task or the parent: it runs `set_aside.py --spec <n> --parent <slice parent> --entries <path> --vault <v>`, so the spec gains a `## Waiting` set-aside entry and the parent's `**Covers:**` drops the criterion; it re-scopes (or drops) the child with a note naming the waiting entry; and the final report names the criterion by its own words and what it waits on. Spec criteria: AC15 (a build that finds a criterion waiting on something outside the work sets it aside, with what it waits on, and goes on), AC19 (the loop does not stall: no `blocked` write). The prose under test is `skills/_shared/execute.md` "Handling Executor Status" as it stands when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `invoice-members` with two criteria (AC1 a PDF copy of a past invoice; AC2 sales tax collected under the company's live registration with the payment provider's tax service, with notes saying the company has not applied, the provider issues it after a weeks-long verification, and there is no test version), an `in-progress` slice parent `members-can-download-invoices-with-sales-tax` (labels `craft/slice-parent`, `craft/branch=<its name>`, related to the spec, `**Covers:** AC1, AC2`) and one `in-progress` child `invoice-pdf-with-a-sales-tax-line`. The prompt resumes the session at "Handling Executor Status" with the executor's `BLOCKED` report stated verbatim. It grants no `Agent`, forbids dispatching agents (say in one line what you would send and go on), and does not say "set aside" or name the writer.

Stand-ins: `./lore` logs each call's arguments to `lore-calls.log` and runs the real lore CLI over a throwaway vault. `scripts/ledger_gate.py`, `candidate_set.py`, `covers_gate.py`, `found_work.py`, `pending_check.py` and `observation_gate.py` log their arguments and run real copies of craft's scripts against a `bin/lore` reaching the same vault. `scripts/set_aside.py` logs its arguments, copies the `--entries` file into `set-aside-entries.log`, then `exec`s the real `set_aside.py`, so the sanctioned path really writes the spec and narrows the parent. A prose that never runs the writer leaves its log empty.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, grants from craft's `eval-gate.json` runner block (which already carries `Bash(*/scripts/set_aside.py *)`).
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 540`. Single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs at 1.0 after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. Each batch is three single-run invocations.
- The baseline runs on the unedited prose before any edit and is a **detector**: the current prose sends a BLOCKED that survives steps 1 to 3 to a `blocked` write, so the set-aside clauses and the no-blocked clauses are expected red.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The spec carries a `set aside` entry for AC2 under `## Waiting` | `spec-gains-the-ac2-set-aside-entry` | `regex` (dot-all) on `vault/spec/invoice-members.md` |
| The parent's `**Covers:**` line no longer lists AC2 | `parent-no-longer-covers-ac2` | `regex`, not_contains, on the parent's body |
| The parent's `**Covers:**` line still lists AC1 | `parent-still-covers-ac1` | `regex` on the parent's body |
| The parent was not written `blocked` | `parent-is-not-blocked` | `regex`, not_contains, on the parent's sidecar |
| The child was not written `blocked` | `child-is-not-blocked` | `regex`, not_contains, on the child's sidecar |
| No lore call wrote `--status blocked` | `no-blocked-write-was-made` | `regex`, not_contains, on `lore-calls.log` |
| The writer ran with `--parent` | `writer-ran-with-parent` | `regex` on `lore-calls.log` |
| The child was updated (re-scoped or dropped) | `child-was-rescoped-or-dropped` | `regex` on `lore-calls.log` |
| The child's note says why and names the waiting entry | `child-note-names-the-waiting-entry` | `llm`, `focus: trace` |
| The report names AC2 by its words as set aside and what it waits on | `reply-names-ac2-and-what-it-waits-on` | `llm`, `focus: trace` |

### Each clause against each branch, checked before any run

| Branch | spec entry | parent no AC2 | parent has AC1 | parent not blocked | child not blocked | no blocked call | writer --parent | child updated | child note | reply | Whole run |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Today's prose: writes `blocked` on the task and/or parent | **fail** | **fail** | pass | **fail** | **fail** or pass | **fail** | **fail** | pass or **fail** | **fail** | **fail** | fails |
| Today's prose: re-dispatches or escalates, writes nothing | **fail** | **fail** | pass | pass | pass | pass | **fail** | **fail** | **fail** | **fail** | fails |
| Edited, done right (writer with `--parent`, child re-scoped or dropped with a note naming the entry, report names AC2) | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Wrong edit: sets AC2 aside and also writes `blocked` on the task | pass | pass | pass | pass or **fail** | **fail** | **fail** | pass | pass | pass | **fail** if it says blocked | fails |
| Wrong edit: sets AC2 aside without `--parent` (spec only; the parent still claims AC2) | pass | **fail** | pass | pass | pass | pass | **fail** | depends | depends | depends | fails |
| Wrong edit: sets AC2 aside but leaves the child untouched | pass | pass | pass | pass | pass | pass | pass | **fail** | **fail** | pass | fails |

Only the edited prose done right satisfies every clause. The mechanical clauses read the vault files and the call log, not the reply.

## Limitations

- The `BLOCKED` report is scripted. The case tests only the controller's reaction to that report, not whether a real executor would block on this criterion, nor whether the controller would reach the BLOCKED branch in a real build.
- One spec, one phrasing of the dependency, one model tier, three runs, one Linux host. No agent is dispatched, so the child's note is judged from the call and its arguments the `llm` clause can see; the clause on the child's update is mechanical and the note's contents are not.
- The judgement on a borderline criterion is not exercised; the sibling `execute-builds-against-a-stand-in-when-one-exists` is the other side of the rule.
- The `llm` clauses judge the first and last 12 events of a run; the clauses that decide what was written are mechanical.
