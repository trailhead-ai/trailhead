# ritual-output-wording — does craft's always-loaded wording section make a session mention an acceptance criterion by its own text, and leave a reviewer's report alone?

This file is the pass condition. It is committed before any arm is run, and the grammar, thresholds and verdict
words below may not be changed after a treated or shipped-state output has been seen. A change that is needed
anyway is made as a dated amendment appended at the end, stating what was seen first.

## What is under test

The prose under test is two pieces of craft's shipped text:

- The `## Output wording` section of `tools/craft/plugins/craft/rules.md`, the always-loaded rules file. It does
  not exist at the baseline revision. A later task adds it; this task only freezes the baseline.
- Slice choice's step 8 in `tools/craft/plugins/craft/skills/slice/SKILL.md`, which today tells the ritual to name
  covered criteria to the reader by identifier. A later task edits it.

The claim: with the wording section loaded, output that mentions an acceptance criterion gives the criterion's own
text with its number after it in round brackets; and a council reviewer's report to its caller keeps the council
template's shape.

## The three sites

| Site | What the session is doing | What is graded |
|---|---|---|
| no-ritual | No ritual is loaded. The session is given a small spec and a status note and asked which acceptance criteria remain. | `criterion-mention` over the whole reply |
| slice | The slice skill is loaded, stubbed so the run starts at step 8: the session states the chosen slice and then writes, on a final separate line, the covered-criteria value it would draft for the gate. | `criterion-mention` over the statement (the reply without its final line), and `covers-field` over the final line |
| reviewer | One council member's agent definition (the reliability lens) is loaded, and the council prompt template is filled for that lens against a small plan and the same spec. | `reviewer-report` over the whole reply, and `criterion-mention` as the comparison below |

Every arm at a site gets the same fixture and the same prompt. Only the system prompt differs.

## Arms

Each arm is a file under `arms/` named `<arm>.md`, a committed copy of its sources as they stood when it was
built. The table below names each arm's sources and, per source, the commit it was copied from or the working tree.
The manifest in `tools/craft/tests/test_ritual_output_wording_arms_contract.py` lists every arm file; no test
recomposes an arm, because the pinned text exists only in repository history. Arms carry no provenance notes;
provenance is this file's.

Every variable that differs between arms:

| Arm | Site | Slice skill | Reliability agent definition | Rules file | Wording section | Added by |
|---|---|---|---|---|---|---|
| `baseline-noritual` | no-ritual | none | none | `9458af4e` | absent | this task |
| `baseline-slice` | slice | `9458af4e` | none | `9458af4e` | absent | this task |
| `baseline-reviewer` | reviewer | none | `9458af4e` | `9458af4e` | absent | this task |
| `treated-noritual` | no-ritual | none | none | working tree | present | later task |
| `treated-slice-rules-only` | slice | `9458af4e` | none | working tree | present | later task |
| `shipped-slice` | slice | working tree | none | working tree | present | later task |
| `treated-reviewer` | reviewer | none | working tree | working tree | present | later task |

The baseline arms are frozen at `9458af4e`, a commit that already exists on the main branch and is the last one
before any wording edit. Within an arm pair the arms differ in one variable: at the no-ritual and reviewer sites,
the rules file (the wording section); at the slice site, `treated-slice-rules-only` differs from `baseline-slice`
by the wording section alone, and `shipped-slice` differs from `treated-slice-rules-only` by the slice skill's
step 8 alone. Arms for a variable nobody has changed yet are not created.

The slice site's drafted value is also graded in every slice arm, because a script reads that line.

## Fixtures

All fixtures under `fixtures/` are written by hand. None pulls text from a vault record, and none contains a
credential-shaped value. They are set in a small lending library, a domain unlike any example the wording section
will carry.

- `spec.md` — a spec with seven numbered acceptance criteria written as `**AC<n>.** <one line>`. This is the file
  the grader takes as its `--criteria` argument.
- `status-note.md` — a status note saying which criteria are done.
- `prompt-noritual.md` — asks which criteria remain, one line each (no-ritual site).
- `slice-run.md` and `prompt-slice.md` — the stubbed slice run, asking for the statement of the chosen slice and
  the covered-criteria value on a final separate line (slice site).
- `plan.md` and `prompt-reviewer.md` — a small plan, and the council prompt template filled for the reliability
  lens, with that lens's plan-level Critical bars and the production maturity block, as the template prescribes
  (reviewer site).

In each prompt, `FIXTURES_DIR` stands for the directory holding a copy of `fixtures/`; the runner replaces it
before the run.

## Dispatch

Each run is its own `claude -p` process. The runner copies `fixtures/` into a throwaway directory under `/tmp`,
replaces `FIXTURES_DIR` in the prompt with that path, then starts the process from a second, empty throwaway
directory:

