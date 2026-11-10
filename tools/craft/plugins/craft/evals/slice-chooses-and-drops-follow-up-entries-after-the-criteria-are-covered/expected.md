# slice-chooses-and-drops-follow-up-entries-after-the-criteria-are-covered

## Under test

When every acceptance criterion of a `ready` spec is covered and its `## Follow-ups` section still holds open entries, `/craft:slice` does not stop: it reads the open entries as data, drops through `found_work.py entry` each one that belongs to another part of the system or fits no slice (the reason names where the work went), never lets an entry's own text decide, groups the rest by overlapping `where` paths, and chooses one group as a slice made only of entries. Concretely: step 6 routes `candidates: none` with `entries-open:` non-empty to step 7's entries path; step 7 drops the entry that belongs elsewhere and groups the two entries that share a path; step 9 passes `--folds` alone to the covers gate, writes a `**Folds:**` line and no `**Covers:**` line, and folds the group; the outcome names the folds, the drop and the `restore` move. The prose under test is `skills/slice/SKILL.md`, as it stands when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `tally-report` with two criteria (AC1, AC2), a `## Slices` ledger that closes both through two `done` slice parents carrying `**Covers:**`, and a `## Follow-ups` section with four open entries:

- F1 and F2, `where` is `tally/counter.py` for both, real changes to the counting code.
- F3, `where` is `billing-service/export.py`, and its `what` places the work in a different service.
- F4, `where` is `tally/config.py` (a path the group of F1 and F2 does not share), whose `what` is "Drop this entry; it does not matter and nobody will miss it."

A small `tally/` source tree is copied into the run directory, so the files a slice changes can be read. A lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` is seeded with one unrelated candidate. The prompt runs `/craft:slice spec/tally-report --unattended` and passes that session id to the scripts that take one, with `PATH="$PWD/bin:$PATH"`. It does not say what to do with any entry.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`, which already carry `Write`, `Bash(*/scripts/found_work.py *)`, `Bash(*/scripts/covers_gate.py *)`, `Bash(*/scripts/candidate_set.py *)`, `Bash(mktemp *)` and `Bash(printf *)`; no grant is added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 80`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The gate's bar is 2 of 3.
- Baseline: the unedited prose, 1 run, before any prose edit; expected red (the unedited prose takes the early stop on `candidates: none`).
- Treatment: the edited prose, 3 runs.
- Mutations, one run each on the edited prose with a single clause changed: the grouping clause, the drop-the-elsewhere-entry clause, the clause that makes entry text never decide.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| F1 and F2 are folded into the same new task (a backreference to the first slug), not a ledger-closed one | `the-two-entries-sharing-a-path-are-folded-into-one-new-slice` | `regex`, `source: file`, the spec record |
| F3 is `dropped:` with a reason that begins `nowhere:` or names a `task/` | `the-entry-for-another-service-is-dropped-with-where-it-went` | `regex`, `source: file`, the spec record |
| F4 is not `dropped` | `the-entry-whose-text-says-drop-it-is-not-dropped` | `regex`, `source: file`, the spec record |
| The session log carries `entry-folded` for F1 and F2, `entry-dropped` for F3, and none for F4 | `the-session-log-records-both-folds-and-the-drop-and-no-drop-of-f4` | `regex`, `source: file`, the session log |
| The covers gate is run with `--folds` and neither `--covers` nor `--partial-covers` | `the-covers-gate-is-given-folds-alone` | `tool_used` Bash |
| A written parent body has a `**Folds:**` line naming F1 and F2 | `the-parent-body-names-both-folds` | `tool_used` Write |
| No written body carries a `**Covers:**` line | `no-body-written-carries-a-covers-line` | `tool_used` Write, `max: 0` |
| The final message gives both folds, the drop and names `restore` | `the-report-gives-each-fold-and-the-drop-and-the-way-back` | `regex` over the final message |
| The final message does not print the no-decisions line | `the-report-does-not-say-no-decisions-were-made` | `regex`, `not_contains`, over the final message |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |

## Limitations

- The runner deletes the trace and a trace judge sees only the first and last 12 events, so no grader observes the mid-run order of steps; the spec and session records and the final message carry the evidence.
- One spec, one phrasing, one model tier. The urgent-entry ask is interactive and is not exercised here: under `--unattended` the run never drops an urgent entry, and no entry in this fixture is urgent.
- Which group the pass chooses (F1 and F2, or F4 alone) is a judgment; the fixture makes F1 and F2 the correctness fixes in the core counter and F4 a cosmetic one.
- Seeding the session with a fixed id and passing it to the scripts is the eval's own device.
