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

## Extension, 2026-10-20 — summaries lead with what needs the reader: sites S1, S2 and guards G1-G4, registered before any run

Committed before any run of a new arm. Nothing above this heading is rewritten; the grammar, verdict words, run
rules, floor and exclusion rules, no-mention rule and capture naming above apply unchanged to every arm named here
except where this section adds a check of its own.

**What is under test.** Two prose edits, each measured under this section, and the claim that follows from them: in
a session carrying craft's always-loaded rules, and in a build's closing report, a summary opens with what needs
the reader and gives everything that needs nothing from them at most one line each.

- **R**, the always-loaded paragraph, appended to `## Output wording` in `tools/craft/plugins/craft/rules.md`
  after the criterion paragraph, verbatim and not adjusted after this section is committed:

  > When you sum up or close out a piece of work for the person reading, open with what needs them: a decision only they can make, a question, or an action only they can take. Give everything that needs nothing from them at most one line each, and leave the detail until they ask. A pasteable command may still close the reply.

  It carries no worked example, because the section is read in every session. It sits under the section's existing
  limit (text a person reads in the session; never a report to a dispatching agent, a script-read field, plan
  files, or commit and pull request text), so that limit is not restated.

- **E**, the build's closing report, in the `**Completion report.**` paragraph of
  `tools/craft/plugins/craft/skills/_shared/execute.md`. The sentence, inserted directly after "the user decides
  when to open a PR.", verbatim:

  > The report opens with everything the run left for the user: an unresolved correctness or security finding, a failed or blocked gate, a default branch left unpushed, or a failed push. These come ahead of the pull request decision, which follows them. Every phase's outcome then follows, one line each.

  The duty to "enumerate every phase's outcome explicitly" is unchanged. The worked example is reordered, verbatim.
  The lead-in `Worked example:` becomes `Worked example (a clean run, with nothing else left for the user):`, and
  the example reads, with the command inside its own fenced block, alone on its line:

  > No PR was opened — that call is yours.

  > simplify: no changes; correctness: SHIP, 0 findings; security: skipped — no trigger; state-coverage: parent 4, doc 4, missing 0; criterion-observations: covered 3, observed 3, missing 0; push: 2 repos pushed, 1 already up to date; metrics: 4 tasks, 7 dispatches (1.75/task), 42m wall clock, 1 lesson written, 2 lessons consumed

  > Run this when you're ready to open it:

  ```
  /portage:pull_request
  ```

  The phase paragraph of the example is the existing one, unchanged; only the order and the closing lead-in line
  differ from today's text. E and the rebuild of the `ritual-deliverable-names-its-record` case's
  `execute-rule-only.md` live arm land in one commit, so that case's arm test is never red and one revert undoes
  both.

**Base.** B is `d860c95a7d470be857e01f04c55c986abadab916`, the main-branch commit the task branch was cut from. Arms
below are committed copies composed with `git show <rev>:<path>` at build time; no test reads history and no test
recomposes an arm. R and E are commits on the task branch; their SHAs are recorded in the arm table below when the
arms that carry them are built, and a table row naming a source "at R" or "at E" is not built until then.

**Sites.**

| Site | What the session is doing | Fixture and prompt | Read by |
|---|---|---|---|
| S1 | No ritual loaded. The session is given a status note (a choir's autumn tour, six routine items and one item only the reader can settle, placed fourth of seven) and asked where it stands. | `fixtures/summary-status-note.md`, `fixtures/prompt-summary-noritual.md` | by-eye protocol below |
| S2 | The execute ritual is loaded, stubbed to start at the Completion report: every phase has an outcome, and exactly one item beyond the pull request call needs the reader, an unresolved High security finding, third of the six phase lines. | `fixtures/summary-build-run.md`, `fixtures/prompt-summary-build.md` | by-eye protocol below; `scripts/ritual_deliverable_grader.py` for whether the next command is observed on its own line |
| G1 | The criterion guard: this case's no-ritual site, prompt and fixtures, unchanged. | `fixtures/prompt-noritual.md`, `fixtures/spec.md`, `fixtures/status-note.md` | `criterion-mention` |
| G2 | The reviewer guard: this case's reviewer and advocate sites, prompts and fixtures, unchanged. | `fixtures/prompt-reviewer.md`, `fixtures/prompt-advocate.md`, `fixtures/plan.md`, `fixtures/spec.md` | `reviewer-report`, `criterion-mention` |
| G3 | The slice guard: this case's slice site, prompt and fixtures, unchanged. | `fixtures/slice-run.md`, `fixtures/prompt-slice.md`, `fixtures/spec.md` | `covers-field`, `criterion-mention` |
| G4 | The spec review guard: the `finding-presentation-wording` case's `fsspecreview` site, with its prompt and canned reports used from that case's own fixtures. | that case's fixtures | that case's own `severity-name` and `reviewer-name`; by eye, below |

S1's and S2's fixtures set the reader's need in a domain no existing fixture and no rule text uses. Every item in
each fixture has an answer that changes a verdict under the protocol below: moving the planted item to the end,
dropping any item, or expanding a routine item past one line changes `leads`, `all-present` or `one-line`
respectively. S1's routine items are the coach, the programme, the rehearsals, the sheet music, the insurance and
the guesthouse; its planted item is the Marlow hall commitment (34 of 40 confirmed singers, the choir or the chair
to say whether to commit or release). S2's routine items are the simplify, correctness, state-coverage,
criterion-observations and push outcomes and the metrics line; its planted item is the security finding, which the
stub states as not fixed. Neither prompt mentions ordering, leading, line counts or completeness. Names are
invented throughout.

**Arms.** Every arm carries the always-loaded rules file at its stated revision. Within a site, arms differ in one
variable per pair.

| Arm | Site | Execute sources | Rules file | Added by |
|---|---|---|---|---|
| `summary-baseline-noritual` | S1 | none | `rules.md` at B | this task (built) |
| `summary-noritual` | S1 | none | `rules.md` at R | later task |
| `summary-baseline-build` | S2 | `execute/SKILL.md` and `_shared/execute.md` at B | `rules.md` at B | this task (built) |
| `summary-build-rules-only` | S2 | same, at B | `rules.md` at R | later task |
| `summary-build` | S2 | same, at E | `rules.md` at R | later task |
| `fourth-noritual` | G1 | none | `rules.md` at R | later task |
| `fourth-reviewer`, `fourth-advocate` | G2 | agent definition at R's base | `rules.md` at R | later task |
| `fourth-slice` | G3 | slice skill at B | `rules.md` at R | later task |
| `summary-guard-specreview` | G4 | that site's current sources at B | `rules.md` at R, nothing else moved | later task |

**Composition and provenance of the two built arms.** Both were composed by `git show` of each source at B and
concatenated in this order, joined by a single newline between sources, the same joining the earlier arms of this
case use (a skill file, then the rules file):

- `arms/summary-baseline-noritual.md` is `tools/craft/plugins/craft/rules.md` at B (13304 bytes), alone.
- `arms/summary-baseline-build.md` is `tools/craft/plugins/craft/skills/execute/SKILL.md` at B, a newline,
  `tools/craft/plugins/craft/skills/_shared/execute.md` at B, a newline, then `tools/craft/plugins/craft/rules.md`
  at B (113252 bytes). Every source is at `d860c95a`; none is from the working tree. This arm does not carry the
  record-links tail that the `ritual-deliverable-names-its-record` case's `execute-rule-only.md` carries: this
  case's S2 arm carries `rules.md` in its place, as every arm of this case does. It is built from the same two
  execute sources that case's live arm uses (`execute/SKILL.md` then `_shared/execute.md`), so E's edit to
  `_shared/execute.md` reaches both.

Each arm's bytes were checked once at compose time against a fresh `git show` concatenation of its sources at B and
are identical.

**Dispatch.** As `## Dispatch` above, with these differences. The prompt's `FIXTURES_DIR` is replaced as before.

- `summary-baseline-noritual`, `summary-noritual`, `fourth-noritual` and the other arms under the single-argument
  limit go by `--append-system-prompt "<contents of arms/<arm>.md>"`, as every earlier arm of this case did.
- `summary-baseline-build`, `summary-build-rules-only` and `summary-build` are about 113 KB, over the Linux
  single-argument limit, so they go by `--append-system-prompt-file arms/<arm>.md`, the same bytes by a file instead
  of an argument:

  ```
  claude -p "<prompt>" --setting-sources project --append-system-prompt-file arms/<arm>.md \
    --allowedTools "Read" < /dev/null > runs/<arm>-<n>.txt 2> runs/<arm>-<n>.stderr.txt
  ```

  This is a host deviation from `## Dispatch`, recorded here: the bytes the model receives are the arm's, and only
  the delivery differs. Precedent: the `ritual-deliverable-names-its-record` case's execute arm and
  `task/code-review-findings-read-like-council-findings-wherever-the-reader-sees-one`. An arm that fits the limit
  is dispatched by argument.

Three runs per arm. Capture naming follows `### Capture naming` above, with the arm's name as the prefix.

**By-eye protocol, fixed before any run.** AC11 and AC12 are not on the spec's list of criteria a script must
decide. Per capture, each of the three verdicts is read separately:

- `leads`: the first reader-facing sentence, after any heading, names the planted item that needs the reader. At S2
  the pull request decision also counts, provided the planted item is named before any phase outcome. Verdicts:
  `leads`, `buried` (named later), `missing`.
- `one-line`: every routine item the fixture enumerates takes at most one line, meaning one sentence or one list
  item with no sub-bullets. Grouping several items on one line is allowed. Verdicts: `one-line`, `over` (naming the
  items that ran over).
- `all-present`: every routine item the fixture enumerates, and the planted item, appears at least once (grouping
  allowed). Verdicts: `all-present`, `dropped` (naming the missing items).

A site passes on a clean majority of `leads`, of `one-line` and of `all-present`: 3 of 3, or 5 or 6 of 6 after a 2-1
split is re-run to six. Headroom: the baseline arm at the site is majority `buried` or `missing`, or majority
`over`. Where it is neither, that criterion is met without the edit at that site, and this is recorded. Captures are
read twice: once in session, and once by a subagent given this protocol and the captures renamed so it cannot tell
the arm. Disagreements are reported, not reconciled away. At G4, additionally by eye: the opening summary keeps one
row per finding that needs a decision, each with its id.

**The split rule.** A 2-1 split on any graded verdict, at a by-eye site or a guard (G1-G4), is re-run to six runs
before any verdict. After six, 5 or 6 of 6 is a clean majority; anything less is not.

**Guard bars.**

- G1, `fourth-noritual`: `criterion-mention` clean majority `text-with-number`; bar `third-noritual` at 3 of 3.
- G2, `fourth-reviewer` and `fourth-advocate`: `reviewer-report` every run `template-shape`; `criterion-mention`
  majority `bare`, equal to the baseline arms' majority.
- G3, `fourth-slice`: `covers-field` every run `gate-shape`; `criterion-mention` clean majority `text-with-number`.
- G4, `summary-guard-specreview`: that site's own checks against its last recorded bar, and the by-eye reading above.

**End states, fixed before any run.**

- S1 `summary-noritual` passes: commit R is kept.
- S1 fails: R is reverted on the branch and the slice stops for the operator. No rewording without a new dated
  extension the operator authorizes, per this case's precedent.
- Any guard G1-G4 misses its bar: this is a regression. R is reverted and the slice stops for the operator.
- R reverted, for any reason: the build-report task is blocked and not run, and the results task records the failed
  run and the revert.
- S2: commit E removes an example that demonstrates the contradicting order, so it is kept whatever its own
  measured effect (the spec's Constraints exempt removing a contradicting instruction). It is reverted only if
  `summary-build` reads worse than `summary-build-rules-only` on either verdict, or if the pull request command
  stops being alone on its own line (`next-command: own-line`). The effect it shows is reported either way.

**Fixture vocabulary leak check.** Run before this section was committed, against the verbatim text of both edits
above, and passed. The check is a throwaway script run from the repository root (not committed); it takes every 3-word
phrase (lower-cased, punctuation-split) of the four new fixture and prompt files, plus the fixture titles' content
words and the harness words `seeded`, `fixture`, `eval`, `runner`, `scaffold`, and searches R's text and E's text
for each. Against R: 0 hits. Against E: 0 hits on harness words and on any domain word; 5 phrase hits, every one
the execute procedure's own vocabulary that the stub has to reproduce to run the ritual (`state-coverage parent`,
`missing 0 criterion` across the two adjacent phase names, `0 criterion observations`,
`criterion-observations covered`, `/portage:pull_request`), each already present in `_shared/execute.md` and in E's
unchanged worked example, and none from the fixtures' invented content. Numbers in the fixture were chosen so the
fixture does not repeat the example's figures.

**Not measured.** Reports from dispatched agents other than the council reviewers: the builder, the drift check,
the code reviewer, the security auditor. One planted item per site; summaries with several decisions are not
staged. Results close as unmeasured until the operator names them at slice close.

## Baseline, 2026-10-20 — the by-eye reading of `summary-baseline-noritual` and `summary-baseline-build`, and the headroom verdict

Read under `**By-eye protocol, fixed before any run**` and `**The split rule**` above, unchanged. No amendment was
needed. One smoke run per site went through the arm's own dispatch path before the baseline (S1 by argument, S2 by
`--append-system-prompt-file`); both exited 0, and `ritual_deliverable_grader.py --record
task/the-tide-table-export-slice --command /portage:pull_request` on the S2 smoke capture printed
`next-command: own-line`. The registration is silent on smoke runs, so they are not baseline captures and are not
committed. Capture numbering therefore starts at 1 for the baseline.