```
claude -p "<prompt>" --setting-sources project --append-system-prompt "<contents of arms/<arm>.md>" \
  --allowedTools "Read" < /dev/null > runs/<arm>-<n>.txt 2> runs/<arm>-<n>.stderr.txt
```

The exit status goes to `runs/<arm>-<n>.exit`. No `--model` flag is passed, so the process uses its default model.
Runs are sequential, in the foreground.

### Capture naming

One prefix per site and arm: the arm's name, then `-<n>` with `n` counting dispatches from 1, never reused. A
run's three files are `<arm>-<n>.txt`, `<arm>-<n>.stderr.txt` and `<arm>-<n>.exit`. Replacement and re-run
dispatches continue the count.

| Prefix | Checks the grader runs | Expected under this pre-registration |
|---|---|---|
| `baseline-noritual-<n>` | `criterion-mention` | majority `bare` (headroom precondition) |
| `treated-noritual-<n>` | `criterion-mention` | clean majority `text-with-number` |
| `baseline-slice-<n>` | `criterion-mention` over the statement; `covers-field` | majority `bare`; `gate-shape` |
| `treated-slice-rules-only-<n>` | same | observed only; `covers-field` guard applies |
| `shipped-slice-<n>` | same | clean majority `text-with-number`; `covers-field` guard `gate-shape` |
| `baseline-reviewer-<n>` | `reviewer-report`; `criterion-mention` | observed; sets the comparison |
| `treated-reviewer-<n>` | same | every run `template-shape`; same majority `criterion-mention` verdict as baseline |

## Verdict words

Each check prints one line per capture, with the check name and one of these words.

- `criterion-mention`: `text-with-number`, `nickname`, `bare`, `no-mention`.
- `reviewer-report`: `template-shape`, `off-shape`.
- `covers-field`: `gate-shape`, `off-shape`, `absent`.

## Grammar

Fixed here, before any run.

### Reading the capture

A **logical line** is built from the raw capture before any other step. A heading line (starting `#`), a list item
(a line whose first non-space characters are `-`, `*` or `+` followed by a space, or digits followed by `.` or `)`
and a space), a table row (first non-space character `|`) and a code-fence line each start their own logical line.
Any other non-blank physical line joins the logical line before it, with a single space, unless a blank line
intervened, in which case it starts its own. So a sentence wrapped over several physical lines is one logical line,
and a bracket and the words before it are on the same logical line wherever the wrap falls. A table row is read cell
by cell: cells are split on `|`, a separator row (cells of only `-`, `:` and spaces) is skipped, and a cell is a
logical line of its own. Then the characters `*`, `_` and the backtick are deleted from every logical line.

### A criterion number

A criterion number is the letters `AC` followed by one or more digits, where the `A` is not preceded by a letter or
digit and the last digit is not followed by a digit. Case matters. Nothing else is a criterion number: `criterion 3`,
`#3`, `item 3`, `[AC3]` as a number (the brackets matter below, not this), `ac3` and `AC` without digits are not
criterion numbers, and a capture that only refers to criteria that way has `no-mention`. Other numbers (ten pounds,
three days, a count of findings) are not criterion numbers.

### A group

A **group** is a run of criterion numbers joined by connectors, read as one mention. List connectors between two
`AC<digits>` tokens: `,`, `, and`, `and`, `or`, `&`, `/`, `+`. Range connectors: `to`, `through`, `-`, `–`, `—`,
followed by either an `AC<digits>` token or bare digits (`AC4 to AC7`, `AC4 to 7`, `AC4-7`). A range stands for
every number from its first to its last, when the first is not greater than the last; otherwise for just its two
ends. A group's numbers are all the numbers it names, written or implied.

### Bracketed or bare

A group is **bracketed** when the nearest non-space character before it is `(` and the nearest non-space character
after it is `)`, so the group sits wholly inside one pair of round brackets with nothing else between the brackets.
Any other form is **bare**: a number leading a line, a number in a table column of its own, `(see AC4)`, `(AC4:
gloss)`, `[AC4]`, `AC4 — gloss`, or a list of numbers with no brackets. This is what "still makes sense with the
number deleted" is decided by: only a group wholly inside trailing brackets can be deleted and leave a sentence.

### Words before the brackets

For a bracketed group, take the text of its logical line before the opening bracket. Strip trailing spaces and
any of `. , ; : ! ? " ' - – —` from its end, then cut it after the last sentence terminator (`.`, `!`, `?` or `;`
followed by a space) it still contains. Keep at most the last 60 words of what is left, a word being a run of the
letters a to z after lower-casing. These are the **words before**. The window never crosses a logical line or a
table cell.

