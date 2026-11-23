# Found-work branch-load A/B

One-time experiment, not a gate case: none of its cases is in `evals/eval-gate.json`, and the folder holds no
`case.yaml`, so the gate never selects or counts them. Written before any arm is run.

## Verdict

Pending. Nothing has been run. This block is the one place the result is recorded (verdict, per-cell counts,
mechanism observed, the decision asked for); the manual eval log and the criterion observation cite it.

## Under test

Whether an agent that reaches a decision point in a long procedure loads and follows a branch file as reliably
as it follows the same text held inline. The text is `## Found work` of `skills/_shared/execute.md` (about 146
lines, run only when the build's parent task is linked to a spec). Its 11 citations are what change between the
arms: three in the unattended end-phase stops and completion report, one in the per-task review, one each in the
Phase 3 triage, the unattended Phase 3 surfacing and the Phase 4 triage, two in Phase 5 (capture, settle), one in
the Phase 6 report order, and one in the executor-status concerns.

## Arms

| Arm | What the run reads as `skills/_shared/execute.md` |
|---|---|
| `inline` | `arms/inline/execute.md`, a byte copy of the live procedure at the time the arms were cut |
| `branch` | `arms/branch/execute.md`: `## Found work` removed, each of its 11 citations rewritten to "When <condition>, read `../_shared/found-work.md` now and follow it in full." at that site; plus `arms/branch/found-work.md`: one opening line saying when it applies, a blank line, then the section byte-for-byte |

`python3 ab.py check` holds the arm diff to exactly that (the moved text byte-identical; every other change a
load line replacing a citation; no citation left). Not caught by it, and disclosed: the moved text keeps its
in-file anchors (for example `[Phase 5](#phase-5-flow-out)`), which no longer resolve inside the branch file, and
the build procedure's contents list still names the section.

Each arm runs against a temp copy of the craft plugin made without following symlinks (`ab.py prepare`), with
that arm's procedure swapped in. The live `skills/` tree is never edited.

## Fixtures (one case folder each, names unique across the craft evals tree)

| Case | Spec link on the task record | Prompt says the task is linked to the spec | Role |
|---|---|---|---|
| `ab-found-work-a-spec-in-prompt` | yes | yes | in the bar |
| `ab-found-work-b-spec-on-record-only` | yes | no, the run must test the condition itself | in the bar |
| `ab-found-work-c-no-spec-link` | no | no | disclosure only, never in the bar |

All three come from `build-triages-found-work` (same review, same seeded `./lore`, same session id, same tool
limits). The scaffold copies the repo's lore plugin and seeds a throwaway vault; it creates no git repository,
so there is no `origin`.

## Graders

The seven graders of `build-triages-found-work` (the plan counted six; the case holds seven: the six clauses
of its pass table plus `no-found-work-question-was-asked`), copied into (a) and (b). Plus
`branch-file-was-read` on (a) and (b), a `tool_used` grader (tool `Read`, input matching
`_shared/found-work\.md`, 1 to 20 calls). The driver removes it from the inline arm's copy of the case, where
there is no branch file. Fixture (c) carries one grader, `no-found-work-decision-was-logged` (no `found-work: `
line in the session log), and its reads of the branch file are counted from the kept trace with
`ab.py count-reads`, not graded.

## Conditions

- Models: Sonnet `claude-sonnet-5-5` (primary), Opus `claude-opus-5-5` (confirms).
- Sample: Sonnet, fixtures (a) and (b), 5 runs each, so 10 runs per arm. Opus, fixture (a), 3 runs per arm.
  Fixture (c): branch arm, Sonnet, 5 runs, disclosure only.
- Runner: `claude plugin eval` direct, `--ablation none`, `--scaffold`, `-j 1`, craft's runner grants (`ab.py
  grants`), limits from each case's `prompt.md` (`max_turns: 40`, `timeout_seconds: 420`).
- Per-run pass unit: a run passes only when every grader passes (score 1.0). That is 7 graders on the inline arm
  and 8 on the branch arm in (a) and (b).
- Reach marker: a run reached the decision point when its session log
  (`vault/session/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.md`) holds a line that begins `found-work: `. A branch-arm
  run that never reaches it counts as a fail, never as an exclusion. Reach is reported per cell.
- A scaffold error or a timeout is an error, logged apart from graded fails. Identical partial scores across
  runs are an environment failure, not a result: read a trace before believing a score.

## Bar

<!-- bar-begin -->
Sonnet is primary: fixtures (a) and (b) at 5 runs each, 10 per arm. Opus confirms on fixture (a): 3 runs per arm.

- **A run passes** only when every grader passes (score 1.0).
- **Reaching the decision point** is the first found-work decision line (`found-work: `) in the run's session
  log. A branch-arm run that never reaches it counts as a fail, never as an exclusion.
- **Void:** the inline arm scores under 8/10 on Sonnet, or under 3/3 on Opus. The instrument cannot rank the
  arms. The fallback materials named below are offered to the operator.
- **Pass:** on each model, the branch arm scores at least inline minus 1 **and** at least 8/10 on Sonnet (3/3 on
  Opus) in absolute terms, and every branch-arm run read the branch file, as the `tool_used` grader on the Read
  records.
- **Fail:** any other result.
- **Reporting:** fixtures (a) and (b) are reported separately, so a pass on (a) alone cannot hide a miss on (b).
  Fixture (c)'s wrong-load count (branch-arm runs that read the branch file although the parent has no spec
  link) is printed on the verdict as a disclosure and cannot rescue a fail. The verdict states in plain words
  what difference 10 runs per arm can and cannot detect: against a clean 10 of 10 it can show a branch arm at
  6 of 10 or worse (one-sided Fisher exact p = 0.043), and it cannot tell 10 of 10 from 8 or 9 of 10 (p = 0.24
  and 0.50); a branch arm at 8 of 10 passes the bar, so the bar tolerates a real drop it cannot detect.
- **No rerun to rescue a result.** One rerun of a batch with an environment failure (a scaffold error, or
  identical partial scores across runs) is allowed and disclosed.
- **The bar cannot move.** The fingerprint of this section (`ab.py bar | git hash-object --stdin`) is recorded
  before the first cell and re-checked before the verdict is written.
<!-- bar-end -->

Bar fingerprint (`git hash-object` of the text between the delimiters), recorded before any cell: `e9be50bfa90a7f9e65e9485f9bfc6e704b35a464`

## Fallback materials, offered to the operator only if the result is void

Other conditional sections of `skills/_shared/execute.md`, each with an existing instrument:

1. `### A criterion waiting on something outside the work` (under Handling Executor Status; 2 citations; runs only
   when a criterion is blocked on an outside account). Instrument: `execute-sets-aside-a-criterion-blocked-on-an-outside-account`.
