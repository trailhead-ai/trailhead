# waiting-checks-hold-the-loop-open

## Under test

When the slice ritual's candidate set is empty and the gate's `waiting:` token names a criterion, the pass does not report the spec complete and does not record the loop as stopped: it reports how many criteria are waiting on checks, names each by its own words with its number after, prints the command that lists them, and hands off to `/craft:checks` and then re-running `/craft:slice spec/<id>`. It writes no `craft/slice-loop` label and no body note, so a driver that halts on it halts with a visible reason and the spec's loop state is untouched (the spec's loop-end rule: a criterion waiting on a manual check keeps the loop open without ending it). The prose under test is `skills/slice/SKILL.md` (steps 4 and 6, and the Outcome handoff), as it stands when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `print-shipping-labels` with `- **ACn.**` criteria AC1 to AC3, a `## Slices` ledger covering AC1 and AC2 in full and AC3 in part (a done slice that built the preview), two `done` slice parents with the `craft/slice-parent` label, and a `## Waiting` section holding one pending check for AC3 whose latest history line is `waiting`. The gate output for it is `candidates: none`, `partial: AC3`, `waiting: AC3`, `complete-eligible: no`. The `./lore` wrapper appends every call's arguments to `lore-calls.log` in the run directory (truncated after the seed), so graders read what the run wrote to the vault from the call log and the spec's sidecar, not from the reply. The prompt runs `/craft:slice spec/print-shipping-labels`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` runner block.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 40`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the unedited slice prose before any prose edit; it is expected red, because that prose takes the early-stop path for an empty candidate set with `complete-eligible: no`, which writes `craft/slice-loop=stopped` and an amend and prints no `/craft:checks`.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The run reached the gate: the call log holds `record criteria` | `reached-the-gate` | `regex` on `lore-calls.log`, contains |
| No completion write | `no-completion-write` | `regex` on `lore-calls.log`, `craft/slice-loop=complete`, not_contains |
| No stop write | `no-stop-write` | `regex` on `lore-calls.log`, `craft/slice-loop=stopped`, not_contains |
| No amend | `no-amend` | `regex` on `lore-calls.log`, `record amend`, not_contains |
| No record created | `no-record-create` | `regex` on `lore-calls.log`, `record create`, not_contains |
| The spec carries no loop label | `spec-carries-no-loop-label` | `regex` on `vault/spec/print-shipping-labels.json`, `craft/slice-loop`, not_contains |
| The reply hands off to the check list | `hands-off-to-checks` | `regex` over the final message, `/craft:checks` |
| The reply names the waiting criterion by its words with AC3, says it is waiting, and neither calls the spec complete nor stopped | `reply-names-the-waiting-criterion` | `llm`, `focus: trace` |

### Each pass line against each branch, checked before any arm ran

| Branch of the prose | `reached-the-gate` | `no-completion-write` | `no-stop-write` | `no-amend` | `no-record-create` | no loop label | `hands-off-to-checks` | reply clause | Whole run |
|---|---|---|---|---|---|---|---|---|---|
| Complete (writes `craft/slice-loop=complete`, hands off to tidy-followups) | pass | **fail** | pass | pass | pass | **fail** | **fail** | **fail** | fails |
| Stopped (writes `craft/slice-loop=stopped` and an amend, hands off to `/craft:slice`) | pass | pass | **fail** | **fail** | pass | **fail** | **fail** | **fail** (reports a stop, no waiting count) | fails |
| Waiting (no writes, `/craft:checks` then re-run) | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Failed-entry arm's path, selecting AC3 (the other case) | pass | pass | pass | pass | **fail** if the pass materializes the parent, else pass | pass | **fail** (no `/craft:checks`) | **fail** (says nothing waits) | fails |

Only the waiting branch satisfies every clause; each of the other three fails at least three. The write clauses read the call log, which records a write whether or not the reply mentions it. `no-record-create` and `hands-off-to-checks` are what separate the waiting branch from the selection path.

## Limitations

- One spec, one phrasing, one model tier, three runs.
- The call log records arguments only, not piped bodies; the sidecar grader covers the label independently of the log.
- The reply clause is an `llm` judgement over the first and last 12 events of the run; the clauses that decide the write behaviour are mechanical.
- Drive's halt on this outcome is not run here; drive writes nothing of its own and reports slice's early-stop text.
- The companion case `failed-check-returns-its-criterion-to-the-candidates` covers the same spec with the entry `failed`.