If the group is bracketed and there are no words before, it is `bare`: a number alone in brackets stands in for
the criterion's text, it does not follow it.

### A criterion's own words

Read from `--criteria`: for criterion `n`, the text after `**AC<n>.**` on its line and any following non-blank
lines up to the next blank line. Its own words are the distinct words of four or more letters in that text,
lower-cased. Both these words and the words before are plural-folded for comparison: a word longer than three
letters ending in `s` loses that `s`. A number that has no entry in `--criteria` has no own words and cannot pass.

The **share** of criterion `n` is the number of its own words that appear among the words before, divided by the
number of its own words.

### Classifying a group

Groups are read in order, line by line, left to right.

- Not bracketed: `bare`.
- Bracketed, no words before: `bare`.
- Bracketed, and every number in the group already appeared in an earlier group in this capture, in any form: a
  pointer back to a criterion already stated. It is `text-with-number` when there is at least one word before, and
  `bare` when there is none (already covered by the previous rule).
- Bracketed otherwise: `text-with-number` when the share is at least 0.5 for every number in the group, and
  `nickname` when any number in it falls short. A bracketed list or range is therefore text-with-number only if
  the words before carry half the words of each criterion it names.

### A capture's verdict

- No group at all: `no-mention`.
- Any group `bare`: `bare`.
- Otherwise any group `nickname`: `nickname`.
- Otherwise: `text-with-number`.

One bare group makes the capture `bare`, however many text-with-number groups it also has. This is stricter than a
majority of mentions on purpose: the complaint this work answers is a bare number reaching the reader at all.

At the slice site, `criterion-mention` is read over the capture with its last non-blank line removed, when that line
contains a criterion number (it is then the drafted value, read by `covers-field`). When the last line contains no
criterion number it stays in the text read.

### Worked cases

Using the committed `spec.md`, where AC4 reads "A librarian override records who granted it and the reason given,
and shows in the borrower's history." and has nine own words (librarian, override, record, granted, reason, given,
show, borrower, history):

| Text in a capture | Verdict of the group |
|---|---|
| `A librarian override records who granted it and the reason given, and shows in the borrower's history (AC4)` | `text-with-number` (share 1.0) |
| `Override recording is not started (AC4)` | `nickname` (share 1/9) |
| `**AC4** — the override records who granted it` | `bare` (not bracketed) |
| `\| AC4 \| Override recording \| not started \|` | `bare` (not bracketed; the cell has no brackets) |
| `Still to do: AC4, AC5 and AC6` | `bare` (one group, not bracketed) |
| `(AC4) Override recording is not started` at the start of a line | `bare` (no words before) |
| `Two remain: (AC4, AC5)` | `nickname` (shares far below 0.5) |
| `(see AC4)` | `bare` (not wholly inside the brackets) |
| `The override item (AC4) is still open`, after the first row above has appeared | `text-with-number` (pointer back, words before present) |

### The reviewer report

`template-shape` needs all of: a line that, after trimming, is exactly `## Findings`; a later line that, after
trimming, is exactly `## Confidence`; at least one line between them that starts (after optional spaces, a `-` or
`*` list marker and optional `**`) with `[Critical]`, `[Important]` or `[Minor]`; and a non-blank line after
`## Confidence`. Text before `## Findings` is allowed. Anything else is `off-shape`. The template's word cap and its
limit of two Critical findings are not graded here; a report that exceeds them but keeps the headings and tagged
lines is `template-shape`, and the overrun is recorded by eye in the results.

### The covered-criteria value

`covers-field` reads the last non-blank line of the capture, trimmed of surrounding whitespace only. It is
`gate-shape` when the whole line matches `\AAC\d+(,\ ?AC\d+)*\Z`, the grammar the covers gate certifies. It is
`off-shape` when the line contains a criterion number but does not match, for instance `AC3 and AC4`, a
backtick-wrapped list, or a line that begins with a field label. It is `absent` when the capture is empty or its
last non-blank line contains no criterion number.

## Runs, sample size and splits

Three runs per arm. A criterion's verdict from three graded runs that all agree is decided. A 2-1 split is re-run:
three more runs of the whole arm (six in total, a fresh batch and not just the outlier), all six combined. A
**clean majority** is 3 of 3 at three runs, and 5 of 6 or 6 of 6 at six. 4 of 6 is not clean: the result is
`AMBIGUOUS`, which is not a pass and is reported as unresolved.

**Majority** without the word clean (used for baselines) means more than half of the graded runs.

### Floor and exclusions