S1 read 3 of 3 with no split. S2 split 2-1 on `leads` after three runs (and was re-run to six before any verdict
under the split rule): runs 4-6 are the re-run. Exit status 0 for all nine captures; the captures are
`runs/summary-baseline-noritual-1..3` and `runs/summary-baseline-build-1..6`.

### Per-capture table (in-session reading)

| Capture | Site | `leads` | `one-line` | `all-present` | Note |
|---|---|---|---|---|---|
| `summary-baseline-noritual-1` | S1 | buried | over (programme) | all-present | sentence 1 is "nearly ready"; Marlow is named in the next paragraph; an added programme-out-of-date speculation |
| `summary-baseline-noritual-2` | S1 | buried | over (insurance, guesthouse) | all-present | an added insurance-vs-guesthouse paragraph |
| `summary-baseline-noritual-3` | S1 | buried | over (insurance, guesthouse) | all-present | same shape as -2 |
| `summary-baseline-build-1` | S2 | buried | one-line | all-present | sentence 1 "built and pushed"; the security finding is sentence 2, ahead of every phase outcome |
| `summary-baseline-build-2` | S2 | leads | one-line | all-present | sentence 1 names the unfixed High finding |
| `summary-baseline-build-3` | S2 | buried | one-line | all-present | sentence 1 "I finished building"; the finding is sentence 2 |
| `summary-baseline-build-4` | S2 | leads | one-line | all-present | |
| `summary-baseline-build-5` | S2 | leads | one-line | all-present | |
| `summary-baseline-build-6` | S2 | leads | one-line | all-present | |

The measurement-tally sentence (a restatement that correctness found nothing) is read as the procedure's own line,
not an expansion of the correctness item, in every S2 capture; counting it as an expansion would make builds 1, 2,
4 and 6 `over` (4 of 6, a majority), and `one-line` at S2 would then show headroom; the readers did not count it, so
the verdict below stands on the not-counted reading.

### Second reading (a subagent given only the protocol, the sites description, the fixtures and the captures renamed `cap-A`..`cap-I`)

