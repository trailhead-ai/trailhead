# execute-builds-against-a-stand-in-when-one-exists

## Under test

The other side of the build-time set-aside rule in `skills/_shared/execute.md` "Handling Executor Status": when the executor reports `BLOCKED` on an outside service that offers a test mode or sandbox a test double can stand in for, the controller does not set the criterion aside and does not write `blocked`; it re-dispatches the executor with the stand-in as context. Spec criterion: AC16 (a criterion that can be built against a stand-in is not set aside). The prose under test is "Handling Executor Status" as it stands when the baseline runs and after the edit.

This case is a **guard, not a detector**, with one honest qualification. The unedited prose has no set-aside rule, and its first branch for BLOCKED is "context problem, provide more context, re-dispatch", which is what this report calls for, so the baseline is expected green. It exists to catch an edit that makes the rule over-eager, and its mutation check inverts the rule's stand-in clause to show it can go red. If the baseline instead escalates and writes `blocked` (the `blocked` clauses go red), that is a finding about today's prose, recorded as such, not a failure of the case.

## Fixture

`fixtures/scaffold.sh` seeds the same shape as the sibling case: spec `invoice-members` with AC1 (PDF copy of a past invoice) and AC2 (each invoice shows the sales tax the payment provider's tax service calculates for the member's address), with notes saying that service has a test mode with sample keys and a documented sample response a fake can replicate; an `in-progress` slice parent covering AC1 and AC2; one `in-progress` child. The prompt resumes at "Handling Executor Status" with a verbatim `BLOCKED` report: the executor has no keys for the tax service and did not want to guess the response shape. The stand-ins and the prompt's wording are the sibling case's (the scripts are copied into this case's scaffold, not shared); `scripts/set_aside.py` logs its call and runs the real script.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, grants from craft's `eval-gate.json` runner block.
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 540`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs at 1.0, before and after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. Each batch is three single-run invocations.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The writer never ran | `no-writer-call` | `regex`, not_contains, on `lore-calls.log` |
| The spec has no `## Waiting` section | `spec-has-no-waiting-section` | `regex`, not_contains, on the spec's body |
| The parent still covers AC2 | `parent-still-covers-ac2` | `regex` on the parent's body |
| The parent was not written `blocked` | `parent-is-not-blocked` | `regex`, not_contains, on the parent's sidecar |
| The child was not written `blocked` | `child-is-not-blocked` | `regex`, not_contains, on the child's sidecar |
| No lore call wrote `--status blocked` | `no-blocked-write-was-made` | `regex`, not_contains, on `lore-calls.log` |
| The report says it would re-dispatch against a stand-in, not set aside, not escalate | `reply-redispatches-against-a-stand-in` | `llm`, `focus: trace` |

### Each clause against each branch, checked before any run

| Branch | no writer | no Waiting | parent covers AC2 | parent not blocked | child not blocked | no blocked call | reply | Whole run |
|---|---|---|---|---|---|---|---|---|
| Re-dispatches against the stand-in (today's prose and edited prose alike) | pass | pass | pass | pass | pass | pass | pass | passes |
| Over-eager: sets AC2 aside | **fail** | **fail** | **fail** | pass | pass | pass | **fail** | fails |
| Writes `blocked` on the task or parent (today's escalation path) | pass | pass | pass | **fail** or pass | **fail** or pass | **fail** | **fail** | fails |
| Sets AC2 aside and also writes `blocked` | **fail** | **fail** | **fail** | pass or **fail** | **fail** | **fail** | **fail** | fails |
| Stops without deciding (neither re-dispatch nor a write) | pass | pass | pass | pass | pass | pass | **fail** | fails |

## Limitations

- The `BLOCKED` report is scripted; the case tests only the controller's reaction, not whether a real executor would block on a criterion with a test mode.
- Whether a stand-in exists is stated in the spec's notes; a real controller would have to judge it from less. The case does not exercise a borderline service.
- No agent is dispatched, so "re-dispatch against a stand-in" is judged from the one line the agent says it would send; the mechanical clauses pin only what was not written.
- One spec, one phrasing, one model tier, three runs, one Linux host. The `llm` clause judges the first and last 12 events of a run.