A **crashed run** is one whose `.exit` is non-zero, whose output is empty, or which did not finish within the
600-second tool limit. A run whose output says a tool it needed was refused is also excluded, judged by eye. A
crashed run is excluded and never graded. It stays committed under `runs/`, with its `.exit`, and is logged by
ordinal and reason in the commit that carries it and in the results record. It is replaced by a fresh dispatch with
the next ordinal. At most three replacements per arm; past that, stop and report, not keep retrying. The floor is
three graded runs per arm after exclusions. An arm that cannot reach the floor is reported unmeasured.

A run is never discarded for any other reason: not because its output looks wrong, short, or unlike the others.

### No-mention runs

At the no-ritual and slice sites, a `no-mention` run counts as neither a pass nor a fail for `criterion-mention`. It
is logged, kept, and left out of that criterion's tally; the tally needs at least three mention-bearing runs, so
more are dispatched (up to nine dispatches per arm) until there are. If the arm still has fewer than three
mention-bearing runs, stop and report to the operator running the slice, with two options: amend this file to change
the fixture before any treated run, or record the site unmeasured. At the reviewer site, `no-mention` is a verdict
like the others, used in the comparison below.

## Pass conditions

### `criterion-mention` (the criterion that output names a criterion by its text with the number after it)

- **Headroom, at each of the no-ritual and slice sites:** the baseline arm is majority `bare`. If it is not, the site
  has no headroom: stop, change nothing, and report.
- **No-ritual site:** the `treated-noritual` arm reaches a clean majority of `text-with-number`.
- **Slice site:** the `shipped-slice` arm reaches a clean majority of `text-with-number`. `treated-slice-rules-only`
  is observed and reported, and decides nothing on its own.

### `reviewer-report` (a reviewer's report keeps the form it has today)

- Every graded `treated-reviewer` run is `template-shape`. One `off-shape` run fails it.
- The `treated-reviewer` arm's majority `criterion-mention` verdict is the same as the `baseline-reviewer` arm's
  majority verdict. When the baseline's majority verdict is `no-mention`, the treated arm must also be majority
  `no-mention`; a treated report that starts mentioning criteria where the baseline did not has changed form. When
  either arm has no verdict held by more than half of its graded runs, the comparison is `AMBIGUOUS`, not a pass.

### The guard on script-read fields

Every graded `treated-slice-rules-only` and `shipped-slice` run's `covers-field` is `gate-shape`. One `off-shape`
or `absent` run fails the guard, whatever the criterion-mention result.

## Not measured

- The agents a ritual dispatches other than the council reviewer: the executor, the drift gate and the code
  reviewer are not run here. Nothing in this case says the wording section leaves their reports alone.
- The reviewer-report result is stated for a council reviewer, and measured with the reliability lens only. The
  other three lenses are not run.
- Every criterion this case cannot decide by script is outside it.

## Limits

- The wording section is appended to the system prompt with `--append-system-prompt`. The harness's own loading of
  a rules file is not exercised.
- One model tier, the process default (the probe that preceded this file saw `claude-opus-5-5`), at one Claude Code
  version. No other tier is run.
- The ritual steps before step 8 of the slice run are stubbed: the run starts at the statement of the chosen slice.
- The reviewer prompt is one lens's filled template, the reply to which is not seen by a synthesizer.
- Words-before share is a lexical proxy for "the criterion's own text". It cannot tell a faithful paraphrase that
  shares few words from a nickname that shares many. The 0.5 threshold was set before any run and is the threshold
  that counts.
- `criterion 3` and other forms without the letters `AC` are not read as criterion numbers.
- Three runs per arm resolve only a large effect.
- Runs are read-only (`--allowedTools "Read"`) and start from empty directories; behaviour with tools is not measured.

## Amendment, 2026-10-13 — the covered-criteria value may carry its own field label

Committed after the nine baseline captures and before any treated or shipped-state arm file exists or has been run.
Nothing above this heading is rewritten; where this section and "The covered-criteria value" disagree, this section
is the rule the grader implements.

**What the baseline showed.** One of the three baseline slice runs, `baseline-slice-1`, wrote its final line as the
field label followed by the list. The other two wrote the list alone. The slice fixture asks for the value "exactly
as it would be written in the record", and the record's line carries that label, so both answers follow the
fixture. The prose under test was the same in all three runs.

**What changes.** Before matching, `covers-field` removes one leading `**Covers:**` label, and any spaces after it,
from the last non-blank line. What is left must match the same grammar, whole. That one label is the only thing
removed: a different label, a label anywhere but the start of the line, a backtick-wrapped list, and a value that
carries criterion text or brackets are all still `off-shape`. A line that is the label and nothing else is
`off-shape` too, since it contains no value.

**A repeated number.** A value that names the same criterion number twice, such as `AC3, AC3`, is `off-shape`. The
pattern quoted in "The covered-criteria value" would match it, but the covers gate refuses it, and the check asks the
gate. No baseline run produced one; this is stated so the document and the gate agree before any treated run.

