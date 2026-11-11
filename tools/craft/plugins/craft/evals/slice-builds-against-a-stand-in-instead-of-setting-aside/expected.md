# slice-builds-against-a-stand-in-instead-of-setting-aside

## Under test

The other side of the set-aside rule in `skills/slice/SKILL.md` step 7: a candidate whose outside service can be replaced by a stand-in (a fake, a test double, a sandbox) for building and testing is a criterion the pass chooses as usual; the pass sets nothing aside. The prose under test is the rule's boundary in `skills/slice/SKILL.md` steps 7 to 9, as it stands when the baseline runs and after the edit. Spec criterion: AC16 (a criterion that can be built against a stand-in is not set aside).

This case is a **guard, not a detector**: the baseline prose has no set-aside rule, so it is expected to choose a slice and set nothing aside, and the baseline is expected green. It exists to catch an edit that makes the rule over-eager, and its mutation check inverts the rule's stand-in clause to show it can go red.

## Fixture

`fixtures/scaffold.sh` seeds the same shape as the sibling case: a `ready` spec `bill-members-monthly` with no slices, AC1 first, AC2 and AC3 plainly buildable. AC1 asks for each invoice to show the sales tax the payment provider's tax service calculates for the member's address, and the spec's notes say that service has a test mode with sample keys and a documented sample response, so tests can stand in for it with a fake that returns the same shape. The stand-ins and the prompt are the sibling case's (the scripts are copied into this case's scaffold, not shared); `scripts/set_aside.py` logs its call and runs the real script.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, grants from craft's `eval-gate.json` runner block plus `Bash(*/scripts/set_aside.py *)`.
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 540`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs at 1.0, before and after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. Each batch is three single-run invocations.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The writer never ran | `no-writer-call` | `regex` on `lore-calls.log`, `set_aside.py`, not_contains |
| The spec has no `## Waiting` section | `spec-has-no-waiting-section` | `regex` on the spec's body, not_contains |
| A slice parent was created | `a-slice-was-created` | `regex` on `lore-calls.log`, `record create --kind task` |
| The reply states a chosen slice and says nothing was set aside or waits on an outside service | `reply-states-a-slice-and-sets-nothing-aside` | `llm`, `focus: trace` |

### Each pass line against each branch, checked before any arm ran

| Branch | no-writer-call | no waiting section | slice created | reply | Whole run |
|---|---|---|---|---|---|
| Builds against the stand-in (chooses AC1, AC2 or AC3) | pass | pass | pass | pass | passes |
| Over-eager: sets AC1 aside | **fail** | **fail** | pass | **fail** | fails |
| Sets AC1 aside and then stops without choosing | **fail** | **fail** | **fail** | **fail** | fails |
| Refuses or stops before creating a slice | pass | pass | **fail** | **fail** | fails |

## Limitations

- One spec, one phrasing, one model tier, three runs, one Linux host. It pins only that a fakeable service is not set aside; it does not show the judgement on a criterion that is partly fakeable.
- The `llm` clause judges the first and last 12 events; the other clauses read the call log and the vault.