2. `## Unattended request` (about 300 lines; runs only under `--unattended`; 1 link citation plus the execute
   skill's own pointer). Instrument: `unattended-execute-close-ends-stuck-on-the-default-branch`.

## Running one cell

Run from a shell outside any Claude Code session sandbox: inside one, the eval's Bash calls fail with
`Sandbox is required but failed to initialize: EPERM ... srt-mux ... .sock` and every Bash-dependent grader scores
as a fail. TMPDIR must be outside `/tmp` and at most 51 characters. From the repo root:

```
E=tools/craft/plugins/craft/evals/ab-found-work-branch-load
export TMPDIR=$(mktemp -d ~/.cache/ee.XXXXXXXX); out=$(mktemp -d ~/.cache/eo.XXXXXXXX)
arm=$(mktemp -d ~/.cache/ea.XXXXXXXX); rm -rf "$arm"
root=$(.venv/bin/python $E/ab.py prepare <inline|branch> <case name> "$arm/craft")
eval "timeout 580 claude plugin eval $root --case '<case name>' --scaffold --allow-tools $(.venv/bin/python $E/ab.py grants) \
  --ablation none --trust-plugin --runs <n> --model <model> --json $out/run.json --max-cost-usd 5 \
  --output-dir $out --no-publish --keep-temp"
```

`chmod 700 <kept dir> <kept dir>/sealed` to read `trace.jsonl`. `ab.py evidence <trace> "$root" <arm>` shows the run
read that arm's own procedure from the arm copy; `ab.py count-reads <trace>` counts Reads of the branch file.
Delete the arm copy, TMPDIR and the output directory after the verdict.
