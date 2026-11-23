# Set-aside branch-load A/B

One-time experiment, not a gate case: none of its cases is in `evals/eval-gate.json`, and the folder holds no
`case.yaml`, so the gate never selects or counts them. Written before any arm is run. It re-asks the question of
[`../ab-found-work-branch-load/expected.md`](../ab-found-work-branch-load/expected.md) on new material, because
that experiment came out void (its inline arm failed an unrelated report-order grader, so it could not rank the
arms).

## Verdict

**PASS (2026-11-23).** On both models the branch arm matched the inline arm, met the absolute floor, and every
branch-arm run read the branch file. This block is the one place the result is recorded; the manual eval log and
the criterion observation cite it.

| Cell | Arm | Model | Runs | Passed (score 1.0) | Read the branch file | Reached the decision point |
|---|---|---|---|---|---|---|
| fixture (a) `ab-set-aside-a-facts-in-the-report` | inline | Sonnet 5.5 | 5 | 5 | n/a (0 reads) | 5 |
| fixture (b) `ab-set-aside-b-facts-on-the-record-only` | inline | Sonnet 5.5 | 5 | 5 | n/a (0 reads) | 5 |
| fixture (a) | branch | Sonnet 5.5 | 5 | 5 | 5 | 5 |
| fixture (b) | branch | Sonnet 5.5 | 5 | 5 | 5 | 5 |
| fixture (a) | inline | Opus 5.5 | 3 | 3 | n/a (0 reads) | 3 |
| fixture (a) | branch | Opus 5.5 | 3 | 3 | 3 | 3 |
| fixture (c) `ab-set-aside-c-context-problem` (disclosure) | branch | Sonnet 5.5 | 5 | 4 | 5 | 1 |

- **Against the bar.** Sonnet: inline 10/10 (not void), branch 10/10, at least inline minus 1 and at least 8/10.
  Fixture (a) 5/5 against 5/5 and fixture (b) 5/5 against 5/5, reported separately. Opus: inline 3/3 (not void),
  branch 3/3. Every one of the 13 branch-arm runs read `_shared/waiting-criterion.md` once (the `tool_used`
  grader, and `ab.py count-reads` on the kept transcript) and reached the decision point (a `set_aside.py ` line
  in `lore-calls.log`). No runner error, no rerun.
- **Fixture (c), disclosed, cannot rescue or sink the verdict.** The condition is false (the executor's BLOCKED
  names no covered criterion), yet all 5 branch-arm runs read the branch file: the wrong-load count is 5 of 5. In
  4 of them the run then applied the section's own rule and made no set-aside; in 1 it called the set-aside
  writer and failed `no-set-aside-was-made`. No inline run of (c) was registered, so this does not say whether
  the same text inline would mis-fire at the same rate. It does say the load directive's condition was not used
  as a gate: on this material the agent loaded the branch whenever an executor BLOCKED arrived.
- **Mechanism observed.** Every run in every cell read its own arm copy's `skills/_shared/execute.md` and never
  the live file, checked from the kept transcripts.
- **What 10 runs per arm can and cannot detect.** Against 10 of 10 it would have shown a branch arm at 6 of 10 or
  worse (one-sided Fisher exact p = 0.043); it cannot tell 10 of 10 from 8 or 9 of 10 (p = 0.24 and 0.50). A
  real drop of one or two runs in ten would not be visible here.
- **Bar.** Fingerprint `9fc8f9010afa890dd4d747244ede8b90ec35b85f` before the first cell, after the last, and
  before this verdict; the bar did not move.
- **Decision asked for.** Narrow `adr/extraction-is-permitted-when-every-citation-is-unconditional` to permit
  a conditional read directive at the point its condition is tested, one level deep (spec AC32). The operator
  applies it, or declines, through the record-correction path.

## Under test

Whether an agent that reaches a decision point in a long procedure loads and follows a branch file as reliably
as it follows the same text held inline. The text is `### A criterion waiting on something outside the work` of
`skills/_shared/execute.md` (84 lines, under `## Handling Executor Status`; run only when an executor reports
`BLOCKED` and the blocker names a covered criterion that needs something outside the work). It has two citations,
and they are what change between the arms: step 6 of the BLOCKED assessment (the decision point itself) and the
completion report's set-aside paragraph. The procedure's contents list also names the section (not a citation).

## Arms