**Why.** The guard exists to catch the wording section rewording a value that a script reads as bare numbers. The
label is not part of that value: the covers gate is handed the list alone. Under the rule as first written, the
unchanged baseline fails the guard in one run of three, so a treated run failing it would say nothing about the
wording section.

**Effect on the baseline.** `baseline-slice-1` is `gate-shape` under this rule and `off-shape` under the rule as
first written. The results record both readings.

**What does not change.** The fixtures, the baseline captures, the `criterion-mention` grammar, the rule that the
slice site's last line is left out of the statement when it contains a criterion number, every pass condition and
every run rule.

**Limit.** This amendment was written with baseline output in hand. No treated output existed when it was
committed.

## Extension, 2026-10-14 — the second wording, the last this case will measure

Committed after the nine first-wording captures (commit `200b5269`) and before any second-wording arm has been run.
Nothing above this heading is rewritten; the grammar, thresholds, verdict words, run rules, floor and exclusion
rules, split rule and pass conditions above apply unchanged to the arms named here.

**The first wording's result, as counted by the grader.** Three runs per arm.

| Arm | Check | Grader's count |
|---|---|---|
| `treated-noritual` | `criterion-mention` | `text-with-number` 1, `nickname` 2 |
| `treated-slice-rules-only` | `criterion-mention` over the statement | `text-with-number` 3 |
| `treated-slice-rules-only` | `covers-field` | `gate-shape` 3 |
| `treated-reviewer` | `reviewer-report` | `template-shape` 3 |
| `treated-reviewer` | `criterion-mention` | `nickname` 3 (baseline: `bare` 3) |

The no-ritual arm is a 2-1 split under the grader and was not re-run. Reading by eye, `treated-noritual-1` and
`treated-noritual-2` each give a criterion's text with its number trailing, so by eye the arm is 3 of 3
`text-with-number`; the grader reads both as `nickname` because the words before the bracket fall under the
pre-registered 0.5 word share. The threshold set before any run is the one that counts, so the capture mapping
holds the grader's verdict for both, and the by-eye reading is logged here as a disagreement and not mapped. The
reviewer arm's majority `criterion-mention` verdict differs from the baseline's, so the reviewer comparison above
failed for the first wording.

**The second wording.** The text of the `## Output wording` section now at the end of
`tools/craft/plugins/craft/rules.md` is the second wording, recorded in the task record this work was built from.
It is the last wording this case will measure. It is measured exactly as it stands and is not adjusted after any
second-wording run. If a pass condition fails, the section is reverted on the branch by the operator and the run
stops.

**Second-wording arms.** Each is a file under `arms/`, listed in the same manifest, with the rules file read from
the working tree and the other source combined as the first-wording treated arms combine it. The names start with
`second-` so they cannot be confused with the first wording's `treated-` arms.

| Arm | Site | Slice skill | Reliability agent definition | Rules file | Wording section |
|---|---|---|---|---|---|
| `second-noritual` | no-ritual | none | none | working tree | second wording |
| `second-slice-rules-only` | slice | `9458af4e` | none | working tree | second wording |
| `second-reviewer` | reviewer | none | working tree | working tree | second wording |

Capture prefixes are the arm names, each followed by `-<n>` from 1. `second-noritual` is read with
`criterion-mention`; `second-slice-rules-only` with `criterion-mention` over the statement and `covers-field`;
`second-reviewer` with `reviewer-report` and `criterion-mention`. Each of the pass conditions above that names a
`treated-` arm is read, for the second wording, against the `second-` arm at the same site: a clean majority of
`text-with-number` for `second-noritual`; every graded `second-reviewer` run `template-shape` and the same majority
`criterion-mention` verdict as `baseline-reviewer`; every graded `second-slice-rules-only` run `gate-shape`.

**The first wording stays as recorded.** The `treated-` arms and their captures are a recorded result. They are not
re-graded under any rule added here, and their arm files stay reproducible by pinning the rules file to `200b5269`.

**Limit.** This extension was written with first-wording output in hand. No second-wording output existed when it
was committed.

## Result, 2026-10-14 — neither wording passes; the section is removed

Counted by the grader against the pass conditions above. Baseline, three runs per site: no-ritual `bare` 3 of 3;
slice statement `bare` 3 of 3 and covered-criteria value `gate-shape` 3 of 3; reviewer `template-shape` 3 of 3 and
criterion mention `bare` 3 of 3.

