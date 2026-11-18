# slice-sets-aside-what-needs-an-outside-account

## Under test

When `/craft:slice` judges the candidates of a spec and one of them needs a real outside account, business setup or real-world event that no stand-in (fake, test double, sandbox) can replace for building and testing, the pass sets that criterion aside instead of choosing it: step 7 judges each candidate against the set-aside rule before choosing; step 8 states the set-aside with the criterion's own words and what it waits on before anything is written; step 9 writes it with `set_aside.py --spec <n> --entries <path> --vault <v>` (no `--parent`) after the gates and before the create; the pass chooses another candidate; and the closing report names the criterion, what it waits on, and the manual undo (a direct `lore record update` of the spec adding a dated `released` line to the entry) until a check-list walk-through can release it. The prose under test is `skills/slice/SKILL.md` steps 7 to 9 and the Outcome section, as it stands when the baseline runs and after the edit. Spec criteria: AC15 (an unbuildable candidate is set aside by the slice pass with what it waits on), AC18 (the report names it and what it waits on), AC19 (the pass goes on to choose from the remaining candidates).

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `bill-members-monthly` with no slices yet and three criteria. AC1 asks for sales tax to be collected under the company's live registration with the payment provider's tax service; the spec's notes say the company has not applied, the provider issues it after a weeks-long business-verification review, and it offers no test version. AC2 (a PDF copy of a past invoice) and AC3 (line items with prices) are plainly buildable. AC1 comes first so that taking the spec's order would pick it. The prompt runs `/craft:slice spec/bill-members-monthly`, names the vault, redirects `lore` to `./lore` and the plugin's scripts to `./scripts/`; it does not say "set aside", name the writer, or hint that a criterion might not be buildable.

Stand-ins: `./lore` is a self-contained wrapper that logs each call's arguments to `lore-calls.log`. `scripts/ledger_gate.py`, `candidate_set.py`, `covers_gate.py`, `found_work.py` and `pending_check.py` log their arguments to the same file and run real copies of craft's scripts (`real-scripts/`) against a `bin/lore` that reaches the same vault. `scripts/set_aside.py` logs its arguments, copies the `--entries` file's contents into `set-aside-entries.log` at call time, then `exec`s the real `set_aside.py`: it is not a hand-written stand-in, so the arguments the prose passes are the ones the real writer parses, and its effect on the spec is a real vault write. A prose that never runs the writer leaves its logs empty.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, grants from craft's `eval-gate.json` runner block plus `Bash(*/scripts/set_aside.py *)`.
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 540`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs at 1.0 after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. Because of the tool-call time limit each batch is three single-run invocations.
- The baseline runs on the unedited prose before any edit and is a detector: the prose has no set-aside rule, so the pass is expected to choose AC1 or to choose another criterion without writing the set-aside; either way it never runs `set_aside.py`, so the mechanical clauses go red.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The writer ran with `--spec` | `writer-ran` | `regex` on `lore-calls.log` |
| It ran without `--parent` and with `--entries` | `writer-without-parent` | `regex` on `lore-calls.log` (negative lookahead on `--parent`) |
| Its entries name AC1 | `entries-name-ac1` | `regex` on `set-aside-entries.log` |
| Its entries name neither AC2 nor AC3 | `entries-omit-ac2-and-ac3` | `regex` on `set-aside-entries.log`, not_contains |
| The spec carries a `set aside` entry for AC1 under `## Waiting` | `spec-carries-the-set-aside-entry` | `regex` on the spec's body |
| That entry's `waits on:` line names the registration | `entry-says-what-it-waits-on` | `regex` on the spec's body, case-insensitive |
| The writer ran before the slice's create | `writer-before-the-create` | `regex` on `lore-calls.log`, dot-all |
| The reply names AC1 as set aside with its words and what it waits on | `reply-names-the-set-aside` | `llm`, `focus: trace` |
| The reply states a chosen slice for AC2 and/or AC3, not AC1 | `reply-states-another-slice` | `llm`, `focus: trace` |
| The reply names the manual undo | `reply-names-the-manual-undo` | `llm`, `focus: trace` |

### Each pass line against each branch, checked before any arm ran

| Branch | writer-ran | without-parent | entries-name-ac1 | entries-omit | spec entry | waits-on | before-create | set-aside reply | another slice | undo | Whole run |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Today's prose, picks AC1 (or several, AC1 among them) | **fail** | **fail** | **fail** | pass | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | fails |
| Today's prose, picks AC2 or AC3 without a write | **fail** | **fail** | **fail** | pass | **fail** | **fail** | **fail** | **fail** (it may mention AC1 is not buildable, but not as a set-aside) | pass | **fail** | fails |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Sets AC1 aside but reports no undo | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | fails |
| Passes `--parent` (treats it as a build session) | pass | **fail** | pass | pass | pass if the writer accepts it (it refuses: no parent exists) | pass or **fail** | pass | depends | pass | depends | fails |
| Sets AC1 aside after the create | pass | pass | pass | pass | pass | pass | **fail** | pass | pass | pass | fails |
| Sets AC2 or AC3 aside as well | pass | pass | pass | **fail** | pass | pass | pass | pass | pass if it still chooses one | pass | fails |

Only the edited prose done right satisfies every clause. The mechanical clauses read the call log, the entries copy and the vault, not the reply.

## Limitations

- One spec, one phrasing of the dependency, one model tier, three runs, one Linux host.
- The case shows the pass sets aside what is plainly outside; it cannot show the judgement on a borderline criterion. The sibling `slice-builds-against-a-stand-in-instead-of-setting-aside` is the other side of the rule.
- The all-candidates-set-aside branch of the prose (the pass says nothing was buildable and does not reuse the built-and-waiting wording) is not exercised by either case; one fixture seeds a buildable AC2 and AC3.
- The `llm` clauses judge the first and last 12 events of a run; the clauses that decide what was written are mechanical. The `mktemp` entries file's removal is not pinned: the runner grants no `rm`.

## Amendments after the first pass batch

Made after the baseline and the first post-change batch, before any rerun, and reported with the results:

- The prompt now tells the agent the operator has already approved whichever slice the pass chooses, so it writes the slice without waiting for a reply. In one of three baseline runs and one of three first post-change runs of the stand-in case, the pass stated the claim and stopped to wait for an operator who is absent in an eval. That waiting comes from the slice procedure's existing step 8 ("stated while it is still cheap to reject"), not from the set-aside rule, and it failed the slice-created clause for a reason unrelated to what the cases measure.
- The create-matching regex (`writer-before-the-create` here and `a-slice-was-created` in the sibling) now allows other options between `record create` and `--kind task` on the same call line. One first-batch run wrote the set-aside before the create with `--vault eval` placed first, and the old pattern missed it.
- Both cases' baselines are rerun under the amended prompt and graders before the pass bar is judged.