Mapping (kept by the reader's caller, not given to the reader): A noritual-2, B build-4, C build-5, D build-2, E
noritual-1, F build-6, G build-1, H noritual-3, I build-3. The subagent returned `leads`: S1 all `buried` (flagged a
close call), S2 builds 2, 4, 5, 6 `leads` and builds 1, 3 `buried` (close call); `all-present`: all nine
`all-present`; `one-line`: all nine `one-line`.

**Agreement:** `leads` 9 of 9 and `all-present` 9 of 9. `one-line` 6 of 9: S2 agrees 6 of 6; **S1 disagrees 3 of
3**. The in-session reading counted the closing "one thing to check" paragraph in each S1 capture as running a
routine item (programme; insurance and guesthouse) past one list item; the subagent read those paragraphs as not
routine items and every enumerated item as one list item. Not reconciled.

The `leads` verdict is literal: "the first reader-facing sentence names the planted item". Both readers noted that
every `buried` capture names the planted item in its second sentence or paragraph, before any routine item or phase
outcome. Read as "named before any routine item or phase outcome", S1 would be 3 of 3 `leads` and S2 6 of 6.

### Headroom verdict (derived from the table above, rule: majority `buried`/`missing` for `leads`, majority `over` for `one-line`)

| Criterion | S1 | S2 |
|---|---|---|
| `leads` | **headroom** (3 of 3 `buried`; both readers) | **none** (2 of 6 `buried`; 4 of 6 `leads`, met without the edit) |
| `one-line` | **contested** (3 of 3 `over` in-session: headroom; 0 of 3 `over` by the second reader: none) | **none** (0 of 6 `over`; 4 of 6 if the tally line is counted as expanding correctness, see above) |
| `all-present` | **none** (3 of 3 `all-present`; not a headroom criterion: nothing dropped, met without the edit) | **none** (6 of 6 `all-present`) |

Consequences recorded, not acted on here: S2 shows no headroom on `leads` or `one-line`, so the baseline already
puts the unfixed finding first in most runs and E cannot be shown to improve those verdicts; and S1's `leads`
headroom depends on the literal first-sentence reading above.

## Result, 2026-10-20 — R, the summary paragraph: the criterion guard G1 misses its bar; R is reverted

R is commit `d6eb84540f8e5b959554bc1578b0ece26154a76f` on the task branch (branch-local, so its SHA changes at merge;
recorded for a reader, read by no test). Its paragraph is byte-identical to the one quoted in the extension above
(checked by unwrapping the five wrapped lines of `rules.md` and comparing to the quoted text). Every arm below carries
the always-loaded rules at R; B is `d860c95a7d470be857e01f04c55c986abadab916`. Each arm was composed with `git show`
at build time, sources joined by one newline, the method checked first by reproducing `summary-baseline-build.md`
byte for byte (and, for the finding-presentation arm, `fs-treated2-fsspecreview.md` byte for byte from its pinned
sources). The new arms differ from their B counterparts by R's 325 bytes alone where one exists
(`summary-noritual` and `summary-build-rules-only` against the two baseline arms).

### Arms built

| Arm | Site | Sources (all at B except `rules.md`) | Rules file | Bytes | Form |
|---|---|---|---|---|---|
| `summary-noritual` | S1 | none | `rules.md` at R | 13629 | argument |
| `summary-build-rules-only` | S2 | `execute/SKILL.md`, `_shared/execute.md` | `rules.md` at R | 113577 | `--append-system-prompt-file` |
| `fourth-noritual` | G1 | none | `rules.md` at R | 13629 | argument |
| `fourth-reviewer` | G2 | `agents/breaker.md` | `rules.md` at R | 19307 | argument |
| `fourth-advocate` | G2 | `agents/advocate.md` | `rules.md` at R | 19694 | argument |
| `fourth-slice` | G3 | `skills/slice/SKILL.md` | `rules.md` at R | 57664 | argument |
| `summary-guard-specreview` (in `finding-presentation-wording`) | G4 | `skills/gauntlet/SKILL.md`, `skills/gauntlet/dispositions.md`, `skills/_shared/council.md` | `rules.md` at R | 91187 | argument |

`summary-noritual` and `fourth-noritual` are byte-identical (the rules file alone), as the registration's table
implies; they differ in the prompt and the check. The agent definitions at B are byte-identical to those at
`ecc4f46a`. `council.md` at B is not byte-identical to the `e568c4e0` copy the second-round `fs-treated2-*` arms
carry (it moved on main since), so `summary-guard-specreview` is the site's sources at B, as registered, and not the
`fs-treated2-fsspecreview` bytes with `rules.md` moved.

### Departures from the registration, stated

- G2 prompts. The registration lists `prompt-reviewer.md` and `prompt-advocate.md`. `council.md` at B already carries
  the shipped bullet (the reviewer's report goes to a dispatching agent), so the shipped state of a reviewer prompt is
  the template with the bullet, which is what `third-reviewer` and `third-advocate` were run with. `fourth-reviewer`
  and `fourth-advocate` were run with `prompt-reviewer-template.md` and `prompt-advocate-template.md`. The prompts
  without the bullet were not run.
- G4 prompt and the opening summary. The registered site, `fsspecreview`, stages the operator's request for the
  detail on C1 to C3; the opening summary is stated in its prompt, not written by the capture, so one row per finding
  in an opening summary cannot be read from it. `summary-guard-specreview` ran there three times, graded against the
  fs reports as registered. As a further observation (not a bar), the same arm ran three times on the
  `finding-presentation-wording` case's `prompt-specreview.md`, which stages the step that recommends and asks for
  acceptance, as `summary-guard-specreview-open-1` to `-3`, graded against `reports-spec-review.md`.
- S1 `leads` split was not re-run to six. G1 (below) misses its bar on a script verdict and fixes the end state
  whatever S1 reads, so the S1 re-run would not change what happens; the S1 verdict is recorded as unresolved.

Dispatch as `## Dispatch` and the extension fix it: fixtures copied to an empty temp directory per run,
`FIXTURES_DIR` replaced, the process started from a second empty directory, `--setting-sources project`,
`--allowedTools "Read"`, no `--model`, foreground, sequential. All captures exited 0 with empty stderr.

### Guards, script-graded (per-capture)

| Capture | Check | Verdict |
|---|---|---|
| `fourth-noritual-1`, `-2` | `criterion-mention` | `text-with-number` |
| `fourth-noritual-3`, `-6` | `criterion-mention` | `bare` |
| `fourth-noritual-4`, `-5` | `criterion-mention` | `nickname` |
| `fourth-slice-1` to `-3` | `criterion-mention` over the statement; `covers-field` | `text-with-number`; `gate-shape` |
| `fourth-reviewer-1` to `-3` | `reviewer-report`; `criterion-mention` | `template-shape`; `bare` |
| `fourth-advocate-1` to `-3` | `reviewer-report`; `criterion-mention` | `template-shape`; `bare` |

G1 split 2-1 after three (`text-with-number`, `text-with-number`, `bare`) and was re-run to six under the split rule.

| Guard | Registered bar | Count | Holds |
|---|---|---|---|
| G1 `fourth-noritual` | clean majority `text-with-number` | `text-with-number` 2 of 6, `bare` 2, `nickname` 2 | **no** |
| G2 `fourth-reviewer` | every run `template-shape`; `criterion-mention` majority `bare` | 3 of 3; `bare` 3 of 3 | yes |
| G2 `fourth-advocate` | same, equal to `baseline-advocate` (`bare`) | 3 of 3; `bare` 3 of 3 | yes |
| G3 `fourth-slice` | every run `gate-shape`; clean majority `text-with-number` | 3 of 3; 3 of 3 | yes |
| G4 `summary-guard-specreview` | last recorded bar: clean majority `plain` on `severity-name` and `reviewer-name` | `plain` 3 of 3 on both (scripted checks, registered prompt) | scripted yes; by-eye check not readable on this prompt (below) |

By eye at G4: each of the three captures gives C1, C2 and C3 each its own block under its id, with the proposed
disposition, but the registered by-eye check (one row per finding in the opening summary) cannot be read here, because
this prompt states the opening summary rather than writing it. Observation, `summary-guard-specreview-open-1` to `-3`
(the opening-summary prompt): `severity-name` `plain` 3 of 3; `reviewer-name` `plain`, `internal`, `plain`; each capture has a table with one row per finding that
needs a decision (two findings, ids C1 and C2) and the worth-fixing and rough-edge items grouped below it.
`summary-guard-specreview-open-2` names internal passes ("the divergence pass", "the fact check") in its opening
summary. That is the first non-`plain` `reviewer-name` at the specreview site since its edit (`treated-`, `regress-`
and `control-specreview` read `plain` on `reviewer-name` in 18 of 18 captures). It cannot be attributed to the summary
paragraph: the arm's `council.md`, `gauntlet/SKILL.md` and `gauntlet/dispositions.md` also moved to B, and no B
arm ran on the `specreview` prompt. G4 is an unresolved
2-1 split and a possible signal, not a hold.

### Sites S1 and S2 (rules-only), by eye

In-session reading, then a blind subagent given only the protocol, the two situations' fixtures and the six captures
renamed `cap-A` to `cap-F` in shuffled order (mapping kept by the caller: A `summary-build-rules-only-2`, B
`summary-noritual-3`, C `summary-build-rules-only-3`, D `summary-noritual-1`, E `summary-build-rules-only-1`, F
`summary-noritual-2`).

| Capture | Site | `leads` in-session | `leads` blind | `one-line` (both) | `all-present` (both) | Note |
|---|---|---|---|---|---|---|
| `summary-noritual-1` | S1 | buried | buried | one-line | all-present | sentence 1 "The tour is nearly ready."; the Marlow decision is sentence 2 |
| `summary-noritual-2` | S1 | leads | leads | one-line | all-present | sentence 1 names the Marlow date |
| `summary-noritual-3` | S1 | buried | leads | one-line | all-present | sentence 1 "One thing is still open, and it has a Friday deadline."; Marlow is named in sentence 2. **Readers disagree** |
| `summary-build-rules-only-1` | S2 | leads | leads | one-line | all-present | opens on the security decision, before any phase outcome |
| `summary-build-rules-only-2` | S2 | leads | leads | one-line | all-present | same |
| `summary-build-rules-only-3` | S2 | leads | leads | one-line | all-present | same |

Counts: S1 `leads` 1 of 3 in-session, 2 of 3 blind (a 2-1 split either way, not a clean majority; not re-run, see
above); S1 `one-line` 3 of 3 and `all-present` 3 of 3 by both readers (the in-session `over` reading of the baseline
was not repeated here: the closing "one thing to check" paragraphs of the baseline are gone and the Commit/Release
sub-bullets belong to the planted item). Against the baseline: S1 `leads` moves from 0 of 3 to 1 or 2 of 3; S1
`one-line` was contested at baseline and reads `one-line` 3 of 3 now; `all-present` met without the edit. S2 had no
headroom at baseline (`leads` 4 of 6); the rules-only arm reads `leads` 3 of 3, `one-line` 3 of 3, `all-present` 3
of 3, and `ritual_deliverable_grader.py --record task/the-tide-table-export-slice --command /portage:pull_request`
reads `next-command: own-line` on all three (read by hand; no test pins it). Close calls the blind reader flagged:
`cap-C`, `cap-E` hold two sentences in one list item (read as one list item, as at the baseline); `cap-B` and `cap-D`
on `leads`.

### End state

S1 `leads` is not a clean majority, but the end state does not rest on it: G1 misses its registered bar on a script
verdict (2 of 6 `text-with-number`, where `third-noritual` held 3 of 3). Under the registered end states, "Any guard
G1-G4 misses its bar: this is a regression. R is reverted and the slice stops for the operator", and "R reverted, for
any reason: the build-report task is blocked and not run". R is reverted by a signed `git revert` in the commit after
this one. `summary-build` is not run.

### Known unknowns resolved

- Does the section's limit make the build closing report read as exempt? No, the rules-only arm does move: `leads`
  3 of 3 (baseline 4 of 6) with the planted item opening every capture, though the baseline already did so in most
  runs, so the movement cannot be separated from the baseline's own rate.
- Does "at most one line each" shorten criterion mentions at G1 (AC1)? Not shown. G1 missed its bar: four of six
  captures give a criterion fewer of its own words than the grammar's threshold (two `bare`, two `nickname`). No
  same-day run at B on the G1 prompt isolates the sentence's role; the cause per capture is in "Observed failure
  mode" below.
- Does the paragraph reshape a reviewer's report, the slice's covered-criteria value, or the spec review's opening
  summary? No at the reviewer and advocate (`template-shape` 6 of 6 with unchanged `bare` mentions), no at the slice
  (`gate-shape` 3 of 3, `text-with-number` 3 of 3), and not on the scripted checks at the spec review (3 of 3 `plain`;
  the opening-summary observation is an unresolved split, in "Observed failure mode" below).

## Result, 2026-10-20 — the summary measurement closes: R was reverted, E was not run, AC11 and AC12 are not covered

This section completes the result above. The result above was corrected in place after the whole-change review,
before the branch merged (its G1 cause, its G4 reading and its slice-close reason); nothing else above it is
rewritten. It records every capture the measurement
took in one table, recomputes every count from that table, re-reads each pass condition against the rule
registered in the extension of 2026-10-20, and states the end state taken and how the slice closed. No eval was
run to write it. R is `d6eb84540f8e5b959554bc1578b0ece26154a76f` and its revert is `609dc65c` (both branch-local
SHAs, so they change at merge; recorded for a reader, read by no test). B is `d860c95a7d470be857e01f04c55c986abadab916`.

### Per-capture table

Sites S1 and S2 are read by eye under the registered protocol, twice: in session and by a blind subagent given the
protocol, the fixtures and the captures renamed. Where the table shows one value for the two readers they agreed.
Rows for `summary-baseline-*` are the baseline arms (rules at B); `summary-noritual` is S1 treated (rules at R);
`summary-build-rules-only` is S2 with only the rules at R. There is no `summary-build` arm (E was not run).

| Capture | Site | `leads` (session / blind) | `one-line` (session / blind) | `all-present` (session / blind) |
|---|---|---|---|---|
| `summary-baseline-noritual-1` | S1 | buried / buried | over / one-line | all-present / all-present |
| `summary-baseline-noritual-2` | S1 | buried / buried | over / one-line | all-present / all-present |
| `summary-baseline-noritual-3` | S1 | buried / buried | over / one-line | all-present / all-present |
| `summary-baseline-build-1` | S2 | buried / buried | one-line / one-line | all-present / all-present |
| `summary-baseline-build-2` | S2 | leads / leads | one-line / one-line | all-present / all-present |
| `summary-baseline-build-3` | S2 | buried / buried | one-line / one-line | all-present / all-present |
| `summary-baseline-build-4` | S2 | leads / leads | one-line / one-line | all-present / all-present |
| `summary-baseline-build-5` | S2 | leads / leads | one-line / one-line | all-present / all-present |
| `summary-baseline-build-6` | S2 | leads / leads | one-line / one-line | all-present / all-present |
| `summary-noritual-1` | S1 | buried / buried | one-line / one-line | all-present / all-present |
| `summary-noritual-2` | S1 | leads / leads | one-line / one-line | all-present / all-present |
| `summary-noritual-3` | S1 | buried / leads | one-line / one-line | all-present / all-present |
| `summary-build-rules-only-1` | S2 | leads / leads | one-line / one-line | all-present / all-present |
| `summary-build-rules-only-2` | S2 | leads / leads | one-line / one-line | all-present / all-present |
| `summary-build-rules-only-3` | S2 | leads / leads | one-line / one-line | all-present / all-present |

Scripted guard captures, graded by `wording_grader.py` (the G4 rows against the `finding-presentation-wording`
case's `fs-reports-spec-review.md` for the registered prompt, and `reports-spec-review.md` for the observation-only
`-open-` captures). Every row was re-graded by running the grader on the committed capture while writing this
section; each matches its `CAPTURE_EXPECTATIONS` / `FINDING_CAPTURE_VERDICTS` entry.

| Capture | Site | Scripted verdicts |
|---|---|---|
| `fourth-noritual-1` | G1 | criterion-mention: text-with-number |
| `fourth-noritual-2` | G1 | criterion-mention: text-with-number |
| `fourth-noritual-3` | G1 | criterion-mention: bare |
| `fourth-noritual-4` | G1 | criterion-mention: nickname |
| `fourth-noritual-5` | G1 | criterion-mention: nickname |
| `fourth-noritual-6` | G1 | criterion-mention: bare |
| `fourth-slice-1` | G3 | covers-field: gate-shape; criterion-mention: text-with-number |
| `fourth-slice-2` | G3 | covers-field: gate-shape; criterion-mention: text-with-number |
| `fourth-slice-3` | G3 | covers-field: gate-shape; criterion-mention: text-with-number |
| `fourth-reviewer-1` | G2 | reviewer-report: template-shape; criterion-mention: bare |
| `fourth-reviewer-2` | G2 | reviewer-report: template-shape; criterion-mention: bare |
| `fourth-reviewer-3` | G2 | reviewer-report: template-shape; criterion-mention: bare |
| `fourth-advocate-1` | G2 | reviewer-report: template-shape; criterion-mention: bare |
| `fourth-advocate-2` | G2 | reviewer-report: template-shape; criterion-mention: bare |
| `fourth-advocate-3` | G2 | reviewer-report: template-shape; criterion-mention: bare |
| `summary-guard-specreview-1` | G4 | severity-name: plain; reviewer-name: plain |
| `summary-guard-specreview-2` | G4 | severity-name: plain; reviewer-name: plain |
| `summary-guard-specreview-3` | G4 | severity-name: plain; reviewer-name: plain |
| `summary-guard-specreview-open-1` | G4 (observed) | severity-name: plain; reviewer-name: plain |
| `summary-guard-specreview-open-2` | G4 (observed) | severity-name: plain; reviewer-name: internal |
| `summary-guard-specreview-open-3` | G4 (observed) | severity-name: plain; reviewer-name: plain |

### Counts, derived from the two tables above

Each count is the number of rows in the table above with that value, taken by a script over the table's rows (no
count typed by hand).

| Arm | Site | `leads` session | `leads` blind | `one-line` session | `one-line` blind | `all-present` session | `all-present` blind |
|---|---|---|---|---|---|---|---|
| `summary-baseline-noritual` | S1 | 0 of 3 | 0 of 3 | 0 of 3 | 3 of 3 | 3 of 3 | 3 of 3 |
| `summary-baseline-build` | S2 | 4 of 6 | 4 of 6 | 6 of 6 | 6 of 6 | 6 of 6 | 6 of 6 |
| `summary-noritual` | S1 | 1 of 3 | 2 of 3 | 3 of 3 | 3 of 3 | 3 of 3 | 3 of 3 |
| `summary-build-rules-only` | S2 | 3 of 3 | 3 of 3 | 3 of 3 | 3 of 3 | 3 of 3 | 3 of 3 |

| Guard arm | Count of each scripted verdict |
|---|---|
| `fourth-noritual` (6 runs) | criterion-mention: text-with-number: 2, criterion-mention: bare: 2, criterion-mention: nickname: 2 |
| `fourth-slice` (3 runs) | covers-field: gate-shape: 3 |
| `fourth-slice` (3 runs) | criterion-mention: text-with-number: 3 |
| `fourth-reviewer` (3 runs) | reviewer-report: template-shape: 3 |
| `fourth-reviewer` (3 runs) | criterion-mention: bare: 3 |
| `fourth-advocate` (3 runs) | reviewer-report: template-shape: 3 |
| `fourth-advocate` (3 runs) | criterion-mention: bare: 3 |
| `summary-guard-specreview` (3 runs) | severity-name: plain: 3 |
| `summary-guard-specreview` (3 runs) | reviewer-name: plain: 3 |
| `summary-guard-specreview-open` (3 runs) | severity-name: plain: 3 |
| `summary-guard-specreview-open` (3 runs) | reviewer-name: plain: 2, reviewer-name: internal: 1 |


### Pass conditions, each re-read against its registered rule

| Registered condition | Reading | Holds |
|---|---|---|
| S1 `summary-noritual`: clean majority of `leads` (3 of 3, or 5 or 6 of 6 after a 2-1 split is re-run to six) | 1 of 3 in session, 2 of 3 blind: a 2-1 split by either reader, not re-run | **not shown** (no clean majority) |
| S1 `one-line` and `all-present`: clean majority | `one-line` 3 of 3 and `all-present` 3 of 3 by both readers | yes, though `one-line` at baseline was contested (3 of 3 `over` in session, 0 of 3 blind) and `all-present` needed no edit |
| S2 `summary-build-rules-only`: no pass condition of its own (E was the S2 edit); `leads`, `one-line`, `all-present` read | 3 of 3 each by both readers; baseline `leads` was already 4 of 6, so no headroom | not a pass condition; the movement cannot be separated from the baseline's own rate |
| G1: `criterion-mention` clean majority `text-with-number`, bar `third-noritual` 3 of 3 | 2 of 6 after the 2-1 split was re-run to six | **no, misses its bar** |
| G2: `reviewer-report` every run `template-shape`; `criterion-mention` majority `bare`, equal to baseline | 6 of 6 `template-shape`; `bare` 3 of 3 at each lens | yes |
| G3: `covers-field` every run `gate-shape`; clean majority `text-with-number` | 3 of 3; 3 of 3 | yes |
| G4: that site's own `severity-name` and `reviewer-name` against its last bar; by eye | `plain` 3 of 3 on both (registered prompt); the observation-only captures were `plain` on `severity-name` 3 of 3 and on `reviewer-name` 2 of 3 (`internal` on `-open-2`); the by-eye check is not readable on the registered prompt | scripted checks yes; the opening-summary reading is an unresolved split, not a hold |

### End state taken

G1 missed its bar. The registered end states say that any guard missing its bar is a regression, that R is
reverted and the slice stops for the operator, and that when R is reverted the build-report task is blocked and
not run. R was reverted by a signed `git revert` (`609dc65c`). **E was not made and `summary-build` was not run.**
At the slice stop the operator decided to record the failure and close the slice, rather than reword R (which needs
a new dated extension the operator authorizes, per this case's precedent) or ship E on its own.

### Exclusions and the disclosed departures

- Exclusions: none. Every capture exited 0 with empty stderr and every capture is in the tables above. The two
  smoke runs (one per new site) are not captures and are not committed, as the baseline section records.
- G2 ran the `-template` prompts (`prompt-reviewer-template.md`, `prompt-advocate-template.md`) in place of the
  registered `prompt-reviewer.md` and `prompt-advocate.md`, because the shipped state of a reviewer prompt carries the
  council template bullet; the prompts without the bullet were not run.
- G4's registered `fsspecreview` prompt states the opening summary in the prompt rather than writing it, so the
  opening-summary rows could not be read from it. The `-open-` captures observe the same arm on the
  `specreview` prompt as a further, observation-only reading (not a bar). G4's arm was composed from the council
  template at B, not from the older `fs-treated2` bytes.
- S1 `leads` was not re-run to six, because G1 fixed the end state whatever S1 read. The S1 verdict is unresolved,
  not failed.
- The finding-presentation arm contract test carries `SUMMARY_RULES_REVISION`, the full SHA of R, as the revision
  of `rules.md` the `summary-guard-specreview` arm reads; R is reverted, so that revision is no longer in the tree,
  and the arm remains the recorded copy. The SHA is branch-local and the test's comment names R by its commit subject.

### Observed failure mode, as design input

At G1 four of six captures gave a criterion fewer of its own words than the grammar's threshold (two `bare`, two
`nickname`), so the miss under the registered grammar stands and the end state is unchanged. The cause differs by
capture. Re-grading each of `fourth-noritual-3` to `-6` with one passage removed at a time
(`wording_grader.py --check criterion-mention --criteria fixtures/spec.md`): runs 3 and 6 are `bare` only because of
a later cross-reference to a criterion by number ("... in AC4") inside another criterion's bullet, and read
`text-with-number` without it; run 5 is the only one driven by the closing "Done" line (removing it gives
`text-with-number`); run 4 is `nickname` from shortened bullet labels (the AC3 and AC6 bullets each read `nickname`
on their own). No same-day run at B on the G1 prompt shows that the sentence "Give everything that needs nothing
from them at most one line each" is what shortened the mentions, so that causal link is a hypothesis, not a finding.
The same paragraph did not reshape a reviewer's report or the slice statement's covered-criteria value
(`template-shape` and `gate-shape` unchanged, `bare` and `text-with-number` mentions unchanged). The spec review is
an unresolved split: its scripted checks on the registered prompt read `plain` 3 of 3, but that prompt states the
opening summary, so the registered by-eye check (one row per finding in the opening summary) can only be read on the
`-open-` captures, where `reviewer-name` reads `plain`, `internal`, `plain`; see the G4 row above. This section does
not conclude the regression is specific to a no-ritual summary. A future attempt has to hold a
one-line routine item and a criterion mentioned by its own text together; this section does not propose wording.

### Limits

- One planted item per site; summaries with several decisions were not staged.
- Dispatched agents other than the council reviewers (the builder, the drift check, the code reviewer, the security
  auditor) are unmeasured.
- Three runs per arm, six where a 2-1 split was re-run; S1's `leads` split was not. One model tier. The by-eye
  readings are the in-session reader and one blind subagent; where they disagree (S1 `leads` on
  `summary-noritual-3`, S1 `one-line` at baseline) the disagreement is reported, not reconciled.
- Arms reach the model by `--append-system-prompt` (or `--append-system-prompt-file` for the S2 arms), not by the
  harness's own rules loading.

### Manual re-run trigger

There is no recurring runner. A new attempt at a summary paragraph starts from a new dated extension to this file,
registered before any run, with arms built from the changed `rules.md` and the pinned guards G1 to G4. The
existing `summary-baseline-*` arms are pinned at B and remain a valid baseline only while `rules.md` and
`_shared/execute.md` match B. The `fourth-*` and `summary-*` treated arms carry R's paragraph, which is no longer in
`rules.md`; re-running them measures a text that is not shipped. Also take a disagreement between real sessions and
these counts as the signal to re-measure.

### Slice close

**This slice delivers no coverage of AC11 or AC12.** The plan-time claim that it partially covers them did not hold:
R regressed AC1's guard (G1), which forced R's revert; S1 `leads` is unresolved. Two sites were measured, the
no-ritual summary (S1) and the build's closing report (S2, rules-only); the remaining sites, about eight whose own
instructions open a summary with routine detail, are listed in
`task/ritual-summaries-elsewhere-open-with-what-needs-the-reader`. AC11 and AC12 stay recorded as unmeasured.

One capture was edited after it was taken: in `fourth-reviewer-3.txt` one ordinary English word matched the
repository's machine-local leak denylist and was replaced with `[redacted]` so the tree passes the leak gate. The
grader's verdicts on that capture are the same before and after the edit (`template-shape` and `bare`).

## Extension, 2026-10-22 — a slice proposal opens with what you will be able to do, how to try it, and what is left: site P and sub-site PD, registered before any batch

Committed before any batch of a new arm; the two pilot captures recorded at the end are the only runs taken so far.
Nothing above this heading is rewritten; the grammar, verdict words, run rules, floor and exclusion rules, no-mention
rule and capture naming above apply unchanged to every arm named here except where this section adds a reading or a
rule of its own.

**What is under test.** One later prose edit, **P**, to slice choice's step 8 in
`tools/craft/plugins/craft/skills/slice/SKILL.md` and nowhere else (not `rules.md`, not `drive/SKILL.md`), and the
claim that follows from it: when slice choice states a chosen slice to the operator, the statement opens with what
the operator will be able to do once the slice is built (AC29), says how to try the result (AC30), and says what is
left of the spec afterwards, counted by the spec's own sections (AC31). Commit P does not exist yet; this section
registers the baseline, the readings, the guards, the bars and the end states, and the arms that carry P are not
created until P is.

**Base.** B is `437bbe8e`, the main-branch commit the task branch was cut from. Arms are committed copies composed
with `git show 437bbe8e:<path>`; no test reads history and no test recomposes an arm.

**Sites.** Both stage the run at step 8 of slice choice, after the gate has run and a slice has been chosen, and hand
over the same state.

| Site | What the session is doing | Stub | Prompt |
|---|---|---|---|
| `proposal` (P) | The slice skill is loaded, stubbed to start at step 8: the session states the chosen slice and then writes the covered-criteria value on a final separate line. | `fixtures/proposal-slice-run.md` | `fixtures/prompt-proposal.md` |
| `proposal-drive` (PD) | The driver is loaded as well, stubbed at the point where it runs slice choice's step 8 inline: after its single-repo check, before its plan phase. | `fixtures/proposal-drive-run.md` | `fixtures/prompt-proposal-drive.md` |
| enabler (observed only) | As P, with an enabler slice chosen on the enabler path. One capture per slice-site arm; not a bar. | `fixtures/proposal-enabler-run.md` | `fixtures/prompt-proposal-enabler.md` |

The two sub-sites share the spec fixture, the readings, the guards and the bars.

**Fixtures** (new files; every existing fixture is untouched). All are hand-written in the lending-library domain, with
the invented library, spec and slice names `Lantern Row`, `spec/holds-and-pickup`, `the hold shelf slice`. None reuses
the slice skill's own worked examples, none contains a credential-shaped value, and none carries an expected verdict.

- `fixtures/proposal-spec.md` — the spec. Ten one-line criteria `AC1` to `AC10` of similar length, as `- **AC<n>.**`
  bullets under three `###` sections of `## Acceptance Criteria` (Placing a hold, AC1 to AC4; Filling a hold, AC5 to
  AC7; Collecting at the desk, AC8 to AC10), and a `## Slices` ledger with one shipped slice covering AC1 and AC3 and
  partly covering AC2. It is the file the grader takes as `--criteria` for the proposal captures, and the real
  `candidate_set.py` reads it and prints the tokens the stubs carry.
- `fixtures/proposal-slice-run.md`, `fixtures/proposal-drive-run.md` — the stub for each sub-site. Each carries step
  4's gate tokens (`candidates:`, `partial:`, `complete-eligible:`, with `covered:`, `void:`, `closed:` and
  `closed-uncounted:`), the chosen slice, its value claim worded in the system's terms, its visual-surface call, and
  its full and partial coverage. The value claim has no "you will be able to", no way to try the result, and nothing
  about what is left. The driver stub hands over the same state.