| Pass condition | First wording (`treated-`) | Second wording (`second-`) |
|---|---|---|
| No-ritual arm reaches a clean majority of `text-with-number` | 1 of 3, 2 `nickname`; not re-run (the reviewer condition had already failed) | 3 of 6, 3 `nickname`: AMBIGUOUS, fails |
| Every treated reviewer run is `template-shape` | 3 of 3, holds | 6 of 6, holds |
| Treated reviewer's majority `criterion-mention` matches the baseline's (`bare`) | `nickname` 3 of 3, fails | `nickname` 4 of 6, `bare` 2 of 6, fails |
| Every treated slice run's covered-criteria value is `gate-shape` | 3 of 3, holds | 3 of 3, holds |
| No treated arm stops mentioning criteria | holds | holds |

At the slice site, with the rule only, the statement was `text-with-number` 3 of 3 under both wordings.

Neither criterion this case measures closes. The section was removed from `tools/craft/plugins/craft/rules.md` on
the branch, as stated in the second wording's extension, and the shipped-state slice arm was not built or run. The
arm files stay reproducible from their pinned revisions: `200b5269` for the first wording, `5e0db468` for the
second.

What the captures show, read by eye: the no-ritual nicknames each come from one criterion shortened below the word
share (the late fee capped at the replacement price), with the other mentions giving the criterion's text in full.
The reviewer, told in its prompt that it was dispatched as one lens of a panel, still moved criterion numbers into
trailing brackets after a short label in most runs under both wordings. A limit stated inside the always-loaded
section did not hold a dispatched reviewer to its prior form.

One capture was edited after it was taken: in `second-reviewer-1.txt` one ordinary English word matched the
repository's machine-local leak denylist and was replaced with `[redacted]` so the commit could pass the leak gate.
The grader's verdicts on that capture are the same before and after the edit.

## Extension, 2026-10-14 — a third wording and the council report bullet, the last of each this case will measure

Committed before either edit and before any third-wording run. Nothing above this heading is rewritten; the
grammar, the 0.5 word-share threshold, the verdict words, the run, split, floor and exclusion rules, the no-mention
rule and the capture naming above apply unchanged to every arm named here.

**Authorization.** The operator decided on 2026-10-14, after the second wording's result above, to keep the rule in
craft's always-loaded text, to add a counter-instruction to the council prompt template, and to authorize one more
wording under a fresh dated extension. Moving the rule to an output style and relaxing the reviewer criterion were
both considered and declined.

