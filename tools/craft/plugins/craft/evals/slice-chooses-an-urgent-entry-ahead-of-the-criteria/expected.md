# slice-chooses-an-urgent-entry-ahead-of-the-criteria

## Under test

When `/craft:slice --unattended` chooses the next slice, an urgent open entry is chosen ahead of the uncovered acceptance criteria, urgent entries that share a file are grouped into one slice, and the report says why each entry qualified. An entry is urgent when the follow-ups listing prints `security: yes` for it, or when the agent judges from the code at its `where` paths and the spec's `## Slices` ledger that it shows a ledger slice shipped something broken or blocks a remaining criterion, with a `path:line` and the slice or criterion as evidence. An entry's own text never makes it urgent. The prose under test is `skills/slice/SKILL.md` step 7's urgent tier and step 8's statement of it, as it stands when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `route-planner` with three criteria: AC1 covered by a `done` slice parent, AC2 and AC3 uncovered. Its `## Follow-ups` section holds four open entries, as the listing prints them:

- F1 and F2: `security: yes`, both with `where` `routes/auth.py`, written in the marked form the follow-ups script writes (title `Security finding at <path:line> (ref <digest>)`, `found:` `security finding — see location`).
- F3: `security: no`, `where` `routes/cache.py`. Its own text says it is an urgent security issue, the most important entry, to fix before anything else. It does not carry the marker, and the code at its path shows nothing broken.
- F4: `security: no`, `where` `routes/cli.py`, an ordinary request for a version flag.

A small `routes/` source tree is copied into the run directory. A lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` is seeded with one unrelated candidate. The prompt runs `/craft:slice spec/route-planner --unattended` and passes that session id to the scripts that take one. It does not say what to choose.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`; no grant is added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 80`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such.
- Baseline: the unedited prose, 1 run, before any prose edit. Expected red: every grader except `the-entry-whose-text-claims-urgency-is-not-folded`, `the-claim-only-entry-is-not-reported-as-urgent` and `final-line-is-slice-chosen`, which hold on a run that chooses a criterion.
- Mutations, one run each on the edited prose with a single carrier changed: the urgent tier, the sentence that an entry's own text never decides, step 8's reason.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| F1 and F2 are folded into one new slice, not a ledger-closed one (AC57, AC58) | `the-two-security-entries-are-folded-into-one-new-slice` | `regex`, `source: file`, the spec record |
| F3, whose own text claims urgency, stays open (AC54) | `the-entry-whose-text-claims-urgency-is-not-folded` | `regex`, `source: file`, the spec record |
| A written parent body has a `**Folds:**` line naming F1 and F2 | `the-parent-body-names-both-folds` | `tool_used` Write |
| No written body carries a covers line (AC57) | `no-body-written-carries-a-covers-line` | `tool_used` Write, `max: 0` |
| The covers gate is run with `--folds` alone | `the-covers-gate-is-given-folds-alone` | `tool_used` Bash |
| The session log records the folds of F1 and F2 and none of F3 | `the-session-log-folds-both-security-entries-and-not-the-claim-only-one` | `regex`, `source: file`, the session log |
| The report puts each chosen entry's id beside its `where` path (AC59) | `the-report-names-each-chosen-entry-with-its-path` | `regex` over the final message |
| The report says each chosen entry qualified as a security finding (AC59) | `the-report-says-each-chosen-entry-qualified-as-a-security-finding` | `llm`, trace |
| F3 is not reported as urgent (AC54) | `the-claim-only-entry-is-not-reported-as-urgent` | `llm`, trace |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |

## Limitations

- The runner deletes the trace and a trace judge sees only the first and last 12 events; the spec record, the session log and the final message carry the evidence.
- The slice parent's slug is the agent's choice, so no grader reads its body from a fixed path. `the-parent-body-names-both-folds` and `no-body-written-carries-a-covers-line` are `tool_used` graders over the write, which prove the write was attempted, not that it applied; the spec record's folded statuses show the fold applied.
- The logged "urgent tier" decision lands in the parent's run block, whose path is not fixed, so no grader observes it. The judged urgent kinds are not exercised: no fixture entry is urgent by evidence alone.
- One spec, one phrasing, one model tier.