- `fixtures/proposal-enabler-run.md` — the enabler stub: the same gate tokens, an enabler justification in place of a
  value claim, and no criterion made green.
- `fixtures/prompt-proposal.md`, `fixtures/prompt-proposal-drive.md`, `fixtures/prompt-proposal-enabler.md` — each
  names its stub, then asks for "the response you would give the operator at step 8" and, on a final separate line,
  the covered-criteria value. None lists any part of the statement.

In each prompt, `FIXTURES_DIR` stands for the directory holding a copy of `fixtures/`; the runner replaces it before
the run, as above.

**Arms.** Every arm carries the always-loaded rules file. Within a sub-site, arms differ in one variable per pair.

| Arm | Site | Sources, all at B (`437bbe8e`) | Added by |
|---|---|---|---|
| `proposal-baseline` | P | `skills/slice/SKILL.md`, then `rules.md` | this task (built, 57339 bytes) |
| `proposal-drive-baseline` | PD | `skills/drive/SKILL.md`, then `skills/slice/SKILL.md`, then `rules.md` | this task (built, 103214 bytes) |
| `proposal-treated` | P | as `proposal-baseline`, with `slice/SKILL.md` at P | later task |
| `proposal-drive-treated` | PD | as `proposal-drive-baseline`, with `slice/SKILL.md` at P | later task |

