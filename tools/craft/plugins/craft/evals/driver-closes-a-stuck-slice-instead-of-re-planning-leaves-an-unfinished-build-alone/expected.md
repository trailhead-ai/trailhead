# driver-closes-a-stuck-slice-instead-of-re-planning-leaves-an-unfinished-build-alone

## Under test

Step 4 of `skills/drive/SKILL.md`, the same open-parent branch as `driver-closes-a-stuck-slice-instead-of-re-planning`, when the detector does not certify the slice: `pending_check.py stuck` exits 1 with `build-unfinished` and the driver proceeds exactly as before, resuming the slice rather than closing it. This is a regression and paired control for the stuck arm.

## Fixture

The stuck-arm fixture, except one child task of the slice parent is `ready`, not `done`. The prompt and its bound are the stuck arm's.

## Baseline-default check

The unedited prose never runs the detector and proceeds to step 5 for this parent; the treated prose runs the detector, reads exit 1 and proceeds to step 5 too. The two must agree on every grader below, so the baseline is also expected green. That the detector ran in the treated arm is read from `lore-calls.log` and reported, not graded, so the baseline stays comparable.

## Conditions, sample and bar

As the stuck arm: 3 runs, `-j 3`, `--ablation none`, `claude-opus-5-5`. Treatment bar 3 of 3 at 1.0; baseline expected 3 of 3 at 1.0 as well. One rerun of a failed batch permitted, reported as rerun-green.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The waiting writer did not run | `writer-not-run` | `regex` on `lore-calls.log`, not_contains `pending_check.py --spec` |
| The parent is still `in-progress` | `parent-still-in-progress` | `regex` on the parent's `.json` |
| The spec carries no waiting entry | `spec-has-no-waiting-entry` | `regex` on the spec body, not_contains `## Waiting` |
| The run says it is handing the open slice on to the single-repo check (step 5), not closing it | `hands-on-to-step-5` | `llm`, trace |

## Limitations

Three runs, one model tier, one non-stuck reason (`build-unfinished`); the other reason codes share the same exit-1 branch and are not each exercised.