**The third wording.** Appended to `tools/craft/plugins/craft/rules.md` as the `## Output wording` section, verbatim
(it is the second wording plus one sentence, "That holds for every first mention, a closing summary line
included."):

```
## Output wording

This section is for text a person will read in the session. If another agent
dispatched you, or your instructions fix the format of what you return, it does
not apply: write that output exactly as you would without this section. It also
leaves alone any field or line a script reads, plan files, and commit and pull
request text.

When you mention an acceptance criterion to the person reading, give the
criterion's own words, in full or close to it, and put its number after them in
round brackets, as in "Orders over the limit are held for review (AC6)". That
holds for every first mention, a closing summary line included. The sentence
must still make sense with the number deleted, and a short label is not a
substitute for the words. On a later mention, point back in words ("that same
hold for large orders"), with the number still trailing.
```

**The template bullet.** Inserted into `tools/craft/plugins/craft/skills/_shared/council.md`, in the template's text
block, immediately after the line `- One-line Confidence at the end`, verbatim:

```
- This report goes to the agent that dispatched you, not to a person: cite an acceptance criterion by its number alone, as the spec writes it (for example AC5); the output-wording section in your rules does not apply to it
```

Neither text is adjusted after any run. This is the last wording and the only template sentence this case measures.

**The third-wording revision.** The one commit that carries both edits. Every `third-*` arm pins its rules file and
its agent definition to it in the manifest, and the fixture-derivation check reads `council.md` at it, so that
reverting the edits on the branch later leaves the recorded arms rebuilding. The commit's SHA is recorded in the
manifest constant `THIRD_WORDING_REVISION` and in the Result section below. `baseline-advocate` pins to `9458af4e`.

**New fixtures.** `fixtures/prompt-reviewer-template.md` is `fixtures/prompt-reviewer.md` with the bullet inserted
after its `- One-line Confidence at the end` line and nothing else changed. `fixtures/prompt-advocate.md` is the
council template at `9458af4e` hand-filled for the Advocate lens against the same plan and spec, with the plan-level
Advocate Critical bars and the same maturity block as `prompt-reviewer.md`.
`fixtures/prompt-advocate-template.md` is `prompt-advocate.md` with the bullet inserted the same way.

**Arms and prompts.** Every variable that differs between arms:

| Capture prefix | Arm file (system prompt) | Slice skill | Agent definition | Rules file | Prompt | Checks |
|---|---|---|---|---|---|---|
| `third-noritual-<n>` | `third-noritual.md` | none | none | third-wording revision | `prompt-noritual.md` | `criterion-mention` |
| `third-slice-rules-only-<n>` | `third-slice-rules-only.md` | `9458af4e` | none | third-wording revision | `prompt-slice.md` | `criterion-mention` over the statement; `covers-field` |
| `third-reviewer-rules-only-<n>` | `third-reviewer.md` | none | reliability, third-wording revision | third-wording revision | `prompt-reviewer.md` | `reviewer-report`; `criterion-mention` |
| `third-reviewer-<n>` | `third-reviewer.md` (same file) | none | reliability, third-wording revision | third-wording revision | `prompt-reviewer-template.md` | `reviewer-report`; `criterion-mention` |
| `baseline-advocate-<n>` | `baseline-advocate.md` | none | advocate, `9458af4e` | `9458af4e` | `prompt-advocate.md` | `reviewer-report`; `criterion-mention` |
| `third-advocate-<n>` | `third-advocate.md` | none | advocate, third-wording revision | third-wording revision | `prompt-advocate-template.md` | `reviewer-report`; `criterion-mention` |
| `third-advocate-rules-only-<n>` (only if the bullet is dropped) | `third-advocate.md` (same file) | none | advocate, third-wording revision | third-wording revision | `prompt-advocate.md` | `reviewer-report`; `criterion-mention` |
| `shipped-slice-<n>` (run by a later task, not here) | `shipped-slice.md` | working tree | none | working tree | `prompt-slice.md` | `criterion-mention` over the statement; `covers-field` |

The two reliability reviewer prefixes share one system prompt and differ in the prompt alone (the bullet); the two
advocate prefixes likewise. `third-reviewer-rules-only` and `baseline-reviewer` differ in the rules file and agent
definition's revision only, as do the other pairs the tables above name. Three runs per prefix, run exactly as
`## Dispatch` above describes; splits re-run to six by the rule above.

**Pass conditions.**

- `third-noritual`: a clean majority of `text-with-number`.
- `third-slice-rules-only`: every graded run `gate-shape` for `covers-field`. Its `criterion-mention` verdict is
  observed only.
- `third-reviewer` (the shipped state): every graded run `template-shape`, and the majority `criterion-mention`
  verdict equals `baseline-reviewer`'s majority verdict (`bare`).
- `third-advocate`: every graded run `template-shape`, and the majority `criterion-mention` verdict equals
  `baseline-advocate`'s majority verdict. If no verdict holds more than half of either arm's graded runs, the
  result is `AMBIGUOUS`, which is not a pass.
- The bullet's own effect: if `third-reviewer-rules-only` is `bare` in a clean majority (3 of 3, or at least 5 of
  6), the bullet showed no effect of its own. It is reverted, and both reviewer conditions are read on rules-only
  arms instead (`third-reviewer-rules-only`, plus `third-advocate-rules-only` run on `prompt-advocate.md`). Any
  other count keeps the bullet.
- The criterion-mention criterion (AC1) needs the no-ritual condition. The reviewer-report criterion (AC16) needs
  both reviewer conditions, reliability and advocate. Either failing stops the run.

**The `shipped-slice` arm, registered here for the next task.** Slice skill and rules both from the working tree,
prompt `prompt-slice.md`, checks as in the table. Its conditions, fixed now so they are fixed before any edit: a
clean majority of `text-with-number` over the statement, and every graded run `gate-shape` for `covers-field`. The
arm is added and run by the task after this one; it is not created here.

**Stop rule.** This is the last wording and the last template bullet. If a pass condition fails, the arms are
confirmed pinned, both edits are reverted on the branch in a new commit, the extension, arms, prompts and captures
stay as a recorded result, a Result section is written, and the run stops and is reported. Nothing is reworded.

**What does not change.** The grammar, the 0.5 word-share threshold, the verdict words, and every run, split, floor
and exclusion rule above.

**Limit.** This extension was written with first- and second-wording output in hand. No third-wording output
existed when it was committed.

## Result, 2026-10-14 — the third wording and the council report bullet pass; both are kept

Counted by the grader against the pass conditions in the third-wording extension above. The third-wording revision is
`ecc4f46a`; every `third-*` arm pins its rules file and agent definition to it. Every run exited 0 and none was
excluded. `baseline-advocate` is pinned to `9458af4e`.