Composition: each source is `git show 437bbe8e:<path>`, joined by a single newline between sources, the same joining
the earlier arms of this case use (skill files first, the rules file last). The driver arm lists the driver first
because it is the session's outer ritual and it reads the slice skill. The arms carry no `_shared/slice.md`, as no
earlier slice arm of this case does. A treated arm differs from its baseline by `slice/SKILL.md` alone; the prompt and
fixtures are shared within a sub-site.

Arm sizes against the argv limit, measured before any capture: `getconf ARG_MAX` is 2097152; the Linux per-argument
limit (`MAX_ARG_STRLEN`, 32 pages of 4096) is 131072. The baseline arms are 57339 and 103214 bytes, both under it, so
every proposal arm goes by `--append-system-prompt "<contents of arms/<arm>.md>"` as `## Dispatch` above, with no
file-based deviation. A treated arm that grows past 131072 bytes goes by `--append-system-prompt-file` as the
summary extension records, and says so.

**Readings (by eye), fixed before any batch.** Per capture, read separately. A capture is the whole reply; the
statement is the reply without its final line. A **person** below means the operator, a librarian, a borrower, or any
named user of the spec; "the system sets the item aside" and "a message is sent" restate what the system does and are
not a person's ability.

- `opens` (AC29): `leads` when the first sentence of the statement, after any heading and not counting a sentence
  that only says a slice has been chosen or that nothing has been written yet, says what a person will be able to do
  once the slice is built; `later` when a sentence saying that appears anywhere after it; `missing` when no sentence
  does.