| Arm | What the run reads as `skills/_shared/execute.md` |
|---|---|
| `inline` | `arms/inline/execute.md`, a byte copy of the live procedure at the time the arms were cut |
| `branch` | `arms/branch/execute.md`: the section removed, each of its 2 citations rewritten to "When <condition>, read `../_shared/waiting-criterion.md` now and follow it in full." at that site; plus `arms/branch/waiting-criterion.md`: one opening line saying when it applies, a blank line, then the section byte-for-byte |

`python3 ab.py check` holds the arm diff to exactly that (the moved text byte-identical; every other change a
load line replacing a citation; no citation left). Not caught by it, and disclosed: the moved text keeps its
in-file anchors (`[Drop the task](#handling-executor-status)`, `[Phase 6](#phase-6-close-and-completion-report)`),
which no longer resolve inside the branch file, and the contents list still names the section.

Each arm runs against a temp copy of the craft plugin made without following symlinks (`ab.py prepare`), with
that arm's procedure swapped in. The live `skills/` tree is never edited. The driver logic is the first
experiment's `ab.py`, shared; this folder's `ab.py` only names the section, branch file, load sentence and citation.

## Fixtures (one case folder each, names unique across the craft evals tree)

| Case | Where the facts that decide the condition are | Role |
|---|---|---|
| `ab-set-aside-a-facts-in-the-report` | in the scripted executor report (the instrument's prompt, unchanged) | in the bar |
| `ab-set-aside-b-facts-on-the-record-only` | only on the spec record's Notes; the report says AC2 "cannot be built or tested from where I am" and points at the spec | in the bar |
| `ab-set-aside-c-context-problem` | condition false: the executor is BLOCKED on a context problem (which of two invoice layouts to follow) and names no covered criterion | disclosure only, never in the bar |

All three come from `execute-sets-aside-a-criterion-blocked-on-an-outside-account` (same seeded `./lore`, spec,
slice parent and child, logging script wrappers; `scripts/set_aside.py` logs its call and runs the real writer).

Fixture (b), and what the instrument already did. The instrument's prompt never says "set aside" and never says the
condition holds: it gives the executor's report and leaves the judgement (an outside account, no stand-in) to the
run. So fixture (a), the prompt as is, already has the run test the condition; the report just hands it the
facts. Fixture (b) is the closest variant that still varies how the condition is presented: the report names AC2
as blocked and gives no reason, and the run must open the spec record to find that the registration is not
applied for, takes weeks, and has no test version. The scaffold, spec, records and graders are the same as (a).

## Graders

The ten graders of the instrument, copied into (a) and (b): `spec-gains-the-ac2-set-aside-entry`,
`parent-no-longer-covers-ac2`, `parent-still-covers-ac1`, `parent-is-not-blocked`, `child-is-not-blocked`,
`no-blocked-write-was-made`, `writer-ran-with-parent`, `child-was-rescoped-or-dropped` (mechanical, on the vault
files and `lore-calls.log`), and two `llm` graders on the trace, `child-note-names-the-waiting-entry` and
`reply-names-ac2-and-what-it-waits-on`. Plus `branch-file-was-read` on (a) and (b), a `tool_used` grader (tool
`Read`, input matching `_shared/waiting-criterion\.md`, 1 to 20 calls). The driver removes it from the inline
arm's copy of the case, where there is no branch file. Fixture (c) carries one grader, `no-set-aside-was-made`
(no `set_aside.py` call in `lore-calls.log`), and its reads of the branch file are counted from the kept trace
with `ab.py count-reads`, not graded.

## Conditions

- Models: Sonnet `claude-sonnet-5-5` (primary), Opus `claude-opus-5-5` (confirms; the instrument's own registered model).
- Sample: Sonnet, fixtures (a) and (b), 5 runs each, so 10 runs per arm. Opus, fixture (a), 3 runs per arm.
  Fixture (c): branch arm, Sonnet, 5 runs, disclosure only.
- Runner: `claude plugin eval` direct, `--ablation none`, `--scaffold`, `-j 1`, craft's runner grants (`ab.py
  grants`), limits from each case's `prompt.md` (`max_turns: 60`, `timeout_seconds: 540`).
- Per-run pass unit: a run passes only when every grader passes (score 1.0). That is 10 graders on the inline arm
  and 11 on the branch arm in (a) and (b).
- Reach marker: a run reached the decision point when its `lore-calls.log` holds a line that begins `set_aside.py `
  (the writer call, the first act of the section's rule; the scaffold's wrapper logs it before running the real
  writer). A branch-arm run that never reaches it counts as a fail, never as an exclusion. Reach is reported per
  cell.
- A scaffold error or a timeout is an error, logged apart from graded fails. Identical partial scores across
  runs are an environment failure, not a result: read a trace before believing a score.
- Unknown before any run: the instrument was registered against Opus (3 runs); no Sonnet result exists for it, so
  the inline arm may fall under the void floor for reasons of its own.

## Bar

<!-- bar-begin -->
Sonnet is primary: fixtures (a) and (b) at 5 runs each, 10 per arm. Opus confirms on fixture (a): 3 runs per arm.

- **A run passes** only when every grader passes (score 1.0).
- **Reaching the decision point** is the first set-aside writer call (a `set_aside.py ` line) in the run's
  `lore-calls.log`. A branch-arm run that never reaches it counts as a fail, never as an exclusion.
- **Void:** the inline arm scores under 8/10 on Sonnet, or under 3/3 on Opus. The instrument cannot rank the
  arms. The fallback material named below is offered to the operator.
- **Pass:** on each model, the branch arm scores at least inline minus 1 **and** at least 8/10 on Sonnet (3/3 on
  Opus) in absolute terms, and every branch-arm run read the branch file, as the `tool_used` grader on the Read
  records.
- **Fail:** any other result.
- **Reporting:** fixtures (a) and (b) are reported separately, so a pass on (a) alone cannot hide a miss on (b).
  Fixture (c)'s wrong-load count (branch-arm runs that read the branch file although the executor's blocker names
  no covered criterion) is printed on the verdict as a disclosure and cannot rescue a fail. The verdict states in
  plain words what difference 10 runs per arm can and cannot detect: against a clean 10 of 10 it can show a branch
  arm at 6 of 10 or worse (one-sided Fisher exact p = 0.043), and it cannot tell 10 of 10 from 8 or 9 of 10 (p =
  0.24 and 0.50); a branch arm at 8 of 10 passes the bar, so the bar tolerates a real drop it cannot detect.
- **No rerun to rescue a result.** One rerun of a batch with an environment failure (a scaffold error, or
  identical partial scores across runs) is allowed and disclosed.
- **The bar cannot move.** The fingerprint of this section (`ab.py bar | git hash-object --stdin`) is recorded
  before the first cell and re-checked before the verdict is written.
<!-- bar-end -->

Bar fingerprint (`git hash-object` of the text between the delimiters), recorded before any cell: `9fc8f9010afa890dd4d747244ede8b90ec35b85f`

## Fallback material, offered to the operator only if the result is void

`## Unattended request` of `skills/_shared/execute.md` (about 300 lines; runs only under `--unattended`; 1 link
citation plus the execute skill's own pointer). Instrument: `unattended-execute-close-ends-stuck-on-the-default-branch`.

## Running one cell

Run from a shell outside any Claude Code session sandbox: inside one, the eval's Bash calls fail with
`Sandbox is required but failed to initialize: EPERM ... srt-mux ... .sock` and every Bash-dependent grader scores
as a fail. TMPDIR must be outside `/tmp` and at most 51 characters. From the repo root:

```
E=tools/craft/experiments/ab-set-aside-branch-load
export TMPDIR=$(mktemp -d ~/.cache/ee.XXXXXXXX); out=$(mktemp -d ~/.cache/eo.XXXXXXXX)
arm=$(mktemp -d ~/.cache/ea.XXXXXXXX); rm -rf "$arm"
root=$(.venv/bin/python $E/ab.py prepare <inline|branch> <case name> "$arm/craft")
eval "timeout 580 claude plugin eval $root --case '<case name>' --scaffold --allow-tools $(.venv/bin/python $E/ab.py grants) \
  --ablation none --trust-plugin --runs <n> --model <model> --json $out/run.json --max-cost-usd 5 \
  --output-dir $out --no-publish --keep-temp"
```

`chmod 700 <kept dir> <kept dir>/sealed` to read `trace.jsonl` and `lore-calls.log`. `ab.py evidence <trace>
"$root" <arm>` shows the run read that arm's own procedure from the arm copy; `ab.py count-reads <trace>` counts
Reads of the branch file; the reach marker is `grep -c '^set_aside.py ' <kept dir>/lore-calls.log` (the kept
directory holds the scaffold's working files). Delete the arm copy, TMPDIR and the output directory after the
verdict.