| Prefix | Check | Grader's count | Condition | Holds |
|---|---|---|---|---|
| `third-noritual` | `criterion-mention` | `text-with-number` 3 of 3 | clean majority `text-with-number` | yes |
| `third-slice-rules-only` | `covers-field` | `gate-shape` 3 of 3 | every graded run `gate-shape` | yes |
| `third-slice-rules-only` | `criterion-mention` over the statement | `text-with-number` 3 of 3 | observed only | n/a |
| `third-reviewer` | `reviewer-report` | `template-shape` 3 of 3 | every run `template-shape` | yes |
| `third-reviewer` | `criterion-mention` | `bare` 3 of 3 | majority equals baseline's (`bare`) | yes |
| `baseline-advocate` | `reviewer-report`; `criterion-mention` | `template-shape` 3 of 3; `bare` 3 of 3 | sets the comparison | n/a |
| `third-advocate` | `reviewer-report` | `template-shape` 3 of 3 | every run `template-shape` | yes |
| `third-advocate` | `criterion-mention` | `bare` 3 of 3 | majority equals `baseline-advocate`'s (`bare`) | yes |
| `third-reviewer-rules-only` | `reviewer-report` | `template-shape` 6 of 6 | none | n/a |
| `third-reviewer-rules-only` | `criterion-mention` | `bare` 2, `nickname` 4 (first three: 2 `bare`, 1 `nickname`, re-run to six) | bullet's own effect: `bare` in a clean majority drops the bullet | not clean, so the bullet shows an effect and is kept |

`third-advocate-rules-only` was not run: it exists only if the bullet is dropped.

The criterion-mention criterion (AC1) has its no-ritual condition met. The reviewer-report criterion (AC16) has both
reviewer conditions met, reliability and advocate. Both edits stay on the branch: the `## Output wording` section in
`tools/craft/plugins/craft/rules.md` and the bullet in `tools/craft/plugins/craft/skills/_shared/council.md`. The
`shipped-slice` arm and its registered conditions are left to the next task.

What the captures show, read by eye. The bullet carries the reviewer result: without it, under the third wording
alone, the reliability reviewer moved to nickname-plus-number in 4 of 6 runs; with it, 3 of 3 cite bare numbers as the
baseline does. Residuals: a clean majority at three runs still allows a minority of runs to shorten a criterion; the
no-ritual runs gave each criterion's own words in full including the closing line the second wording shortened; the
rule reaches the arms through `--append-system-prompt`, not the harness's rules loading; the executor, drift gate and
code reviewer are not measured. A manual re-run of this case is the signal to take if real sessions disagree.

Two no-ritual runs, `third-noritual-2` and `-3`, were issued in one tool message and may have overlapped in time;
each used its own throwaway directories. The other runs were dispatched one at a time.

## Result, 2026-10-14 — the shipped-state slice arm passes both its conditions; step 8's edit is kept

`shipped-slice` reads the slice skill and the rules file both at `ebf23e43`, the commit that carries slice step 8
with the instruction to name covered criteria by identifier removed (the manifest constant `STEP8_REVISION`). The
rules file at that commit is the third wording, unchanged since `ecc4f46a`. Three runs, dispatched one at a time;
every run exited 0 and none was excluded.

| Prefix | Check | Grader's count | Condition | Holds |
|---|---|---|---|---|
| `shipped-slice` | `criterion-mention` over the statement | `text-with-number` 3 of 3 | clean majority `text-with-number` | yes |
| `shipped-slice` | `covers-field` | `gate-shape` 3 of 3 | every graded run `gate-shape` | yes |

Read by eye, each statement lists the two covered criteria by their own text with the number trailing, and the final
line is the bare list (`AC3, AC4`; one run wrote it behind the `**Covers:**` label, which the amendment above
strips). The `partially covers` line said none in all three. The step 8 edit stays.

Two captures were edited after they were taken: in `third-reviewer-3.txt` and `third-reviewer-rules-only-2.txt`
one ordinary English word matched the repository's machine-local leak denylist and was replaced with `[redacted]`
so the change could pass the leak gate. The grader's verdicts on both captures are the same before and after the
edit (`template-shape` and `bare`; `template-shape` and `nickname`).

After the `shipped-slice` runs, slice step 8 was re-wrapped to the prose column the wrap gate enforces. The change
is whitespace only, so the shipped step differs from the text the `shipped-slice` arm measured (pinned at
`ebf23e43`) in line breaks alone.

## Note, 2026-10-14 — two departures from what the results above read as

The `shipped-slice` arm was registered above as reading the slice skill and the rules file "both from the working
tree". It was built pinned at `ebf23e43`, as the child task specified. The working tree matched that commit's content
when the runs happened, so the measurement is the same; the arm is pinned, not live.

The two `third-reviewer-rules-only` runs the grader reads as `bare` (1 and 3) are not reports in the baseline's form
when read. Run 1 puts the number first with the criterion's quoted text after it (for example `AC5, "returning an
overdue i…`). Run 3 mixes text-before-number mentions with a short label (`the reminder messages (AC6)`). The grader's
precedence returns `bare` for both, so "2 bare" understates how far the arm moved without the bullet. The keep
decision for the bullet is unchanged: the fuller reading only strengthens it.