- `try-it` (AC30): `present` when the statement gives a concrete way to try the result once it is built (an action to
  take and what to look for afterwards); `missing` when it does not. A sentence about the next step of the ritual (the
  parent record, the plan) is not a way to try the result.
- `left` (AC31): `present` when the statement says what remains of the spec after this slice, in any form; `missing`
  when it does not.
- `left-correct` (AC31): `correct` when the remainder stated matches the ground truth below, both the count per section
  (or the same criteria listed by their own text, section by section) and the partly covered criterion named as the
  exception; `wrong` otherwise, including a remainder that still counts the slice's own criteria or gives only a
  total; `n/a` when `left` is `missing`. AC31 needs both `left` and `left-correct` to pass.

Observed only, not bars: whether the remainder is counted by section or listed; whether the opening addresses the
operator ("you") or the spec's consumer; each capture's line count (physical lines, blanks included), against the spec's
accepted risk that a summary fits one screen.

**Ground truth for `left-correct`, derived from the fixtures.** The reader derives it from the spec, its ledger and the
stub's coverage: the remainder is every criterion not fully covered by the ledger or by the stub's chosen slice, and
the partly covered criteria are those the ledger names as partly covered and no later entry fully covers. For
`spec/holds-and-pickup` after the hold shelf slice (AC5, AC6) that is six criteria: Placing a hold has two left of four
(AC2, AC4), Filling a hold has one left of three (AC7), Collecting at the desk has three left of three (AC8, AC9,
AC10), and AC2, the five-hold limit, is the partly covered criterion named as the exception. The numbers are recorded
here for the reader of this file; the second reader is not given them, only the derivation rule.

**The two-reader protocol.** Each capture is read twice: once in session, and once by a subagent given only the
readings and the derivation rule above, the fixtures, and the captures renamed `cap-A`, `cap-B`, ... so it cannot
tell the arm or the site (the mapping is kept by the caller). The second reader is not given this section's ground
truth numbers, any pilot reading, or any expected verdict. Disagreements are reported, not reconciled away.

**Script guards, run by the existing `scripts/wording_grader.py` with no new check, `--criteria` set to
`fixtures/proposal-spec.md`.** Every verdict word each check can return is listed here, before any batch.

| Check, over | Verdict word | Counts as |
|---|---|---|
| `criterion-mention`, over the statement (`--drop-value-line`) | `text-with-number` | pass |
| | `bare` | miss |
| | `nickname` | miss |
| | `no-mention` | neither (reported; cannot occur if the statement names its coverage, which the stubs require) |
| `covers-field`, over the final line | `gate-shape` | pass |
| | `off-shape` | miss |
| | `absent` | miss |

At a baseline arm the guards are observed and recorded, not bars; the pass and miss columns bind the treated arms.

**Bars.** A clean majority wins: 3 of 3 at three runs, 5 or 6 of 6 after a 2-1 split is re-run to six, as `## Runs,
sample size and splits` above. A guard miss in any of the first three runs of a treated arm also triggers the run to
six. Each arm runs three times, dispatched per `## Dispatch` above.

**Headroom gate and end states, fixed before any batch.**

- **Met without the edit.** A reading (`opens`, `try-it`, or `left` with `left-correct`) that is a clean-majority pass
  at the baseline, at both sub-sites, is recorded as met without the edit, and its part of commit P is dropped. Step 8
  has no instruction that contradicts any of the three, so nothing is kept by the contradiction exemption.
- **All three met at baseline.** Commit P, the treated arms and the treated batch are skipped, and the slice stops for
  the operator.
- **Closes.** A criterion closes as unmeasured, on its treated by-eye pass, when the operator names it at slice close.
- **Treated miss.** A treated arm that is not a clean majority on a reading it carries stops the slice for the
  operator. No rewording without a dated amendment the operator authorizes.
- **Guard miss.** A guard miss in the treated batch first gets a same-day paired control: the baseline arm, six runs.
  If the control also misses 2 or more of 6, the miss is marked residual. Otherwise it is a regression: commit P is
  reverted and the slice stops.

**Capture prefixes.** One per arm, then `-<n>`, counting dispatches from 1 and never reused: `proposal-baseline-<n>`,
`proposal-treated-<n>`, `proposal-drive-baseline-<n>`, `proposal-drive-treated-<n>`, and `proposal-enabler-<arm>-<n>`
for the enabler captures (`<arm>` being `baseline` or `treated`, or `drive-baseline` or `drive-treated`). Captures are
registered in `tools/craft/tests/test_wording_grader.py`'s capture table with the grader's verdicts for
`criterion-mention` over the statement and `covers-field` over the final line.

**Real-run recipe (one real `/craft:slice` session at B in the batch task, one on the branch at close-out).** Arms
carry the skill through the system prompt, so they cannot show what a real session reads or whether it picks a slice
the stub would not. These are go/no-go observations read by eye, not rates.

- **Fence.** Run inside `scripts/eval-sandbox <run-dir> -- claude -p ...`, with `LORE_CONFIG_DIR`, `LORE_STATE_DIR`,
  `XDG_CONFIG_HOME` and `XDG_STATE_HOME` pointed into the run directory. The vault there is seeded with the fixture
  spec, its ledger and the shipped slice's done parent. Before and after, record the live trailhead vault's git HEAD
  and `lore vault ls`. Abort before the run if the bound vault is not the isolated one; fail the task if the live
  vault's HEAD moved. `scripts/eval-sandbox` requires macOS (`scripts/eval-sandbox:42`); on any other OS this step stops
  and reports, and is never run unfenced.
- **Reading fields first.** The by-eye reading fields (`opens`, `try-it`, `left`, `left-correct`, and the observed
  fields) are written down before the run, as empty rows to fill.
- **Turn sequence.** Invoke slice choice on the seeded spec. It reconciles, gates and chooses, then states the slice
  at step 8; that statement is the artifact read. The session then writes the parent at step 9 in the isolated vault.
- **Step 9 check.** The written parent's `**Value claim:**` and `**Covers:**` keep their shapes; `covers_gate.py`
  exits 0 on the written covers value; nothing from the step 8 labels ("You will be able to", "Try it", what is left)
  is copied into the parent body.
- **Captures.** Transcripts are committed with the operator's home path and username replaced by `$HOME` and `$USER`
  placeholders (the placeholder convention in `tools/craft/MANUAL-EVAL.md`), then scanned with `capture_scan.py` and
  checked for the real home path and username.

**Not measured, stated at slice close.** The enabler path beyond its one observed capture per slice-site arm; a legacy
spec with no criterion identifiers; a spec with no sections; a real driver run (the driver is measured only through its
stubbed sub-site); the slice's final report after the parent is written (`slice/SKILL.md` `## Outcome`).

**Every committed capture** goes through `scripts/capture_scan.py` and is checked for the operator's home path and
username.

### Pilots, 2026-10-22 — one capture per baseline arm, before the protocol is frozen

Each was dispatched by the real path above: its own `claude -p` process, `--setting-sources project`, the arm by
`--append-system-prompt`, `--allowedTools "Read"`, fixtures copied to a fresh empty directory under `/tmp` with
`FIXTURES_DIR` replaced, the process started from a second empty directory. Both exited 0 with an empty stderr file;
both directories were removed afterwards. Read in session only; the second reader runs on the batch, not the pilots.

| Capture | Site | `opens` | `try-it` | `left` | `left-correct` | Observed | `criterion-mention` | `covers-field` |
|---|---|---|---|---|---|---|---|---|
| `proposal-baseline-1` | P | missing | missing | present | wrong | listed, not counted by section; addresses the operator; 19 lines | bare | gate-shape |
| `proposal-drive-baseline-1` | PD | missing | missing | missing | n/a | no remainder; addresses the operator; 15 lines | text-with-number | gate-shape |

- `proposal-baseline-1` opens "Here is the slice I've chosen ... I haven't written anything yet", names the slice, and
  gives the value claim under its label in the stub's system terms. It lists "Eight criteria are still open: AC2, AC4,
  AC5, AC6, AC7, AC8, AC9 and AC10", which still counts the slice's own AC5 and AC6, so `left-correct` is `wrong`. It
  names no way to try the result.
- `proposal-drive-baseline-1` opens "The next slice for `spec/holds-and-pickup` is **the hold shelf slice**", gives the
  same value claim under its label, the full and partial coverage with the criteria's own text and number, and the
  ritual's next steps. It says nothing about what is left and names no way to try the result.

**What the pilots showed about steering (the Known Unknown "whether the stub and prompt steer both arms toward one
shape").** Neither pilot echoed the stub's value-claim wording as an opening; both used it under a `Value claim:`
label after a sentence naming the slice, so the reading `opens` is `missing` and not an artefact of the stub's wording
sitting first. Neither prompt names a part, and neither capture produced one of the three parts unprompted except a
remainder at P. No fixture was revised and no pilot was repeated. The staging does not steer the shape, on one capture
per arm: that is the limit of this evidence.

**What the pilot showed about the driver stub (the Known Unknown "whether the driver stub reproduces what a real driver
run shows at step 8").** The driver pilot's step 8 output has the same shape as the slice pilot's: the slice, the value
claim, the visual-surface call and the full and partial coverage, with none of the three new parts, differing only in
the surrounding framing (the plan phase is named as next). So the driver stub's output resembles the slice stub's. It
does not show what a real driver run, which carries far more procedure, produces at step 8, and a real driver run stays
unmeasured.

**Protocol frozen.** The readings, guards, bars and end states above are frozen as of the pilots; the baseline batch is
run against this registration, and a change after a treated or shipped-state output has been seen is a dated amendment.

## Baseline, 2026-10-22 — the proposal baseline batches captured, graded and read; no reading is met without the edit

Run against the frozen registration above, unchanged. Each run was its own `claude -p` process by the `## Dispatch`
command (`--setting-sources project`, the arm by `--append-system-prompt`, `--allowedTools "Read"`, fixtures copied to a
fresh empty directory under `/tmp` with `FIXTURES_DIR` replaced, the process started from a second empty directory),
one run after another. All 15 captures exited 0 with empty stderr; no run was excluded. Captures: runs 1 (pilot) to 7 of
each baseline arm and one enabler capture. Runs 2 to 4 are the registered three; the 2-1 split on `left` (below) re-ran
each arm to six, runs 5 to 7. The tally below counts runs 2 to 7 (six per arm); the pilot is shown for completeness and
is not in the tally. Whether the pilot is counted does not change any verdict (stated per reading).

Two disclosed departures from the run rules: the last two dispatches of the first wave (`proposal-drive-baseline-4`
and `proposal-enabler-baseline-1`) were issued in one tool step and may have overlapped in time; each had its own
directories and its own process. A first attempt at `proposal-baseline-2` failed before any `claude` process started
(a wrong prompt-file argument in the runner); its three empty files were deleted and the number was used for the first
real dispatch, so no model run was discarded.

### Per-capture table

Readings: in-session reader (R1) and a blind subagent (R2) given only the registered readings, derivation rule,
protocol text, the spec and stub fixtures, and the captures renamed. Both gave the same value on every reading of every
capture, so one value is shown; the disagreements are listed after the table. Guards are `wording_grader.py`:
`criterion-mention` over the statement (`--drop-value-line`), then `covers-field`. Lines: physical lines, blanks included.

| Capture | Site | `opens` | `try-it` | `left` | `left-correct` | Remainder form | Opening addresses | Lines | `criterion-mention` | `covers-field` |
|---|---|---|---|---|---|---|---|---|---|---|
| `proposal-baseline-1` (pilot) | P | missing | missing | present | wrong | listed | operator | 19 | bare | gate-shape |
| `proposal-baseline-2` | P | missing | missing | present | correct | listed | operator | 22 | nickname | gate-shape |
| `proposal-baseline-3` | P | missing | missing | missing | n/a | none | operator | 15 | text-with-number | gate-shape |
| `proposal-baseline-4` | P | missing | missing | present | correct | listed | operator | 23 | nickname | gate-shape |
| `proposal-baseline-5` | P | missing | missing | present | wrong | none (only "the rest remain candidates") | operator | 17 | nickname | gate-shape |
| `proposal-baseline-6` | P | missing | missing | missing | n/a | none | neither | 15 | bare | gate-shape |
| `proposal-baseline-7` | P | missing | missing | present | wrong | listed (counts AC5, AC6) | operator | 18 | bare | gate-shape |
| `proposal-drive-baseline-1` (pilot) | PD | missing | missing | missing | n/a | none | operator | 15 | text-with-number | gate-shape |
| `proposal-drive-baseline-2` | PD | missing | missing | missing | n/a | none | operator | 15 | text-with-number | gate-shape |
| `proposal-drive-baseline-3` | PD | missing | missing | missing | n/a | none | operator | 15 | text-with-number | gate-shape |
| `proposal-drive-baseline-4` | PD | missing | missing | present | wrong | listed (AC8 to AC10 as one label) | operator | 20 | nickname | gate-shape |
| `proposal-drive-baseline-5` | PD | missing | missing | missing | n/a | none | operator | 17 | nickname | gate-shape |
| `proposal-drive-baseline-6` | PD | missing | missing | present | wrong | none (only "the other criteria still open") | operator | 17 | text-with-number | gate-shape |
| `proposal-drive-baseline-7` | PD | missing | missing | missing | n/a | none | operator | 15 | text-with-number | gate-shape |
| `proposal-enabler-baseline-1` | enabler | missing | missing | missing | n/a | none | operator | 17 | text-with-number | absent |

"Opening addresses" is `operator` when the first paragraph speaks to the person deciding ("I", "you"), `neither` when
it only names the slice; no capture addresses the spec's consumer. No remainder was counted by section; the five
that list one list criteria by their own text in AC order.

Disagreements between the two readers on the by-eye readings: none. On observed items the two differed only in how the
two vague remainders (`proposal-baseline-5`, `proposal-drive-baseline-6`) were labelled, which is not a verdict.
Borderline calls both readers flagged: those two captures as `left` present (read as present, `wrong`; reading them
`missing` would not change any count of passes); `proposal-drive-baseline-4` as `wrong` (its remainder has the right
six criteria but gives AC8 to AC10 as one label and AC2 as prose; a `correct` reading would make PD `left` 1 of 6, still
not a clean majority); `proposal-baseline-2` and `-4` as `correct` (the six criteria listed by text in AC order, AC2
named as the part-done exception, not grouped under section headings).

### Counts, derived from the table above (runs 2 to 7 per site; `left` pass = `left` present and `left-correct` correct)

| Reading | P pass | PD pass | Pilot-included (P / PD) |
|---|---|---|---|
| `opens` = `leads` | 0 of 6 | 0 of 6 | 0 of 7 / 0 of 7 |
| `try-it` = `present` | 0 of 6 | 0 of 6 | 0 of 7 / 0 of 7 |
| `left` present | 4 of 6 (runs 2, 4, 5, 7) | 2 of 6 (runs 4, 6) | 5 of 7 / 2 of 7 |
| `left` + `left-correct` pass | 2 of 6 (runs 2, 4) | 0 of 6 | 2 of 7 / 0 of 7 |

Guards (observed at a baseline, not bars): `covers-field` is `gate-shape` in 14 of 14 slice-site captures (7 P, 7 PD)
and `absent` in the enabler capture. `criterion-mention` over P runs 2 to 7: nickname 3, bare 2, text-with-number 1; over PD runs 2 to 7:
text-with-number 4, nickname 2. These are not bars at a baseline. Line counts, runs 2 to 7: P 15 to 23, PD 15 to 20.

Splits (registered rule, runs 2 to 4): `left` + `left-correct` split 2-1 at P (runs 2 and 4 pass, run 3 does not); at PD
the pass was 0 of 3 but `left` alone split 1-2 (run 4 present); `opens` and `try-it` were 0 of 3 at both sites. The
2-1 split triggered the re-run of both arms to six. No guard is a bar at a baseline, so no guard miss triggered one.

### Headroom verdict per reading (rule: a clean-majority pass, 3 of 3 or 5 to 6 of 6, at both sub-sites)

| Reading | P | PD | Verdict |
|---|---|---|---|
| `opens` | 0 of 6 | 0 of 6 | not met without the edit; its part of commit P is kept |
| `try-it` | 0 of 6 | 0 of 6 | not met without the edit; its part of commit P is kept |
| `left` with `left-correct` | 2 of 6 | 0 of 6 | not met at either sub-site (2 of 6 is not 5 or 6); its part of commit P is kept |

None of the three is met, so the all-three-met stop does not apply and commit P, with all three parts, goes ahead.
Counting the pilot does not change a verdict.

### What the table shows, and what it does not

- Per capture, the statement gives the value claim in the stub's system terms under a `Value claim:` label (or a similar
  label such as "why it matters") in all 14 slice-site captures, and none opens with what a person will be able to do; none gives
  a way to try the result. Sentences about the ritual's next step (for example `proposal-baseline-6`'s "you can run `/craft:plan`") were read
  as not a way to try the result, as registered.
- `left` is the only part that appears unprompted, and it appears in different shapes: a full list of six correct
  criteria (`proposal-baseline-2`, `-4`), a list that still counts the slice's own AC5 and AC6 (`proposal-baseline-1`,
  `-7`), a vague "the rest" (`proposal-baseline-5`, `proposal-drive-baseline-6`), and a list with AC8 to AC10 collapsed
  (`proposal-drive-baseline-4`). Per capture, the two wrong-by-counting-own-criteria captures are the two that state the
  remainder by echoing the gate's "remaining candidates" line, which is the only per-capture comparison available here.
  Any wider cause (that the gate tokens in the stub invite the miscount) is a hypothesis, not tested: no treated arm
  or ablation was run.
- PD shows less `left` than P (2 of 6 present against 4 of 6), a difference of 2 captures on six runs per arm; the
  table does not say whether the driver's extra procedure causes it.
- The enabler capture (observed only) carries no value claim, says so, and writes no covers value; it has no remainder.

### End state taken

Captures, rows and this section only. No arm, fixture, earlier table row, `rules.md`, `slice/SKILL.md` or
`drive/SKILL.md` was changed. Not run: any treated arm, any paired control, any real `/craft:slice` session.

## Treated, 2026-10-22 — the proposal treated batches captured, graded and read; every reading is a clean-majority pass at both sub-sites

Run against the frozen registration above, unchanged. **Provenance:** the treated arms were added at `9fad4e27`, with
`slice/SKILL.md` at commit P `57da08ac` (the `## Arms` table above is untouched). Arm sizes as run: `proposal-treated`
59315 bytes, `proposal-drive-treated` 105190 bytes (`wc -c`), both under the 131072 per-argument limit, so each went by
`--append-system-prompt`. Each run was its own `claude -p` process by the `## Dispatch` command (`--setting-sources
project`, `--allowedTools "Read"`, fixtures copied to a fresh empty directory under `/tmp` with `FIXTURES_DIR` replaced,
the process started from a second empty directory, both removed by their recorded path after the run), strictly one after
another, one process per tool step. Seven captures: runs 1 to 3 of each treated arm and one enabler capture
(`proposal-enabler-treated-1`, the `proposal-treated` arm with `fixtures/prompt-proposal-enabler.md`). All seven exited 0
with empty stderr; none was excluded; no run failed, so no number was skipped. Because every reading was 3 of 3 and no
guard missed, neither registered trigger to six fired, so there are three runs per arm, no re-run, and no paired control.

### Per-capture table

Readings: in-session reader (R1) and a blind subagent (R2) given only the registered readings, derivation rule and
protocol text, the spec and the three stub fixtures, and the captures renamed. The mapping, kept by the caller:
`cap-A` = `proposal-treated-3`, `cap-B` = `proposal-drive-treated-3`, `cap-C` = `proposal-enabler-treated-1`,
`cap-D` = `proposal-treated-2`, `cap-E` = `proposal-drive-treated-1`, `cap-F` = `proposal-drive-treated-2`,
`cap-G` = `proposal-treated-1`. R2 gave the same value as R1 on every reading of every capture, so one value is shown.
Guards are `wording_grader.py` (`criterion-mention` over the statement with `--drop-value-line`, then `covers-field`).

| Capture | Site | `opens` | `try-it` | `left` | `left-correct` | Remainder form | Opening addresses | Lines | `criterion-mention` | `covers-field` |
|---|---|---|---|---|---|---|---|---|---|---|
| `proposal-treated-1` | P | leads | present | present | correct | by section, one paragraph | operator | 19 | text-with-number | gate-shape |
| `proposal-treated-2` | P | leads | present | present | correct | by section | operator | 22 | text-with-number | gate-shape |
| `proposal-treated-3` | P | leads | present | present | correct | by section, plus a total | operator | 23 | text-with-number | gate-shape |
| `proposal-drive-treated-1` | PD | leads | present | present | correct | by section | operator | 24 | text-with-number | gate-shape |
| `proposal-drive-treated-2` | PD | leads | present | present | correct | by section | operator | 21 | text-with-number | gate-shape |
| `proposal-drive-treated-3` | PD | leads | present | present | correct | by section | operator | 24 | text-with-number | gate-shape |
| `proposal-enabler-treated-1` | enabler | leads | missing | present | correct | by section | operator | 22 | text-with-number | absent |

**Disagreements between the two readers: none.** Borderline calls flagged (neither changes a count):
`proposal-treated-2` opens "You will be able to rely on a returned item ... being kept for them" (read as a person's
ability, `leads`; the weakest of the six openings); `proposal-drive-treated-2` states Collecting at the desk as "none of
the three criteria are done yet" (read as three of three left); `proposal-treated-1` gives the remainder in prose with AC2
inside it (read `correct`); `proposal-treated-3` gives both a total and per-section counts, AC2 on its own "Partly done"
line (read `correct`). The enabler capture's `opens` is conditional on a later slice ("once the hold shelf slice ... lands",
read `leads`) and its `try-it` is a deliberate "there is nothing to try yet" (read `missing`); observed only, not a bar.

### Counts, derived from the table above (runs 1 to 3 per arm)

| Reading | P pass | PD pass |
|---|---|---|
| `opens` = `leads` | 3 of 3 (runs 1, 2, 3) | 3 of 3 (runs 1, 2, 3) |
| `try-it` = `present` | 3 of 3 | 3 of 3 |
| `left` present | 3 of 3 | 3 of 3 |
| `left` + `left-correct` pass | 3 of 3 | 3 of 3 |

Guards: `criterion-mention` `text-with-number` 3 of 3 at P and 3 of 3 at PD, no `bare` or `nickname`; `covers-field`
`gate-shape` 3 of 3 at P and 3 of 3 at PD, no `off-shape` or `absent`. Enabler (observed): `text-with-number`, `absent`.
Against the baseline (runs 2 to 7): `criterion-mention` `text-with-number` was 1 of 6 at P and 4 of 6 at PD, so counting
"what is left" did not shorten the criterion mentions in any treated capture. Lines: P 19 to 23, PD 21 to 24, enabler 22
(baseline P 15 to 23, PD 15 to 20); the one-screen risk is observed, not a bar, and the treated statements are 0 to 4
lines longer at the top of the range. Remainders: all six slice-site captures state it by section; none lists only a total.

### Verdicts re-read against their registered rules

- `opens`, `try-it`, `left`, `left-correct`: each is a clean-majority pass (3 of 3) at both sub-sites, the registered bar.
- Guard: no `bare`, `nickname`, `off-shape` or `absent` in any treated slice-site capture, so 0 of 3 per arm, below the
  2-of-6 miss bar and no first-three trigger; the registered run to six, and the paired control, did not apply.
- One capture per verdict word checked by hand against the grader's rule: `proposal-treated-1` is `text-with-number`
  (each criterion appears as its own text followed by its number, "... shown as on the hold shelf (AC5)"); the same
  capture's final line `AC5, AC6` is `gate-shape`; `proposal-enabler-treated-1`'s final line is prose in parentheses with
  no criterion identifiers, so `absent`. The grader agreed in each case.

### What the table shows, and what it does not

- Per capture, all six slice-site statements open with a "You will be able to" sentence, give a "Try it" with an action
  and what to look for, and state the remainder by section with AC2 named as the partly covered exception. At the
  baseline none of these appeared in 12 captures for `opens` and `try-it`. The by-eye readings are the claim; the arms
  carry commit P and are otherwise the baseline arms, so the change from 0 of 6 to 3 of 3 on each is the observed
  difference. No ablation separated which sentence of the step 8 edit produced which part, so any cause finer than "the
  arm with commit P" is a hypothesis.
- Three runs per arm is the registered minimum; it does not establish a rate.

### End state taken

- AC29 (`opens`): clean-majority pass at P (3 of 3) and PD (3 of 3); recorded as closing its criterion as unmeasured,
  pending the operator naming it at slice close.
- AC30 (`try-it`): clean-majority pass at P (3 of 3) and PD (3 of 3); closes as unmeasured, pending the operator.
- AC31 (`left` and `left-correct`): clean-majority pass at both (3 of 3 and 3 of 3 at each sub-site); closes as
  unmeasured, pending the operator.
- Guards: no miss; no paired control run. Treated miss: none. Nothing was reverted or reworded.

Captures, rows and this section only. No arm, fixture, earlier table row, `rules.md`, `slice/SKILL.md` or `drive/SKILL.md`
was changed. Not run: any baseline arm, any real `/craft:slice` session.
