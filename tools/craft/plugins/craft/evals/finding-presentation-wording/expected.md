# finding-presentation-wording — when a spec review, a plan's council review, a consult or the driver shows the reader a finding, are severity and reviewers named in plain words?

This file is the pass condition. It is committed before any arm is run, and the grammar, thresholds and verdict
words below may not be changed after a treated-arm output has been seen. A change that is needed anyway is made
as a dated amendment appended at the end, stating what was seen first, and only in the one window the
"Amendment allowance" below names.

## What is under test

The prose under test is craft's shared reporting contract, `tools/craft/plugins/craft/skills/_shared/council.md`,
which three rituals already point at for the shape of a finding and of a synthesis, plus the one place in consult's
skill that restates the severity grouping in its own words. A later task edits them. This case freezes the
baseline: what each ritual shows today.

The claim, in the words of the parent slice: when a spec review, a plan's council review or a consult presents
findings to the reader in session, severity is shown only as the guide's plain readings and reviewers only by
their plain labels, and the saved `## Council Review` block that plan and the driver write keeps its internal
levels.

Two parts of the claim are decided by script (`severity-name` and `reviewer-name`), one guards what must not
change (`persisted-levels`), and one is not decided by script: the order a finding is written in. The
finding-order criteria (AC17: a finding opens with one sentence carrying the decision asked for, the
recommendation and what is at stake; AC18: after that sentence, the worst case, the fix and who raised it) are
captured before and after, read by eye under the protocol below, recorded as unmeasured, and closed only when the
operator names them at slice close.

## The four sites

Every site stages "the reviewers have returned; present the result" from hand-written canned reports, so no
subagent runs. The session is told to run no commands and write no files.

| Site | What the session is doing | Canned input | Graded |
|---|---|---|---|
| consult | The consult ritual's synthesis step, on a question about plot swaps. | four lens reports (`reports-consult.md`) | `severity-name`, `reviewer-name` |
| plan | The planning ritual's council review step, on a small plan. It presents the review and then writes the `## Council Review` section it would append, in a fenced code block. | four lens reports (`reports-lenses.md`) | `severity-name`, `reviewer-name`, `persisted-levels` |
| specreview | The spec review's step that recommends and asks for acceptance, on a small spec. | eight pass reports (`reports-spec-review.md`) | `severity-name`, `reviewer-name` |
| driver | The driver's step 8, on the same plan as the plan site. It says what the step has it say and then writes the `## Council Review` section it would append, in a fenced code block. | the same four lens reports (`reports-lenses.md`) | `severity-name`, `reviewer-name`, `persisted-levels` |

Every arm at a site gets the same fixtures and the same prompt. Only the system prompt differs.

## Arms

Each arm is a file under `arms/` named `<arm>.md`. The manifest in
`tools/craft/tests/test_finding_presentation_wording_arms_contract.py` names each arm's sources and, per source,
a pinned revision. The test composes every arm from its manifest (the sources joined by one newline) and compares
it to the file byte for byte. Arms carry no provenance notes; provenance is the manifest's and this file's.

The baseline arms pin every source to `f68200933f69e0c00bc79634fb4758b8fa039c1c`, the commit the task branch was
cut from (origin/main at that time; the parent plan cites it as `f6820093`). All paths are under
`tools/craft/plugins/craft/`.

| Arm file | Site | Sources, in order | Added by |
|---|---|---|---|
| `baseline-consult.md` | consult | `rules.md`, `skills/consult/SKILL.md`, `skills/_shared/council.md` | this task |
| `baseline-plan.md` | plan | `rules.md`, `skills/plan/SKILL.md`, `skills/_shared/council.md` | this task |
| `baseline-specreview.md` | specreview | `rules.md`, `skills/gauntlet/SKILL.md`, `skills/gauntlet/dispositions.md`, `skills/_shared/council.md` | this task |
| `baseline-driver.md` | driver | `rules.md`, `skills/drive/SKILL.md`, `skills/_shared/council.md` | this task |
| `treated-<site>.md` | each | the baseline's sources, with `council.md` read from the edited working tree (and, at consult, `consult/SKILL.md` too) | later task, name reserved |
| `fallback-<site>.md` | a site whose treated arm fails | the treated arm's sources, with the one ritual file the fallback edits read from the working tree | later task, name reserved |

`rules.md`, with its shipped output-wording section, is held constant at the baseline revision in every arm,
because every real session loads it. Within a site, the treated arm differs from the baseline by `council.md`
alone, with one exception: at the consult site the treated arm also reads `consult/SKILL.md` from the working
tree, because the edit removes consult's own restatement of the severity grouping. The fallback arm differs from
the treated arm by that one ritual file. Arms for a variable nobody has changed yet are not created.

## Fixtures

All fixtures under `fixtures/` are written by hand. None pulls text from a vault record, and none contains a
credential-shaped value. They are set in a community allotment society's plot waiting list, a domain unlike the
wording fixtures of the other cases and unlike the examples `council.md` carries. The fixture prose avoids the
words critical, important, minor, builder, breaker, attacker, advocate, reliability, premise, consistency and
divergence, except inside the canned reports' severity tags and lens labels, which keep the council template's
form.

- `spec.md` — a small spec with six acceptance criteria and a stamped maturity.
- `plan.md` — a five-task plan against the spec.
- `reports-lenses.md` — four lens reports on the plan (Builder, Reliability, Security, Advocate), in the council
  template's format: a `## Findings` list of `[Critical]`, `[Important]` and `[Minor]` lines and a
  `## Confidence` line. There is a concrete Critical no synthesizer should downgrade (the one-run import with no
  undo), a Critical raised by two reviewers (stored queue places renumbered on a leave), and a Critical that is
  speculative and should be downgraded.
- `consult-question.md` and `reports-consult.md` — a question about direct plot swaps, and four lens reports in the
  same format with the same three kinds of Critical.
- `reports-spec-review.md` — a claim list and eight pass reports on the spec: fact verification, premise,
  Builder, Reliability, Security, Advocate, consistency and divergence, with one Critical raised by two passes.
- `prompt-consult.md`, `prompt-plan.md`, `prompt-specreview.md`, `prompt-driver.md` — one prompt per site. The
  plan and driver prompts also ask for the `## Council Review` section the ritual would persist, written inside a
  fenced code block.

In each prompt, `FIXTURES_DIR` stands for the directory holding a copy of `fixtures/`; the runner replaces it
before the run.

## Dispatch

Each run is its own `claude -p` process. The runner copies `fixtures/` into a throwaway directory under `/tmp`,
replaces `FIXTURES_DIR` in the prompt with that path, then starts the process from a second, empty throwaway
directory, so the arm cannot read this file or anything else in the repository:

```
claude -p "<prompt>" --setting-sources project --append-system-prompt "<contents of arms/<arm>.md>" \
  --allowedTools "Read" < /dev/null > runs/<prefix>-<n>.txt 2> runs/<prefix>-<n>.stderr.txt
```

The exit status goes to `runs/<prefix>-<n>.exit`. No `--model` flag is passed, so the process uses its default
model. Runs are sequential, in the foreground, each with a 600-second limit. On a host that has
`scripts/eval-sandbox` (macOS), the same command runs inside it; on a Linux host without it the command runs as
written, and the controls are the read-only tool grant, the empty working directory and the capture scans below.

### Capture naming

One prefix per site and arm: the arm's name without `.md`, then `-<n>` with `n` counting dispatches from 1, never
reused. A run's three files are `<prefix>-<n>.txt`, `<prefix>-<n>.stderr.txt` and `<prefix>-<n>.exit`. Replacement
and re-run dispatches continue the count.

| Prefix | Arm file | Prompt | Checks the grader runs | Expected under this pre-registration |
|---|---|---|---|---|
| `baseline-consult-<n>` | `baseline-consult.md` | `prompt-consult.md` | `severity-name`, `reviewer-name` | majority `internal` on both (headroom precondition) |
| `baseline-plan-<n>` | `baseline-plan.md` | `prompt-plan.md` | `severity-name`, `reviewer-name`, `persisted-levels` | majority `internal` on both naming checks; `persisted-levels` observed |
| `baseline-specreview-<n>` | `baseline-specreview.md` | `prompt-specreview.md` | `severity-name`, `reviewer-name` | majority `internal` on both (headroom precondition) |
| `baseline-driver-<n>` | `baseline-driver.md` | `prompt-driver.md` | `severity-name`, `reviewer-name`, `persisted-levels` | majority `internal` on both naming checks; `persisted-levels` observed |
| `treated-<site>-<n>` | `treated-<site>.md` | the site's prompt | the site's checks | clean majority `plain` on both naming checks; clean majority `kept` where `persisted-levels` runs (reserved; run by a later task) |
| `fallback-<site>-<n>` | `fallback-<site>.md` | the site's prompt | the site's checks | same as `treated-` (reserved; run only on a measured treated failure) |

`<site>` is `consult`, `plan`, `specreview` or `driver`.

## Verdict words

Each check prints one line per capture, with the check name and one of these words.

- `severity-name`: `plain`, `internal`, `none`.
- `reviewer-name`: `plain`, `internal`, `none`.
- `persisted-levels`: `kept`, `changed`, `missing`.

## Grammar

Fixed here, before any run. Every pattern the three checks use is listed in this section.

### Reading the capture

A **logical line** is built from the raw capture before any other step. A heading line (starting `#`), a list item
(a line whose first non-space characters are `-`, `*` or `+` followed by a space, or digits followed by `.` or `)`
and a space), a table row (first non-space character `|`) and a fence line (below) each start their own logical
line. Any other non-blank physical line joins the logical line before it, with a single space, unless a blank
line intervened, in which case it starts its own. A table row is read cell by cell: cells are split on `|`, a
separator row (cells of only `-`, `:` and spaces) is skipped, and a cell is a logical line of its own.

A **fence line** is a line whose trimmed text begins with three or more backticks, or three or more tildes. A
**fenced block** runs from an opening fence line to the next fence line that uses the same character, has at
least as many of it, and carries nothing else after trimming; an opening fence with no such line runs to the end
of the capture. The fence lines belong to the block.

The **normalised text** of a logical line is the line with the characters `*`, `_` and the backtick deleted, runs
of whitespace collapsed to one space, and the result trimmed.

### Reader-facing text

`severity-name` and `reviewer-name` read **reader-facing text** only: the capture's logical lines, in order, after
removing

1. every fenced block, and
2. every logical line that **quotes a reviewer report verbatim**.

A logical line quotes a reviewer report verbatim when all of these hold:

- its normalised text, after removing one leading `>` and any leading list marker (as defined above) and
  trimming, begins with one of the three tags `[Critical]`, `[Important]`, `[Minor]`;
- the text after that tag, trimmed, is at least 30 characters long; and
- that text is a prefix of the text after the same tag, trimmed, of the normalised text of some logical line of
  the case's canned report files (`fixtures/reports-*.md`), read with the same logical-line rule, that begins
  with that same tag.

So a line that reproduces a reviewer's tagged finding as written is a quotation and is not read. A line that
reuses the tag and rewords the finding, a line that names a reviewer, and a heading such as `### Builder` are not
quotations and are read. The grader takes the report files as its input for this rule.

### The severity names: `severity-name`

Matching is over the normalised text of each reader-facing logical line. A **whole word** is a match not adjacent
to a letter, a digit or a hyphen on either side.

**Internal severity patterns.** The capture is `internal` when any of these matches:

| Id | Pattern (case-sensitive) |
|---|---|
| S1 | the whole word `Critical` or `Criticals` |
| S2 | the whole word `critical` followed by one space and then the whole word `finding`, `findings`, `issue` or `issues` |
| S3 | the whole word `Important` or `Minor` followed by optional spaces and then `:`, `]` or `)`, or by optional spaces and a bracketed count (`(` or `[`, digits, `)` or `]`) |
| S4 | the whole word `Important` or `Minor` as the entire text of a heading line (after removing the leading `#`s), or as the entire text of any other logical line, optionally followed by a bracketed count |
| S5 | the whole word `Important` or `Minor` preceded by digits and one space, or followed by one space and then the whole word `finding`, `findings`, `issue` or `issues` |

S3 and S4 together cover `Important:`, `[Important]`, `Minor (2)`, a bold or heading group label, and the
normalised form of `**Minor**` alone on a line.

**Plain severity readings.** A plain reading appears when any of these phrases occurs in the reader-facing text,
matched case-insensitively and as a whole phrase: `needs your decision`, `worth fixing`, `nothing to decide`.

**Verdict.** `internal` when any internal pattern matches. Otherwise `plain` when at least one plain reading
appears. Otherwise `none`.

### The reviewer names: `reviewer-name`

Matching is over the normalised text of each reader-facing logical line, with the same whole-word definition.

**Internal reviewer patterns.** The capture is `internal` when any of these matches:

| Id | Pattern |
|---|---|
| R1 | case-insensitive, whole word: `builder`, `breaker`, `attacker`, `advocate`, `premise-attacker`, `consistency-auditor`, `divergence-prober` |
| R2 | case-sensitive, whole word: `Builder`, `Reliability`, `Advocate`, `Premise`, `Consistency`, `Divergence` |
| R3 | case-insensitive, whole phrase: `fact verification`, `fact-verification` |

`Security` is a lens label in the reports and is also a plain label, so it never makes a capture `internal`.

#### Reviewer labels

Every label a canned report heading carries is named here. The fixture-shape test reads this table: a label in a
report heading that is not in the first column fails it.

| Label in a report heading | Class | The plain label the reader sees |
|---|---|---|
| `Builder` | internal (R1, R2) | buildability |
| `Reliability` | internal (R2) | failure cases |
| `Security` | plain as written | security |
| `Advocate` | internal (R1, R2) | ease of use |
| `Fact verification` | internal (R3) | fact check |
| `Premise` | internal (R2) | right problem |
| `Consistency` | internal (R2) | self-consistency |
| `Divergence` | internal (R2) | left undecided |

**Plain reviewer labels.** A plain label appears when any of these occurs in the reader-facing text, matched
case-insensitively as a whole phrase: `buildability`, `failure cases`, `right problem`, `self-consistency`,
`left undecided`, `ease of use`, `fact check`, `security`.

**Verdict.** `internal` when any internal pattern matches. Otherwise `plain` when at least one plain label
appears. Otherwise `none`.

### The saved block: `persisted-levels`

`persisted-levels` reads only fenced blocks, never reader-facing text. The **block** is the first fenced block
that contains a line whose trimmed text is exactly `## Council Review`. A line inside the block is read after
trimming and removing one leading list marker.

- A **label line** is a line that begins with exactly `*Critical:*`, `*Important:*` or `*Minor:*` (single
  asterisks, as the schema writes them). A label is **present** when a label line carries it.
- A level's **entries** are the text after its label on the label line, if that text is non-blank, plus the text
  of each list-item line after the label line and before the next label line or the end of the block.
- A level's list **reads none** when it has no entries, or when every entry is, case-insensitively, `none` with
  an optional trailing period.

**Verdict.**

- `missing` when there is no block.
- `kept` when the block exists, all three labels are present, and the Critical list does not read none.
- `changed` otherwise: a label is absent or replaced, or the Critical list reads none.

It is a guard: its pass condition is a clean majority `kept` in the treated arm, with no headroom precondition.
The fixtures hold concrete Criticals, so a `kept` block always has something to list.

## The finding-order reading (AC17, AC18): by eye

No script decides these. For every capture the grader reads (baseline now, treated and fallback later), a person
reads, in this order:

1. The consolidated list the session presents, not the narrative paragraphs above it and not the fenced block.
2. Every finding in the group the session treats as most serious, and the first finding of each other group.
3. For each such finding, three observations, each written down as `yes` or `no` or `partly`:
   - **Opening:** does the finding's first sentence carry (a) the decision asked of the reader, (b) the
     recommendation and (c) what is at stake? Record each of the three.
   - **Order:** after the opening sentence, do the worst case, the `Fix:` line and who raised it follow in that
     order? Record `in order`, `out of order` (naming the first element out of place), or `missing` (naming the
     element).
   - **Plain:** does the opening read without the document under review in hand? Record `yes` or `no`.

The reading is written next to the capture's name in the results record. It is not counted in any pass condition,
not graded by script, and recorded as unmeasured. AC17 and AC18 close only when the operator names them at slice
close.

## Runs, sample size and splits

Three runs per arm. A check's verdict from three graded runs that all agree is decided. A 2-1 split is re-run:
three more runs of the whole arm (six in total, a fresh batch and not just the outlier), all six combined. A
**clean majority** is 3 of 3 at three runs, and 5 of 6 or 6 of 6 at six. 4 of 6 is not clean: the result is
`AMBIGUOUS`, which is not a pass and is reported as unresolved. Any residual rate that is not zero, for example
one run of six, is stated in the results, since the spec says "never".

**Majority** without the word clean (used for baselines) means more than half of the graded runs. A baseline in
which no verdict is held by more than half of the graded runs has no majority.

### Floor and exclusions

A **crashed run** is one whose `.exit` is non-zero, whose output is empty, or which did not finish within the
600-second limit. A run whose output says a tool it needed was refused is also excluded, judged by eye. A crashed
run is excluded and never graded. It stays committed under `runs/`, with its `.exit`, and is logged by ordinal and
reason in the commit that carries it and in the results record. It is replaced by a fresh dispatch with the next
ordinal. At most three replacements per arm; past that, stop and report, not keep retrying. The floor is three
graded runs per arm after exclusions. An arm that cannot reach the floor is reported unmeasured.

A run that completes cleanly is always counted. A run is never discarded for any other reason: not because its
output looks wrong, short, or unlike the others, and the crash carve-out never licenses discarding a clean run.

### Scans before commit

Every capture is read by hand for host paths and user names (a path, a user name or a machine name is redacted,
or the run is repeated, and the redaction is logged in the commit), and is run through
`python3 tools/craft/plugins/craft/scripts/capture_scan.py <path>` and `scripts/leak-gate` before it is committed
under `runs/`. Exit `0` is clean; the caller checks the exit code, never whether stdout is empty.

## Pass conditions

Per site and check, restated here and not referenced:

- **Headroom, per naming check:** the baseline arm is majority `internal` on `severity-name`, and majority
  `internal` on `reviewer-name`. If a baseline has no majority, or a majority of `none`, the site has no
  measurable headroom on that check: stop, change nothing, and report to the operator.
- **Treated, per naming check:** the `treated-<site>` arm reaches a clean majority `plain` on `severity-name` and
  on `reviewer-name`.
- **Guard, at the plan and driver sites:** the `treated-<site>` arm reaches a clean majority `kept` on
  `persisted-levels`.
- A site passes when its headroom precondition holds on both naming checks, its treated arm meets both treated
  conditions, and, at the plan and driver sites, the guard holds.

`none` counts as neither `internal` nor `plain` in any tally.

### End states, fixed before any run

- A site whose baseline is already majority `plain` on a naming check closes that check on the baseline's script
  verdict (met without the edit), and is not recorded as unmeasured.
- A site whose treated arm fails either naming check, or the guard, goes to the fallback.
- A site whose fallback also fails holds the whole slice from merging; the choice (a spec change, or a different
  carrier) goes to the operator.

### Fallback, fixed before any run

If a site's treated arm fails either check, and only then, that ritual gets one narrow edit of its own: the spec
review's step 5 part 4 "Important and Minor, compressed" (`skills/gauntlet/SKILL.md`), plan's step 8.5
presentation (`skills/plan/SKILL.md`), or the driver's step 8 persist instruction (`skills/drive/SKILL.md`). It is
measured as its own arm, `fallback-<site>.md`, which differs from the treated arm by that one file. The spec
review's skill file takes no net added lines. If the ritual-specific edit also fails, it is reverted, the run
stops, and the remaining choice is reported to the operator.

### Stop rule

Stop and report, without running anything further, when: a baseline has no headroom on a check; a treated arm and
its fallback both fail; an arm cannot reach the floor; or any run needs a fifth replacement. A treated arm is
never run before this file, the baseline captures and the grader exist and are committed, in that order.

## Amendment allowance

One dated amendment to the grammar is allowed, after the baseline hand check (one capture per verdict word per
site read by hand against the grader's verdict) and before any treated run. It is appended at the end of this
file under a dated heading, states what the hand check showed, and changes only patterns in the grammar section.
Nothing above its heading is rewritten; where it and the grammar disagree, the amendment is the rule the grader
implements. After the first treated or fallback output exists, no amendment is allowed: the grammar stands as it
is, and a flaw found later is reported in the results, not repaired.

## Not measured

- The finding-order criteria (AC17, AC18): captured, read by eye, recorded as unmeasured.
- Progress narration while passes run; real subagent runs; the saved status stamp of the spec review.
- The review ritual and the end-of-build review, whose reads of the shared contract are a later slice.

## Limits

- Arms carry skill text through `--append-system-prompt`, not a real skill invocation. The harness's own loading
  of a skill or rules file is not exercised.
- One model tier, the process default, at one Claude Code version. No other tier is run.
- The rituals' earlier steps are stubbed: each run starts at the presentation step with the reports in hand.
- The grammar is lexical. An ordinary English use of `Critical`, `Important` or `Minor`, or of `advocate` or
  `builder`, reads as internal; the baseline hand check exists to find that before a treated run.
- Three runs per arm resolve only a large effect.
- Runs are read-only (`--allowedTools "Read"`) and start from empty directories; behaviour with tools is not
  measured.

## Results: baseline grading and headroom (2026-10-15)

Written after the grader (`scripts/wording_grader.py`, checks `severity-name`, `reviewer-name`, `persisted-levels`)
was built to the grammar above and run over the 12 committed baseline captures in `runs/`. No treated or fallback
arm exists yet, and no grammar amendment was made: the hand check below found no misfire. Each site's captures
are graded against that site's own canned reports (`--reports fixtures/<reports file>`): consult
`reports-consult.md`, plan and driver `reports-lenses.md`, specreview `reports-spec-review.md`. The
capture-to-verdict table is `FINDING_CAPTURE_VERDICTS` in `tools/craft/tests/test_wording_grader.py`; the test
that reads it fails on any capture in `runs/` that is not in the table.

### Baseline verdicts and headroom

| Site | Check | Run 1 | Run 2 | Run 3 | Majority | Headroom |
|---|---|---|---|---|---|---|
| consult | `severity-name` | internal | internal | internal | internal | holds |
| consult | `reviewer-name` | internal | internal | internal | internal | holds |
| plan | `severity-name` | internal | internal | internal | internal | holds |
| plan | `reviewer-name` | internal | internal | internal | internal | holds |
| specreview | `severity-name` | internal | internal | internal | internal | holds |
| specreview | `reviewer-name` | internal | internal | internal | internal | holds |
| driver | `severity-name` | internal | internal | internal | internal | holds |
| driver | `reviewer-name` | internal | internal | internal | internal | holds |
| plan | `persisted-levels` | kept | kept | kept | kept | none required (guard) |
| driver | `persisted-levels` | kept | kept | kept | kept | none required (guard) |

Every site is majority `internal` on both naming checks, so no site is stopped by the headroom rule: no baseline
has a no-majority or `none`-majority result, and none is already majority `plain`. All 12 runs exited 0 and
none was excluded. `persisted-levels` is observed at plan and driver: all six baseline blocks keep the three
labels and list Criticals, so the guard has a `kept` baseline to hold in the treated arm.

### Hand check

One real capture per site was read by hand against the grader's verdict: `baseline-consult-1`,
`baseline-plan-1`, `baseline-specreview-1`, `baseline-driver-1` (the first three whole, the fourth its narrative,
list and fenced block). Each reads `internal` on both naming checks by eye as well: bold or heading level labels
(`### Critical`, `**Important (8):**`), `Critical` in the synthesis prose, and the lens names `Builder`,
`Reliability`, `Advocate` (and `Premise`, `Consistency` at specreview) in the prose and attributions.

Only `internal` (naming) and `kept` occur in the baseline. No baseline capture reads `plain`, `none`, `changed`
or `missing`, so those words were checked on constructed inputs in the tests, not on a real capture. The
hand check for those words per site is therefore not available from the baseline and is not claimed.

A scan of every baseline for the words `important`, `minor` and `critical` in any case, outside headings and
labels, found no ordinary English use in reader-facing text. The one lowercase occurrence is the hyphenated
`plan-critical` trigger name (driver captures), which the grammar's whole-word rule does not match.

### Unknown resolved

Ordinary English uses of `important`, `minor` or `critical` do not make the severity grammar misfire on the
baseline: no baseline capture has one, the lowercase forms are excluded by the case-sensitive patterns, and the
hyphen rule keeps `plan-critical` out. This says nothing about a treated capture, which may use them.

### Limits seen

- Every baseline capture also carries the internal forms in several places (headings, attributions), so none
  of the 12 verdicts rests on a single borderline match.
- The text immediately after a closing fence with no blank line between is read as its own line; the grammar
  does not say whether it joins the fence line. No baseline capture has that shape.

## Amendment, 2026-10-15: four readings added to the grammar

The one amendment the "Amendment allowance" permits. Nothing above this heading is rewritten; where this amendment
and the grammar disagree, this amendment is the rule the grader implements.

**What was seen first.** Only the 12 baseline captures exist. No treated or fallback output has been seen, and the
baseline hand check found no misfire. The operator approved spending the amendment, on 2026-10-15, to close a gap
found by reading the grammar's rules against the output a treated arm is likely to produce, not because a baseline
capture was misgraded. Likely treated output sets a level word as a group label behind a list or quote marker, or
as a label followed by a dash or a bracketed gloss, and the patterns above would read that as plain.

The reading below changes only patterns in the grammar section.

1. **S4 sees through a list marker and a quote marker.** Before S4 is tested, one leading `>` and then one leading
   list marker (as the grammar defines a list marker) are removed from the normalised text, after any leading `#`s
   of a heading line. So `- Minor`, `- **Important** (2)` and `> Minor` match S4. Only one of each is removed:
   `> > Minor` does not match.
2. **S6, a severity word leading a line as a group label.** A new internal severity pattern. The whole word
   `Important` or `Minor` at the start of a logical line's normalised text, after removing leading `#`s, one leading
   `>` and one leading list marker and trimming, followed by optional spaces and then a dash (`-`, `–` or `—`) or
   `(`. It catches `**Important** — would cause trouble later`, `Important (worth fixing, your call)` and
   `- **Minor** – rough edges`. Trade-off: a sentence that opens `Important (…)` or `Important —` in ordinary prose
   is read as internal. That errs against a pass, which is the safe direction for this instrument. S6 sits beside
   S1 to S5, so a line it matches makes the capture `internal` ahead of any plain reading.
3. **The report files are read as the grader reads them.** When the report files are read for the verbatim-quote
   rule, they are read with the same logical-line rule, their fenced blocks are dropped, and a report line's tag
   test is applied after removing one leading `>` and one leading list marker, the same as on the capture side.
   The fixture report lines are `- [Critical] …`, so without this no quotation could match.
4. **Text right after a closing fence.** A non-blank line directly after a closing fence line, with no blank line
   between, starts its own logical line; it does not join the fence line.

Readings 3 and 4 state what the grader already did. Readings 1 and 2 change it.

**Re-grade, 2026-10-15.** All 12 baseline captures were graded again under this amendment. Every verdict is the one
the results above record: `internal` on `severity-name` and `reviewer-name` at all four sites, `kept` on
`persisted-levels` at plan and driver, in all three runs each. The capture map does not change and the headroom
table above stands.

## Results: treated arms (2026-10-15)

Written after the four `treated-<site>` arms were run and graded. The arms are pinned at `EDIT_REVISION=af083a20` in
`tools/craft/tests/test_finding_presentation_wording_arms_contract.py`: each equals its baseline up to
`council.md` (and `consult/SKILL.md` at consult). 21 captures were taken: consult, specreview and driver split 2-1 on
`severity-name` in runs 1-3, so the "Runs, sample size and splits" rule added runs 4-6 (a fresh batch, all six
combined) at those three sites; plan was 3 of 3 and was not re-run. All 21 `.exit` files read 0, every `.txt` is
non-empty and every `.stderr.txt` is empty, so none was excluded or replaced. The grammar and its amendment were
used as they stand; no misfire was found, so nothing is repaired. Each capture is graded against its site's own
reports, as in the baseline results. The capture-to-verdict table is `FINDING_CAPTURE_VERDICTS` in
`tools/craft/tests/test_wording_grader.py`, which now carries a row for every treated capture.

### Treated verdicts and pass conditions

A clean majority is 3 of 3 at three runs and 5 or 6 of 6 at six. `none` counts for neither side.

| Site | Check | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 | Run 6 | Tally | Treated condition |
|---|---|---|---|---|---|---|---|---|---|
| consult | `severity-name` | plain | internal | plain | plain | plain | plain | 5 of 6 plain | pass |
| consult | `reviewer-name` | plain | plain | plain | plain | plain | plain | 6 of 6 plain | pass |
| plan | `severity-name` | plain | plain | plain | not run | not run | not run | 3 of 3 plain | pass |
| plan | `reviewer-name` | plain | plain | plain | not run | not run | not run | 3 of 3 plain | pass |
| plan | `persisted-levels` | kept | kept | kept | not run | not run | not run | 3 of 3 kept | pass (guard) |
| specreview | `severity-name` | plain | internal | plain | plain | plain | plain | 5 of 6 plain | pass |
| specreview | `reviewer-name` | plain | plain | plain | plain | plain | plain | 6 of 6 plain | pass |
| driver | `severity-name` | plain | plain | internal | plain | plain | plain | 5 of 6 plain | pass |
| driver | `reviewer-name` | plain | plain | plain | plain | plain | plain | 6 of 6 plain | pass |
| driver | `persisted-levels` | kept | kept | kept | kept | kept | kept | 6 of 6 kept | pass (guard) |

Every site holds its headroom precondition (the baseline results above) and meets every treated condition, so
every site passes: consult, plan, specreview and driver.

### Residual leak rate

- consult: 1 of 6 runs used an internal severity name (run 2); 0 of 6 an internal reviewer name.
- plan: 0 of 3 runs used an internal severity or reviewer name; 0 of 3 changed the saved block.
- specreview: 1 of 6 runs used an internal severity name (run 2); 0 of 6 an internal reviewer name.
- driver: 1 of 6 runs used an internal severity name (run 3); 0 of 6 an internal reviewer name; 0 of 6 changed the
  saved block.

The spec says "never"; the rate is not zero at three of four sites. Each of the three leaks matched S1 (the whole
word `Critical`), and each was confirmed by reading the line, so none is a grader misfire:

- consult-2: "The ease-of-use reviewer's Critical, that tenants "might feel the process is unfair", names no
  failure ..." (in the synthesis prose).
- specreview-2: "**Recommended outcome:** fix both Criticals in this round, and the spec then moves to `ready`."
- driver-3: "... under the `plan-critical` trigger, which applies when a Critical survives synthesis", and again, in the
  decision list, "... which is rated Critical" and "production failure visibility is Critical at this spec's
  production level".

In all three the word is the model describing its own regrading or the trigger, in prose beside otherwise plain
labels; the finding headings, groups and attributions in those captures are plain.

### Outcome rule

At least one site passes (all four do), so the edits to `council.md` and `consult/SKILL.md` are kept. No revert.

The fallback in "Fallback, fixed before any run" is reserved for a site whose treated arm fails a check. No site
fails, so the fallback has no failing site to act on and no `fallback-<site>` arm is created or run.

### Finding order (AC17, AC18): an observation, not a verdict

Read by eye from the captures while checking the leaks above, not under the full protocol, not counted in any pass
condition, and still recorded as unmeasured. AC17 and AC18 close only when the operator names them at slice close.

- consult (6 runs): every capture puts a narrative (what the reviewers found, whether it holds, what to do) before
  the grouped list; in the list each finding opens with a bold sentence naming the problem, and in 5 of 6 that
  sentence or the next carries the decision and an "I recommend" (run 6 states the condition without a
  recommendation on its first finding). Worst case, `Fix:` and `Raised by:` follow in that order in 6 of 6.
- plan (3 runs): each finding in the first two groups opens with one bold sentence carrying the question, "I
  recommend" and what is at stake, then worst case, `Fix:` and `Raised by:` in order, in 3 of 3 (the lower groups
  in runs 1 and 3 fold the worst case into the opening sentence).
- specreview (6 runs): the two or three top findings are table rows (id, finding, disposition, proposed edit), not
  sentences with a decision, recommendation and stake; the recommendation sits in the narrative and the
  `Recommended outcome` line above the table, and there is no per-finding worst case or `Fix:` line. Who raised a
  finding is in the narrative, not the row. The shape the criteria describe is not what these captures show.
- driver (6 runs): the same shape as plan: the opening sentence carries a question or statement, "I recommend" (or
  "I'd recommend") and the stake in 6 of 6 for the top group, then worst case, `Fix:` and `Raised by:` in order;
  run 6 puts the recommendation in the bold line and the stake in the next sentence.

### Limits seen

- The three leaks are real uses of the internal word, not lexical misfires, so the grammar's trade-off in
  "Limits" (ordinary English reading as internal) did not bite on a treated capture. No ordinary-English use of
  `important`, `minor` or `critical` appeared in any treated capture outside the three leaks.
- The finding-order notes above are impressions from reading the captures, not the protocol's per-finding record.
- Consumers of `skills/_shared/council.md` (grep `council.md` under `tools/craft/plugins/`, excluding `evals/`;
  4 files, 25 lines; no agent file names it). Each skill's eval site is the one whose arm loads that skill:
  - `skills/consult/SKILL.md` lines 19, 45, 48, 57, 89: the consult site (this is the only consumer whose own
    restatement was edited, and its treated arm carries the edit).
  - `skills/plan/SKILL.md` lines 450, 457, 459, 461, 474, 526, 577: the plan site.
  - `skills/gauntlet/SKILL.md` lines 47, 127, 162, 167, 172, 186, 194, 239, 319, 470: the specreview site. The
    eval covers the step that recommends and asks for acceptance; the dispatch and synthesis steps at lines 162 to
    194 are not presented to a reader in the staged run.
  - `skills/drive/SKILL.md` lines 292, 300, 335: the driver site (step 8).
  - Outside the plugin, not a skill or agent: `tools/craft/README.md` line 77 names it as the single source of
    council membership; no eval site applies.
  - The review skill, the end-of-build review and every agent file under `agents/` do not name `council.md`; they
    are not consumers and no site covers them (see "Not measured").

## Results: finding-order reading, close-out and a real consult run (2026-10-15)

Written after the treated results above. It carries the by-eye finding-order reading of every baseline and treated
capture, the close-out of the fallback, and one real `/craft:consult` run. Nothing above this heading is rewritten.

### Fallback and live-arm rebuild

No site failed. Every site passed both naming checks and, at plan and driver, `persisted-levels` (the table under
"Results: treated arms (2026-10-15)"). The fallback in "Fallback, fixed before any run" is reserved for a failing
site, so no skill edit was made, no `fallback-<site>` arm was created and no arm was re-run by this task. No
`gauntlet/SKILL.md` or `plan/SKILL.md` edit was made either, so the record-link case's live arms needed no rebuild.

### The finding-order reading (AC17, AC18)

**Who read.** An agent read these captures, not a person. The protocol in "The finding-order reading (AC17, AC18):
by eye" says "a person reads". The reading is therefore a first pass for the operator to check, and it closes
nothing: **AC17 and AC18 are recorded as unmeasured**, and the operator's naming at slice close is what closes them.

**Protocol as applied.** For each of the 33 captures (12 baseline, 21 treated): the consolidated list only (not the
narrative paragraphs, not the fenced block); every finding in the most serious group and the first finding of each
other group; and per finding the three observations. Judgement rules used, so a reader can redo it:

- **Opening (a)** is `yes` when the first sentence or heading asks the reader to decide something ("Decide whether",
  "Should ...?"), `partly` when it recommends without asking, `no` otherwise. **(b)** is `yes` when the opening carries
  an "I recommend" or an imperative that is the recommendation, `no` when the recommendation first appears on the
  `Fix:` line or in a table cell. **(c)** is `yes` when the opening names what is at stake in so many words,
  `partly` when it only names the problem, `no` when it names neither.
- A table row is read as a finding whose opening is its finding cell. Its `proposed disposition` cell stands for the
  decision and its `proposed edit` cell for the recommendation, so a table row at most reaches `partly` on (a) and (b).
- **Order** is `in order` when the worst case, the `Fix:` line and who raised it follow the opening in that order,
  `out of order` naming the first element out of place, or `missing` naming the element. A worst case written as an
  unlabelled sentence right after the opening is read as present.
- **Plain** is judged strictly: an opening that leans on a spec detail it does not explain (for example "the time of
  day the 14 days end", or "Tasks 2 and 5") is `no`.

Each line reads: group, the finding's short title, then the three observations.

- `baseline-consult-1` (3 findings read)
  - most serious group, "Two tenants can both be promised the same plot": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the second group, "A good plot could be sold and passed off as a swap": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the last group, "The confirmation email should name both plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `baseline-consult-2` (3 findings read)
  - most serious group, "Two tenants can be given the same plot": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the second group, "A swap could keep a good plot away from the waiting list": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "The confirmation email should name both plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `baseline-consult-3` (3 findings read)
  - most serious group, "A tenant can arrive to find a stranger on the plot they swapped for": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the second group, "A swap could hide a paid plot transfer that passes over the waiting list": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the last group, "Confirmation emails should name both plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `baseline-plan-1` (5 findings read)
  - most serious group, "1 One freed plot can be offered to two people (at once)": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - most serious group, "2 The paper-list import can't be checked first / undone": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - most serious group, "3 If the nightly offer job stops, nobody finds out": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the second group, "Opening the offer email's link accepts the plot": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the last group, "A grant's reason may expose personal details (bullet)": Opening (a) no, (b) no, (c) partly; Order missing, the Fix line; Plain yes.
- `baseline-plan-2` (5 findings read)
  - most serious group, "1 One freed plot can be offered to two people (at once)": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - most serious group, "2 The paper-list import can't be checked first / undone": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - most serious group, "3 If the nightly offer job stops, nobody finds out": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the second group, "A forwarded email or mail scanner can accept a plot for someone": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the last group, "Tasks 2 and 5 both format dates; share one helper (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain no.
- `baseline-plan-3` (5 findings read)
  - most serious group, "1 One freed plot can be offered to two people (at once)": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - most serious group, "2 The paper-list import can't be checked first / undone": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - most serious group, "3 If the nightly offer job stops, nobody finds out": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the second group, "4 Opening the offer email's link accepts the plot": Opening (a) no, (b) no, (c) partly; Order out of order, who raised it, in the heading line, comes before the worst case; Plain yes.
  - first finding of the last group, "No test covers an offer exactly 14 days old (bullet)": Opening (a) no, (b) no, (c) no; Order missing, worst case and the Fix line; Plain yes.
- `baseline-specreview-1` (4 findings read)
  - most serious group, "C1 paper-list people without email lose their place": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 an offer that never arrives empties the plot, then sends a long-waiting person to the back": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - first finding of the second group, "No criterion says who marks a plot as freed": Opening (a) no, (b) no, (c) partly; Order missing, worst case, Fix line and who raised it; Plain yes.
  - first finding of the last group, "When during the day the 14 days end": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `baseline-specreview-2` (4 findings read)
  - most serious group, "C1 paper-list people have no route onto the online queue": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 an offer that never arrives costs the person their place (Reliability + Advocate)": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the proposed-edit cell stands for the Fix line); who raised it sits inside the finding cell, ahead of the edit; Plain yes.
  - first finding of the second group, "A person can't see their current place after the first email (Consistency + Advocate)": Opening (a) no, (b) no, (c) no; Order missing, worst case and the Fix line; Plain yes.
  - first finding of the last group, "wording around "a few times a month"": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `baseline-specreview-3` (4 findings read)
  - most serious group, "C1 paper-list people have no path into the online queue": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 an undelivered offer leaves the plot empty then moves the person to the back": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - first finding of the second group, "Missing triggers and states (nobody marks a plot freed; empty queue)": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain yes.
  - first finding of the last group, "the time of day the 14 days end": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `baseline-driver-1` (5 findings read)
  - most serious group, "1 One freed plot can be offered to two people": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - most serious group, "2 A bad or repeated import can't be undone": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - most serious group, "3 If the nightly offer job stops, nobody finds out": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the second group, "A person granted a plot out of turn can be offered a second one": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "The out-of-turn reason a warden types is visible to every committee member (bullet)": Opening (a) no, (b) no, (c) partly; Order missing, the Fix line; Plain yes.
- `baseline-driver-2` (5 findings read)
  - most serious group, "1 One freed plot can be offered to two people": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - most serious group, "2 A bad or repeated import can't be undone": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - most serious group, "3 If the nightly offer job stops, nobody finds out": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the second group, "A warden's out-of-turn grant leaves the person in the queue": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "Tasks 2 and 5 each format dates; share one helper (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain no.
- `baseline-driver-3` (5 findings read)
  - most serious group, "1 One freed plot can be offered to two people": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - most serious group, "2 Importing the paper list can't be checked first or undone": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - most serious group, "3 If the nightly offer job stops, nobody finds out": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the second group, "Someone given a plot out of turn can still be offered another one": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "No test covers an offer exactly 14 days old (bullet)": Opening (a) no, (b) no, (c) no; Order missing, worst case and the Fix line; Plain yes.
- `treated-consult-1` (3 findings read)
  - most serious group, "If swaps complete without the warden, two of them can hand out the same plot": Opening (a) partly, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "A "swap" could hide a paid sale of a good plot": Opening (a) yes, (b) yes, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "The confirmation email should name both plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-consult-2` (3 findings read)
  - most serious group, "Allowing direct swaps without a single, live record of plot holders would let two swaps claim the same plot": Opening (a) partly, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "Direct swaps could pass over people on the waiting list": Opening (a) partly, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "The swap confirmation email doesn't name the plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-consult-3` (3 findings read)
  - most serious group, "1 Decide whether the form checks plot holdings at the final confirmation": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "2 Decide whether the warden must approve swaps": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "7 The swap confirmation email should name both plots": Opening (a) no, (b) yes, (c) partly; Order missing, the worst case; Plain yes.
- `treated-consult-4` (3 findings read)
  - most serious group, "Two swaps can claim the same plot, and one tenant arrives to find a stranger on it": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "Swaps can become a back door past the waiting list": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "The confirmation email should name both plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-consult-5` (3 findings read)
  - most serious group, "Two swaps can claim the same plot, and both go through": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "A direct swap can hide a sale and pass over the waiting list": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "The swap confirmation email should name both plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-consult-6` (3 findings read)
  - most serious group, "Two swaps can claim the same plot": Opening (a) partly, (b) partly, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "A paid "swap" can pass over the waiting list": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "The swap confirmation email doesn't name the plots (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-plan-1` (5 findings read)
  - most serious group, "1 Should queue order be stored as a renumbered place, or worked out from join time?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "2 Should the paper-list import be allowed to run once, live, with no preview or undo?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "3 Should the nightly offer job be able to fail without anyone noticing?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "4 The offer link accepts a plot just because it was opened. Put a confirm page behind it": Opening (a) no, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "Dates are formatted in two places; Tasks 2 and 5 should share a helper (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain no.
- `treated-plan-2` (5 findings read)
  - most serious group, "Should the queue stop storing place numbers?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "Should the paper-list import get a preview and a way to undo it?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "Should the nightly offer job tell someone it ran, and alarm when it didn't?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "Should an out-of-turn grant also take the person off the queue?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "Add a test for an offer exactly 14 days old (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-plan-3` (5 findings read)
  - most serious group, "1 Should the queue order come from join time instead of a stored place number?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "2 Should the paper-list import get a preview and a way to undo a run?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "3 Should someone be told when the nightly offer job fails to run?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "4 A plot given out of turn should take the person off the queue": Opening (a) no, (b) yes, (c) no; Order in order; Plain yes.
  - first finding of the last group, "10 The reason a warden types for a grant shows on the history page (bullet)": Opening (a) no, (b) yes, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-specreview-1` (4 findings read)
  - most serious group, "C1 an offer email that never arrives sends the person to the back": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 the paper list has no way onto the online queue": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - first finding of the second group, "Visibility: "see their place" has no criterion after the first email": Opening (a) no, (b) no, (c) no; Order missing, worst case and the Fix line; Plain yes.
  - first finding of the last group, "Wording: "lapse" reads as jargon": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `treated-specreview-2` (4 findings read)
  - most serious group, "C1 an undelivered offer silently sends a person to the back (failure cases, ease of use)": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the proposed-edit cell stands for the Fix line); who raised it sits inside the finding cell, ahead of the edit; Plain yes.
  - most serious group, "C2 people on the paper list have no way into the new queue (right problem, plus my own reading)": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the proposed-edit cell stands for the Fix line); who raised it sits inside the finding cell, ahead of the edit; Plain yes.
  - first finding of the second group, "Seeing your place: nothing after the first email lets them": Opening (a) no, (b) no, (c) no; Order missing, worst case and the Fix line; Plain yes.
  - first finding of the last group, "what time of day the 14 days end": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `treated-specreview-3` (4 findings read)
  - most serious group, "C1 the paper list has no path online": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 an offer that never arrives leaves the plot empty then sends the person to the back": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - first finding of the second group, "Seeing your place: no criterion delivers it after the first email": Opening (a) no, (b) no, (c) no; Order missing, worst case and the Fix line; Plain yes.
  - first finding of the last group, "Wording and visibility: the time of day the 14 days end": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `treated-specreview-4` (4 findings read)
  - most serious group, "C1 no way to move the paper list online in order": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 an undelivered offer costs the person their place": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - first finding of the second group, "After joining, a person has no way to see their current place": Opening (a) no, (b) no, (c) no; Order missing, worst case and the Fix line; Plain yes.
  - first finding of the last group, "The list has 412 names, not about 340": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `treated-specreview-5` (4 findings read)
  - most serious group, "C1 the paper list has no route online": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 an offer that never arrives silently sends the person to the back": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - first finding of the second group, "Buildability: nothing marks a plot as freed (AC2 has no trigger)": Opening (a) no, (b) no, (c) no; Order out of order, who raised it leads the line (a reviewer label first), and the worst case and Fix line are missing; Plain yes.
  - first finding of the last group, "What time of day an offer ends": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `treated-specreview-6` (5 findings read)
  - most serious group, "C1 nothing moves the 412 people on the paper list online": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C2 a bounced offer holds the plot 14 days then sends an unwarned person to the back": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - most serious group, "C3 the Objectives promise people can see their place, no criterion shows it": Opening (a) partly, (b) partly, (c) partly; Order missing, worst case (the finding cell is the problem statement and the proposed-edit cell stands for the Fix line) and who raised it; Plain yes.
  - first finding of the second group, "Nothing says who marks a plot as freed": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain yes.
  - first finding of the last group, "The time of day the 14 days end isn't stated": Opening (a) no, (b) no, (c) no; Order missing, worst case, Fix line and who raised it; Plain no.
- `treated-driver-1` (5 findings read)
  - most serious group, "Decide how the queue keeps its order": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "Decide whether the paper-list import needs a preview and an undo": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "Decide whether a missed nightly offer run should alert someone": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "A plot granted out of turn should take the person off the queue": Opening (a) no, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "Tasks 2 and 5 both format dates; share one helper (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain no.
- `treated-driver-2` (5 findings read)
  - most serious group, "1 Should a queue position be a stored number, or worked out from join time?": Opening (a) yes, (b) partly, (c) yes; Order in order; Plain yes.
  - most serious group, "2 Should the one-off import of 340 people get a preview and a way to undo it?": Opening (a) yes, (b) no, (c) yes; Order in order; Plain yes.
  - most serious group, "3 Should the nightly offer job report whether it ran?": Opening (a) yes, (b) no, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "4 The offer link accepts a plot just by being opened; I'd make it lead to a confirmation page": Opening (a) no, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "Calling an expired offer a "lapse" reads as jargon; use "offer expired" (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain yes.
- `treated-driver-3` (5 findings read)
  - most serious group, "1 How the queue keeps order: should it be ordered by join time?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "2 A safe way to run the paper-list import: preview and an undo?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "3 Knowing when the nightly offer job didn't run: should a failed run alert someone?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "A warden's grant leaves the person on the queue; I recommend having the grant take them off": Opening (a) no, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the last group, "Tasks 2 and 5 each format dates separately; share one helper (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain no.
- `treated-driver-4` (6 findings read)
  - most serious group, "1 Should queue position be a stored number?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "2 Does the import need a preview and an undo?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "3 Should a missed nightly run alert someone?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "4 Should the history page be built from recorded events?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain no.
  - first finding of the second group, "Getting a plot out of turn doesn't take the person off the queue": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "An offer exactly 14 days old has no test; one should be added (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain yes.
- `treated-driver-5` (5 findings read)
  - most serious group, "1 Should the queue keep its order by join time instead of a stored place number?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "2 Should the paper-list import run as a batch you can preview and undo?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "3 Should the nightly offer run report whether it ran?": Opening (a) yes, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "4 A warden's out-of-turn grant leaves the person in the queue": Opening (a) no, (b) no, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "10 A warden's reason for an out-of-turn grant may contain personal details (bullet)": Opening (a) no, (b) no, (c) partly; Order missing, worst case and the Fix line; Plain yes.
- `treated-driver-6` (5 findings read)
  - most serious group, "One freed plot can be offered to two people; I recommend replacing the stored place number with join-time ordering": Opening (a) partly, (b) yes, (c) yes; Order in order; Plain yes.
  - most serious group, "The paper-list import can't be previewed or undone; I recommend a batch id with a preview": Opening (a) partly, (b) yes, (c) partly; Order in order; Plain yes.
  - most serious group, "If the nightly offer job stops running, nobody finds out; I recommend having it report to the warden": Opening (a) partly, (b) yes, (c) yes; Order in order; Plain yes.
  - first finding of the second group, "A warden's out-of-turn grant leaves the person on the queue; I recommend having the grant close their queue row": Opening (a) no, (b) yes, (c) partly; Order in order; Plain yes.
  - first finding of the last group, "Tasks 2 and 5 each format dates; one shared helper would do (bullet)": Opening (a) no, (b) yes, (c) no; Order missing, worst case and the Fix line; Plain no.

### Summary, per site and arm

"Most serious group" counts only the findings the reading names under that group. "All read" adds the first finding
of each other group. "All three yes" is (a), (b) and (c) all `yes` on the same finding.

| Site | Arm | Reading | Captures | Findings read | (a) yes | (b) yes | (c) yes | all three yes | Order in order |
|---|---|---|---|---|---|---|---|---|---|
| consult | baseline | most serious group | 3 | 3 | 0 | 0 | 0 | 0 | 1 |
| consult | baseline | all read | 3 | 9 | 0 | 3 | 0 | 0 | 2 |
| consult | treated | most serious group | 6 | 6 | 3 | 5 | 6 | 3 | 6 |
| consult | treated | all read | 6 | 18 | 8 | 17 | 11 | 7 | 12 |
| plan | baseline | most serious group | 3 | 9 | 0 | 0 | 0 | 0 | 0 |
| plan | baseline | all read | 3 | 15 | 0 | 1 | 0 | 0 | 0 |
| plan | treated | most serious group | 3 | 9 | 9 | 9 | 9 | 9 | 9 |
| plan | treated | all read | 3 | 15 | 10 | 15 | 11 | 10 | 12 |
| specreview | baseline | most serious group | 3 | 6 | 0 | 0 | 0 | 0 | 0 |
| specreview | baseline | all read | 3 | 12 | 0 | 0 | 0 | 0 | 0 |
| specreview | treated | most serious group | 6 | 13 | 0 | 0 | 0 | 0 | 0 |
| specreview | treated | all read | 6 | 25 | 0 | 0 | 0 | 0 | 0 |
| driver | baseline | most serious group | 3 | 9 | 0 | 0 | 0 | 0 | 9 |
| driver | baseline | all read | 3 | 15 | 0 | 1 | 0 | 0 | 12 |
| driver | treated | most serious group | 6 | 19 | 16 | 16 | 18 | 13 | 19 |
| driver | treated | all read | 6 | 31 | 16 | 25 | 21 | 13 | 25 |

**What the counts say.**

- consult: no baseline finding opens with a decision and a recommendation (0 of 9 on (a), 3 of 9 on (b), all of them
  minor-group bullets whose text is itself the instruction). Treated, the most serious finding carries all three in 3
  of 6 runs, and the recommendation in 5 of 6. Runs 1, 2 and 6 state the condition without an explicit ask on the first
  finding (run 6 also without a single recommendation). The order holds on 6 of 6 most serious findings, against 1 of 3
  at baseline, where two runs put who raised it ahead of the worst case.
- plan: baseline 0 of 9 most serious findings carry all three, and 0 of 9 are in order, since every baseline heading
  puts who raised it ahead of the worst case. Treated, 9 of 9 carry all three and 9 of 9 are in order.
- specreview: **no change.** Baseline and treated both present the most serious findings as table rows: no row opens
  with a decision, recommendation and stake as a sentence (0 of 6 and 0 of 13), no row carries a worst case, and the
  order cannot hold (0 of 6 and 0 of 13 in order). See below.
- driver: baseline 0 of 9 carry all three, 9 of 9 are in order (raised-by is last in the baseline's driver captures,
  so the order held with no edit). Treated, 13 of 19 most serious findings carry all three and 19 of 19 are in order.
  The 6 that do not are run 2 (decision asked, no recommendation or only "I'd raise this", 3 findings) and run 6 (an
  "I recommend" with no explicit ask, 3 findings), run 6's import finding also naming only the problem as the stake.
  Run 4 has a fourth most serious finding that no reviewer raised, which also carries all three.

**The specreview shape, as seen.** In all six treated captures, and in all three baseline captures, the most serious
findings are rows of a four-column table (id, finding, proposed disposition, proposed edit): two rows in five treated
captures, three in run 6. There is no per-finding worst case and no `Fix:` line in any of the nine; the proposed-edit
cell is the nearest thing to a fix. Who raised a finding is in a finding cell in treated run 2 only (and baseline run
2's second row); in the others it is in the narrative above the table. Only treated run 2 puts the group heading
"Would build the wrong thing, needs your decision" above the table; the others show the table with no group heading and
use the two lower group headings for the bulleted lists beneath it. Those lower groups are one-line bullets, mostly
topic fragments with no worst case and no `Fix:` line (and so `no` on Plain where they rely on the spec). The
recommendation sits in the narrative and the `Recommended outcome` line above the table. The treated shape is the
baseline's shape with plain group names and plain lens labels; the finding shape the criteria describe is not what
either arm shows at this site. This verifies the earlier observation exactly as made.

**Other things seen by eye.**

- The gap between baseline and treated at consult, plan and driver is mostly the opening: treated openings are
  questions or "Decide whether" sentences that carry "I recommend" and the stake, where baseline openings are a
  problem statement with who raised it in the heading line or at the end.
- The treated lower groups are weaker than the top group. The first finding of the last group is a one-line bullet in
  every site, with no worst case, so (a) is `no` throughout and the order is `missing` throughout. The bullets that
  name tasks by number (plan and driver) and the spec-detail fragments (specreview) read `no` on Plain.
- Two treated driver captures (runs 2 and 4) add a finding no reviewer raised, about the history page; both say so in the
  raised-by line.
- The log can be checked by sampling a capture's consolidated list against its lines above.

### One real `/craft:consult` run, read by eye

**What ran.** One real session, from a fresh empty `/tmp` directory, with the branch's plugin only: `claude -p
"/craft:consult <question>" --plugin-dir <worktree>/tools/craft/plugins/craft --setting-sources project --allowedTools
...`. Nothing was installed and nothing under `~/.claude`, `~/.config` or `~/.local/state/trailhead` was touched. The
question was set in a domain unlike the fixtures: a two-person bakery deciding whether to replace a hand-edited
shared order spreadsheet with a hosted order form (about 60 loaves a week, regulars who order by text, bakers who are
not technical).

**Which plugin loaded, and how it is known.** The run was made with `--output-format stream-json --verbose`, so the
session's own `system init` record is in the output. Its `plugins` list holds exactly one craft entry, with source
`craft@inline` and path `<worktree>/tools/craft/plugins/craft` (the `--plugin-dir` directory), plus three built-in
plugins and nothing else; `agents` and `slash_commands` list the `craft:*` agents and skills, including `craft:consult`
and the four lens agents, and the session's first tool call read `<worktree>/.../skills/_shared/council.md`. No
installed craft was present.

**Two attempts, one retry.** The first attempt, exactly as the dispatch gave the command (plus the stream flags), from
a truly empty directory with tools `Read,Grep,Glob,Agent`, did not convene the council. The consult skill runs
`scripts/maturity_bars.py --agent-instruction-file <dir>/CLAUDE.md` before dispatching, the `Bash` call was refused by
the tool grant, and the script refuses a missing `CLAUDE.md` rather than defaulting (reason code
`agent-instruction-file-unreadable`). The session said so and stopped, giving a short view of its own instead. That is
a reason fixable in the command, so the one permitted retry was used: a fresh directory containing only a
`CLAUDE.md` with a `## Project Maturity` section (`prototype`) and the grant widened to `Bash(:*)` and
`Bash(<plugin>/scripts/maturity_bars.py:*)`. That run dispatched all four lens agents (`craft:builder`,
`craft:breaker`, `craft:attacker`, `craft:advocate`) and synthesized. **A consult from an empty directory with no
`CLAUDE.md` and no `Bash` grant does not run the council at all**; that is the shipped skill's own preflight, not a
defect of this change, and is noted for the operator.

**Reading by eye (final presentation to the reader).** Grouped in plain names: "Would build the wrong thing, needs your
decision" (2 findings), "Would cause trouble later, worth fixing, your call" (5), "Rough edges, nothing to decide" (4
bullets). Reviewers appear only as "buildability", "failure cases", "security", "ease of use" (and "All four"). The
words `Critical`, `Important`, `Minor`, `Builder`, `Breaker`, `Attacker`, `Advocate`, `Reliability`, `Premise` do not
appear in capitalised form. **Internal severity leak, found by eye:** the lowercase word `minor` names a severity level
three times, all in the rough-edges list and the narrative: "Rated minor because the project is a prototype",
"prototype maturity sets it at minor" and "a one-off job, minor at prototype level". The session also names the
maturity level `prototype` in the opening note and these lines. The reviewers' raw reports (not shown to the reader) used
`[Critical]`, `[Important]` and `[Minor]` tags, as the template has them.

**Graded with the grader.** `wording_grader.py --check severity-name` and `--check reviewer-name`, run over the final
presentation text only, with `--reports` pointing at the four lens reports taken from the session's stream (a live run
has no canned reports file). Both read `plain`. `persisted-levels` was not run: a consult saves no block. The grader
did not flag the three lowercase `minor` uses, because its severity patterns are case-sensitive by design (the
grammar's whole-word rule in "Grammar"); the by-eye reading above finds them. The grammar and its amendment stand, so
this is reported here and not repaired.

**Finding order of the live run (same protocol).** Most serious group (2 findings): "The form will take more orders
than they can bake. I recommend a hard limit and a Friday close before the form goes live." Opening (a) partly, (b)
yes, (c) yes; Order in order (worst case, fix, raised by); Plain yes. "Text orders and form orders end up in two
places. I recommend keeping texts and having a baker enter each one through the form." (a) partly, (b) yes, (c)
partly; in order; Plain yes. First of the second group, "Hand edits in the form's own tab break things. I recommend a
separate working tab.": (a) partly, (b) yes, (c) partly; in order; Plain yes. First of the last group, "Undoing the
switch: costs nothing, because the sheet stays the master copy. Rated minor because the project is a prototype.":
(a) no, (b) no, (c) no; Order missing (worst case and fix); Plain yes. This is unmeasured like the rest.

**Excerpts.** Top of the supporting detail: `### Would build the wrong thing, needs your decision` / `**1. The form
will take more orders than they can bake.** I recommend a hard limit and a Friday close before the form goes live.` /
`- **Worst case:** a customer gets an automatic "order received" for loaf 61. The bakers find out on Friday night and
have to text an apology.` / `- *Raised by: failure cases, ease of use. Buildability and security raised the same gap
as less serious.*` The leaks: `- **Undoing the switch:** costs nothing, because the sheet stays the master copy.
Rated minor because the project is a prototype. *(All four.)*`

**Not committed.** The raw stream holds host paths and the session's working directory, so only the excerpts above are
recorded. No file was added under `runs/`.

### Limits seen

- One real run, one question, one model tier; it is a read-by-eye observation, not a measured rate.
- The live run needed a `CLAUDE.md` and a narrow `Bash` grant that the dispatch's command did not give; the finding
  above is about the skill's own preflight.
- The finding-order reading is by an agent and is unmeasured by the protocol's own terms.

## Review notes (2026-10-15)

Appended after the whole-change review. Nothing above this heading is rewritten, and the grammar and its amendment
stand as written.

**(a) The lowercase `minor` leak and what the treated results do not cover.** The live consult's lowercase "minor"
comes from two instructions in `council.md`: the maturity-calibration paragraph (lines 202-205), which tells the
reader-facing text to restate a downgraded finding as "migration and backfill — Minor, downgraded by…", and the
stand-down restatement instruction (lines 237-240), whose rendered lines end "would rate Critical"
(`scripts/maturity_bars.py:490-491`). No fixture staged prototype maturity or a stand-down: every reports fixture is
production with "No stand-downs". So the treated results do not exercise that path, and AC22 is not closable on them
alone. A follow-up measurement on this branch covers it.

**(b) The specreview "no change" finding-order reading.** The spec's settled point is that the spec review's opening
summary keeps one row per finding that needs a decision, and that AC17 to AC21 govern a finding printed in full, on
request or saved. The staged step is the opening summary, so a table row with no opening sentence is what that step
is settled to produce and is not a miss against those criteria. The in-scope surface for them, the full detail on
request (`gauntlet/SKILL.md:317-320`), was never captured, so the staged step is not the surface those criteria
govern there. "No change" is a reading of the summary rows only.

**(c) Limits.** `persisted-levels` reads `*Critical:* (none)` and `*Critical:* — none` as `kept`. That is faithful to
the frozen grammar, and it is the failure shape the guard exists for: a Critical entry emptied while the label stays.
No treated block had it. Separately, the grader now reads an indented heading line (`  ## Important — later`) the
way the amendment's S4 and S6 say, removing the leading `#`s from the line before testing; before this it read such a
line as `none`. All 33 committed captures (12 baseline, 21 treated) were graded again on all three checks (99 verdicts),
and no verdict changed.

## Extension, 2026-10-15: the consult-calibrated site and the `calibration-level` check

Pre-registered before any run of the two arms it adds. Nothing above this heading is rewritten; the grammar, its
amendment, and every earlier result stand as written. The extension adds one site, one check and two arms. Where it
names a rule the case already states (the reading rules, the verbatim-quotation exemption, crashed runs, the scans
before commit, the splits rule, the stop rule), that rule applies unchanged.

**Why.** The review notes above found two older paragraphs in `council.md` (the maturity-calibration paragraph and
the stand-down restatement instruction) and the matching instruction in `consult/SKILL.md` that still show the
reader internal levels: a downgraded finding restated as "migration and backfill — Minor, downgraded by this
spec's prototype maturity level", and a stand-down line that ends "would rate Minor". Every fixture so far was at
production maturity with no stand-down, so that path was never staged, and the real consult run showed the leak
("Rated minor because the project is a prototype", three times).

### The new site

| Site | What the session is doing | Canned input | Graded |
|---|---|---|---|
| consult-calibrated | The consult ritual's synthesis step, on a question about a one-term trial sign-up page for a swimming club, at prototype maturity with one waived concern. | four lens reports and the rendered calibration block (`reports-calibrated.md`) | `calibration-level`, `severity-name`, `reviewer-name` |

### New fixtures

Written by hand under `fixtures/`, in a domain (a swimming club's Saturday lane sign-ups) unlike every earlier
fixture and unlike the examples `council.md` carries. The fixture prose avoids the words critical, important,
minor, builder, breaker, attacker, advocate, reliability, premise, consistency and divergence, except inside the
reports' severity tags, lens labels and the renderer block, which keep the council template's and the renderer's
form.

- `consult-calibrated-question.md`: the question, with the trial's own notes (carrying the paper sheets over is
  not part of this term's work).
- `reports-calibrated.md`: the rendered calibration block in a fenced block, exactly as
  `scripts/maturity_bars.py` prints it for a spec stamped `prototype` with a `Waives: backwards compatibility`
  Non-Goal (`maturity: prototype (basis: stamp)`, one `stand-down:` line ending `would rate Minor`, the four
  rated concerns at Minor), then four lens reports (Builder, Reliability, Security, Advocate) in the template's
  format. Two lens findings are calibrated downgrades that name the deciding level (migration and backfill, and
  rollback and reversibility, each `[Minor]` and each stating "downgraded by this spec's prototype maturity
  level"). The ordinary findings are two concrete Criticals (a double booking of the last place in a lane; a
  lost booking during the Friday tidy-up), one speculative Critical (the Advocate's, to be downgraded), and
  Important and Minor findings in each lens that reports any.
- `prompt-consult-calibrated.md`: the prompt. `FIXTURES_DIR` stands for the directory holding a copy of
  `fixtures/`, replaced by the runner as in "Dispatch".

### The new arms

| Arm file | Site | Sources, in order | Pin |
|---|---|---|---|
| `precal-consult.md` | consult-calibrated | `rules.md`, `skills/consult/SKILL.md`, `skills/_shared/council.md` | every source at the baseline revision `f68200933f69e0c00bc79634fb4758b8fa039c1c`, except `consult/SKILL.md` and `council.md` at `e88e01303e744f94b60c46e10212d2adf0ca7ed0` (the committed state before the calibration edit; byte-identical to `af083a20`) |
| `cal-consult.md` | consult-calibrated | the same three | the same, with `consult/SKILL.md` and `council.md` at the commit that carries the calibration edit (its SHA is named in the results) |

The two compositions differ in those two files only. The manifest in
`tools/craft/tests/test_finding_presentation_wording_arms_contract.py` names both. `cal-consult.md` is built
and run only after the edit is committed on its own.

### Capture naming

`precal-consult-<n>` and `cal-consult-<n>`, `n` counting dispatches from 1, never reused; three files per run as in
"Capture naming". Each is graded against `fixtures/reports-calibrated.md`.

### Verdict words

- `calibration-level`: `plain`, `internal`, `none`.

### Grammar of `calibration-level`

Every pattern the check uses is listed here.

**Reading.** The capture is read as in "Reading the capture" and "Reader-facing text" above: logical lines, fenced
blocks dropped, a line that quotes a reviewer report verbatim dropped (the quotation rule is applied against
`fixtures/reports-calibrated.md`, read as the amendment says). Matching is over the normalised text of each
reader-facing logical line, case-insensitive. A **whole word** is as the case defines it: not adjacent to a
letter, a digit or a hyphen on either side. A table cell is a logical line of its own, so a severity word in one
cell and a calibration word in another do not share a line.

| Id | Pattern (whole word, any case) |
|---|---|
| K1 | the severity words `critical`, `criticals`, `important`, `minor` |
| K2 | the calibration words `prototype`, `early`, `production`, `maturity`, `level`, `waive`, `waived`, `waiver`, `stand-down`, `downgrade`, `downgraded`, `rate`, `rated` |
| K3 | the resolved maturity level: `prototype` |

**Verdict.**

- `internal` when any one reader-facing logical line matches K1 **and** K2. The two must share the line.
- Otherwise `plain` when K3 matches in any reader-facing logical line.
- Otherwise `none`.

Both halves of `internal` are needed: a severity word on a line with no calibration word is not counted here (it
is `severity-name`'s concern), and a calibration word with no severity word is not counted. `plain` is the reading
the guide asks for: the level is named and no severity name sits beside a calibration word.

**Trade-offs, written down.** An ordinary use such as "an important decision for production" reads `internal`,
and so does "a critical look at the rate of change": the grammar is lexical and errs against a pass, the safe
direction for this instrument. The `-ed` and `-s` forms are listed where they matter (`rated`, `waived`,
`downgraded`, `criticals`); any other inflection (`rating`, `levels`, `importance`) is not a whole word match and
is not counted. A capture that never names `prototype` reads `none` even when it is clean, so a treated capture
that restates the level only as "early" or "production" reads `none`, which is not `plain`.

### Pass condition, fixed before any run

- **Headroom, `calibration-level`:** the `precal-consult` arm is majority `internal`. If it is not, the site has no
  headroom: the claim closes on the baseline verdict, the prose edit is not made, and the result is reported.
- **Treated:** the `cal-consult` arm reaches a clean majority `plain` on `calibration-level` (3 of 3, or 5 or 6 of 6
  after a split), and a clean majority `plain` on `severity-name` and `reviewer-name` too, so the finding-contract
  result does not regress.
- A treated failure: the prose-edit commit is reverted on the branch with a revert commit (no history rewriting),
  the result is recorded, and the run stops and is reported.

`none` counts as neither `internal` nor `plain`, as before. The splits rule, the floor and exclusions, and the
stop rule apply as written above, with the checks and arms of this extension.

### Amendment allowance

One dated amendment to the `calibration-level` grammar is allowed, after the `precal-consult` hand check (one
capture per verdict word that occurs, read by hand against the grader's verdict) and before any `cal-consult` run.
It is appended below under a dated heading, states what the hand check showed, and changes only patterns in the
grammar above. After the first `cal-consult` output exists no amendment is allowed; a flaw found later is
reported in the results, not repaired.

### Not measured

- The finding-order criteria (AC17, AC18) at this site: not read here.
- The plan's and the spec review's own restatement instructions, which a logged follow-up owns.
- The driver's persisted restatement and the renderer's block, which keep the internal forms by design.

### End states, fixed before any run

- `precal-consult` majority `internal`: the headroom holds and the edit is made.
- `precal-consult` not majority `internal`: no headroom, the claim closes on the baseline verdict.
- `cal-consult` meets the treated condition: the edit is kept.
- `cal-consult` fails the treated condition: the edit is reverted by a revert commit and the failure is reported.

## Results: the consult-calibrated site (2026-10-15)

Written after the `calibration-level` check, the fixtures and both arms were committed and both arms were run and
graded, in this order: the extension, fixtures, grader check and `precal-consult` arm (commit `835c29f9`, before any
run); three `precal-consult` runs, their captures and their hand check (commit `4f295b18`); the prose edit alone in commit
`df65828b`, the `cal` pin; the `cal-consult` arm at that pin (commit
`999e9ade`); three `cal-consult` runs. The two arm compositions differ only in `consult/SKILL.md` and `council.md`; `rules.md` is the same bytes. All
six runs exited 0, every capture is non-empty, every stderr is empty, so none was excluded or replaced. No split, so
no re-run. The grammar was used as pre-registered: no amendment was made. Each capture is graded against
`fixtures/reports-calibrated.md`. The capture-to-verdict table (`FINDING_CAPTURE_VERDICTS` in
`tools/craft/tests/test_wording_grader.py`) carries a row for all six, and the test that reads it failed on each
unmapped capture before its row was added.

| Arm | Check | Run 1 | Run 2 | Run 3 | Tally | Condition |
|---|---|---|---|---|---|---|
| `precal-consult` | `calibration-level` | internal | internal | internal | 3 of 3 internal | headroom holds |
| `precal-consult` | `severity-name` | plain | plain | plain | 3 of 3 plain | observed |
| `precal-consult` | `reviewer-name` | plain | plain | plain | 3 of 3 plain | observed |
| `cal-consult` | `calibration-level` | plain | plain | plain | 3 of 3 plain | pass |
| `cal-consult` | `severity-name` | plain | plain | plain | 3 of 3 plain | pass |
| `cal-consult` | `reviewer-name` | plain | plain | plain | 3 of 3 plain | pass |

The site passes: the headroom precondition holds (`precal` majority `internal` on `calibration-level`) and `cal`
reaches a clean majority `plain` on all three checks. The prose edit is kept; there is no revert.

### Hand check (before any `cal-consult` run)

Only `internal` occurs on `calibration-level` in the `precal` captures, so one capture per verdict word is one
capture. `precal-consult-1` was read whole. It restates the stand-down line verbatim ("... would rate Minor") and
writes "Migration and backfill — Minor, downgraded by this trial's prototype maturity level" and the same for
rollback and reversibility; the verdict is `internal` by eye as well. `precal-consult-2` and `-3` carry the same two
shapes ("Migration and backfill: Minor, downgraded by this spec's prototype maturity level", "would rate Minor").
`plain` and `none` do not occur in the `precal` arm on this check; those words were checked on constructed inputs in
the tests. The hand check found no misfire, so the allowed amendment was not spent.

One thing seen at the hand check and not repaired: `severity-name` reads `plain` on all three `precal` captures,
although each uses "Minor" as a rating. Its patterns match a level word that leads a line, is followed by a colon,
bracket or count, or sits beside a finding word; a level word in mid-line ("Migration and backfill — Minor,
downgraded") matches none of them. That gap is why `calibration-level` exists, and it is a limit of `severity-name`,
not something this extension amends.

### By-eye reading of the treated captures

All three `cal-consult` captures were read for the lines the grammar cannot see. Each restates the maturity line
(`prototype (basis: stamp)`), restates the stand-down with the Non-Goal quoted and the plain reading in place of the
level ("without it this would be rough edges, nothing to decide"; "Without that waiver this would be rough edges,
nothing to decide"), and puts the two calibrated downgrades in the "Rough edges, nothing to decide"
group with the reading and the deciding level in words ("because this trial is at prototype maturity"). None uses a
level word as a label or a rating. The one use of the word "rated" (`cal-consult-3`: "the reviewers rated migration,
rollback, failure visibility and blast radius as rough edges") has no level word beside it.

### Residual leak rate and limits

- `cal-consult`: 0 of 3 runs used an internal level word beside a calibration word, a severity name or a reviewer
  name. The spec says "never"; three runs bound the rate loosely, not at zero.
- One site, one question, one model tier, one fixture at prototype maturity with one waived concern at one level. A
  waiver at another level, a per-repository block and `waiver-not-recognised:` were not staged.
- The grammar is lexical. A treated capture that never names `prototype` reads `none`; none did here.
- Plan's and the spec review's own restatement instructions are untouched and not measured here; a logged follow-up
  owns them.
- The finding-order criteria (AC17, AC18) were not read for this site.

## Extension, 2026-10-15: the code review sites, the council-site regression and the `code-review-name` check

Pre-registered before any run of the arms it adds. Nothing above this heading is rewritten; the grammar, its
amendment, the earlier extension and every earlier result stand as written. The extension adds three sites, one
check, three baseline arms now and further arms by reserved name, and a regression at the four council sites. Where
it names a rule the case already states (the reading rules, the reader-facing text, the verbatim-quotation
exemption, the severity grammar, three runs per arm and the splits rule, crashed runs, the scans before commit, the
dispatch command), that rule applies unchanged, with the sites, arms and checks of this extension.

**Why.** The shared finding contract (`skills/_shared/council.md`, `### How a finding reads`) is read today by the
council sites only. The review ritual and a build's end-of-run review (`_shared/execute.md` Phase 3) both show the
reader a code review finding, and neither reads that contract, so a finding there reads as the reviewer's
`file:line — one-line ask` bullet under an internal level name. A later task edits the contract and the three skill
files that route to it so those findings take the contract's shape. This extension freezes how that edit is
measured, and how the four sites that already read the contract are checked for a regression.

### The new sites

Every site stages "the reviewer has returned; present the result" from one hand-written canned report over one small
fixture change, so no subagent runs. The session is told to run no commands and write no files.

| Site | What the session is doing | Canned input | Graded |
|---|---|---|---|
| review | The review ritual's step 5, "Act on feedback": the reviewer dispatched at step 3 has returned, and the session tells the user what it found and what it will do. | the change, `reports-code-review.md` | `code-review-name`, `severity-name` |
| buildreview | The build procedure's Phase 3, after the one re-review round: the findings that survived are surfaced to the user. | the same | `code-review-name`, `severity-name` |
| drivebuild | The driver resumed at its build phase (a new session; the plan was approved earlier), running the build procedure inline, at Phase 3 after the one re-review round: the surviving findings are surfaced to the user. | the same | `code-review-name`, `severity-name` |

`reviewer-name` is not run at these sites: its vocabulary is the council lenses, and a code review has no lens
label. `persisted-levels` is not run: no site stages the writing of a saved block, and the persisted `## End Phases`
notes and `## Measurement tally` are not staged (see "Not measured"). `severity-name` runs unchanged. Every arm at a
site gets the same fixtures and the same prompt; only the system prompt differs.

### New fixtures

Written by hand under `fixtures/`. None pulls text from a vault record, and none contains a credential-shaped value.
They are set in a village beekeeping society's apiary inspection reminders, a domain unlike the allotment plot
waiting list this case already uses, unlike the lending library of `ritual-output-wording`, and unlike the examples
`council.md` and the skill files carry. The fixture prose avoids the words critical, important, minor, builder,
breaker, attacker, advocate, reliability, premise, consistency and divergence, except the three severity heading
lines of the canned report, which keep the code reviewer's output form.

- `hive-reminders/change.md`: the intent document of the change (what the committee asked for, and what was built).
- `hive-reminders/reminders.py` and `hive-reminders/test_reminders.py`: the change, two short files. The code has
  four real shortcomings the report cites.
- `reports-code-review.md`: one canned report in the exact shape `agents/code-reviewer.md` fixes: `Verdict:
  FIX_FIRST`, then bare `Critical`, `Important` and `Minor` heading lines, each finding a `file:line — one-line ask`
  bullet. One finding under Critical (`reminders.py:13`, a hive with no inspection yet raises inside `next_due`), one
  under Important (`reminders.py:13`, `<` leaves a hive due today off the list), two under Minor
  (`test_reminders.py:8`, thin test coverage; `reminders.py:21`, the board line carries no date). Every cited
  line exists in the fixture files above.
- `prompt-review.md`, `prompt-buildreview.md`, `prompt-drivebuild.md`: one prompt per site, each naming the four
  files above. `FIXTURES_DIR` stands for the directory holding a copy of `fixtures/`, replaced by the runner as in
  "Dispatch". No prompt or fixture mentions the finding contract, plain readings or labels.

**Staging choice (the reviewer's name).** The prompts say "the reviewer you dispatched" or "the reviewer was
dispatched" and never write `code-reviewer`, `code reviewer` or "code review" as a name for it, so the prompt seeds
neither form and the baseline's headroom on `code-review-name` is a property of the arm text. The real procedures
dispatch `code-reviewer` and every arm's skill text says so, so the session knows the agent's name in every arm; a
staging that wrote it would be faithful but would seed the internal form in the treated arm as well as the baseline.
The report file's name (`reports-code-review.md`) is a file path the session reads, not prose it is asked to echo.

The report holds no `[Critical]`-style tagged line, so the verbatim-quotation exemption excludes nothing from these
captures. The grader is still given `--reports fixtures/reports-code-review.md` at every site, as for the other
report checks.

### The arms

All paths are under `tools/craft/plugins/craft/`. The three baseline arms exist when this extension is committed; the
rest are reserved names, created by the task that needs them.

The baseline arms pin every source to `7b5c65f9a00157e0aee884bdfb0f3fe0b99cdc29` (`CODE_REVIEW_BASELINE_REVISION`
in the arms contract test), origin/main when this extension was cut. `rules.md` at that revision is byte-identical
to `rules.md` at the case's earlier baseline `f6820093`, so "`rules.md` held constant" still holds across the case.

| Arm file | Site | Sources, in order | Added by |
|---|---|---|---|
| `baseline-review.md` | review | `rules.md`, `skills/review/SKILL.md` | this task |
| `baseline-buildreview.md` | buildreview | `rules.md`, `skills/execute/SKILL.md`, `skills/_shared/execute.md` | this task |
| `baseline-drivebuild.md` | drivebuild | `rules.md`, `skills/drive/SKILL.md`, `skills/_shared/execute.md` | this task |
| `treated-review.md` | review | `rules.md`, `skills/review/SKILL.md` (edited), `skills/_shared/council.md` (edited) | later task, name reserved |
| `treated-buildreview.md` | buildreview | `rules.md`, `skills/execute/SKILL.md` (edited), `skills/_shared/execute.md` (as baseline), `skills/_shared/council.md` (edited) | later task, name reserved |
| `treated-drivebuild.md` | drivebuild | `rules.md`, `skills/drive/SKILL.md` (edited), `skills/_shared/execute.md` (as baseline), `skills/_shared/council.md` (edited) | later task, name reserved |
| `fallback-<site>.md` | a new site whose treated arm fails | the treated arm's sources, with the one file the fallback edits read from the working tree | later task, name reserved |
| `regress-<site>.md` | each of the four council sites | see "Regression" | later task, name reserved |

No baseline arm carries `council.md`, because none of the three routes reads it at that point today; the treated
arm adds the read the edit causes, so the variable is "the ritual reads the contract", not the contract alone. In a
treated arm `council.md` is the last source, as in the earlier treated arms, and "edited" means read from the commit
that carries the edit (its SHA is named in the results). `rules.md` is held constant in every arm. The build arms
carry their skill file and `_shared/execute.md` and not the four other documents a real build reads, matching the
case's earlier build-route arms and the record-link case's `execute-rule-only.md`. The fallback differs from its
treated arm by one file: `skills/review/SKILL.md` at the review site, `skills/_shared/execute.md` at the build sites.
That one `execute.md` edit serves both build sites, so a treated failure at either build site makes the
`fallback-buildreview.md` and `fallback-drivebuild.md` pair, and both are measured.

### Regression at the four council sites

The edit changes text that the spec review, the plan's council review, consult and the driver's council step already
read, and the case's arms for them pin `council.md` at the previous edit. Four regression arms re-run those sites
against the edited contract. Each regression arm is the site's most recent arm composition with only
`skills/_shared/council.md` moved to the edit commit, the same commit the treated code review arms read.

| Arm file | Site | Prompt | Sources, in order |
|---|---|---|---|
| `regress-consult.md` | consult | `prompt-consult.md` | `rules.md` (baseline revision), `skills/consult/SKILL.md` (at `CAL_REVISION`), `skills/_shared/council.md` (edited) |
| `regress-plan.md` | plan | `prompt-plan.md` | `rules.md`, `skills/plan/SKILL.md`, `skills/_shared/council.md` (edited); the first two at the baseline revision |
| `regress-specreview.md` | specreview | `prompt-specreview.md` | `rules.md`, `skills/gauntlet/SKILL.md`, `skills/gauntlet/dispositions.md`, `skills/_shared/council.md` (edited); the first three at the baseline revision |
| `regress-driver.md` | driver | `prompt-driver.md` | `rules.md`, `skills/drive/SKILL.md`, `skills/_shared/council.md` (edited); the first two at the baseline revision |

**Which consult arm it derives from, and why.** At consult the case's most recent arm is `cal-consult.md` (the
calibration prose edit, `CAL_REVISION`), not `treated-consult.md`: `treated-consult.md` reads
`consult/SKILL.md` and `council.md` from `EDIT_REVISION`, which is before the calibration edit, and the shipped
consult on main reads the calibrated `consult/SKILL.md`. So `regress-consult.md` takes `consult/SKILL.md` as it
ships (`CAL_REVISION`, byte-identical to origin/main `7b5c65f9`) and moves only `council.md`. It is run with
`prompt-consult.md` and graded with consult's own checks, so the clean-majority bar it is held to is the one
`treated-consult` cleared.

**What the regression arm differs from the treated arm by.** At plan, specreview and driver the arm composition is
the treated arm's with `council.md` moved from `EDIT_REVISION` to the edit commit. The shipped `council.md`
(origin/main `7b5c65f9`) is the calibrated one, so that move includes the calibration prose edit as well as this
slice's edit; the regression measures the contract as it will ship, not this slice's edit in isolation. Every
non-`council.md` source of the plan, specreview and driver arms is byte-identical at `f6820093` and at `7b5c65f9`. The
driver's regression arm keeps `drive/SKILL.md` unedited, as the treated driver arm does: the new read directive sits
in the build phase (step 9), after the council step the arm exercises.

**Checks and bar.** Each regression arm is graded with the checks its site already runs: `severity-name` and
`reviewer-name` at consult and specreview, plus `persisted-levels` at plan and driver. Pass at a site: a clean
majority `plain` on both naming checks and, where `persisted-levels` runs, a clean majority `kept`, which is the
bar the site's treated arm cleared. There is no headroom precondition (the comparison is against the bar the site
already cleared, not against a baseline). A regression failure stops the slice for the operator: the contract
edit is reworked, and it is not routed to a ritual fallback.

### Capture naming

Prefixes as in "Capture naming" above: the arm's name without `.md`, then `-<n>` with `n` counting dispatches from
1, never reused, with three files per run.

| Prefix | Arm file | Prompt | Checks the grader runs | Expected under this pre-registration |
|---|---|---|---|---|
| `baseline-<site>-<n>` | `baseline-<site>.md` | the site's prompt | `code-review-name`, `severity-name` | majority `internal` on both (headroom precondition) |
| `treated-<site>-<n>` | `treated-<site>.md` | the site's prompt | `code-review-name`, `severity-name` | clean majority `plain` on both (reserved; run by a later task) |
| `fallback-<site>-<n>` | `fallback-<site>.md` | the site's prompt | `code-review-name`, `severity-name` | same as `treated-` (reserved; run only on a measured treated failure) |
| `regress-<site>-<n>` | `regress-<site>.md` | the council site's prompt | the council site's own checks | clean majority `plain` on both naming checks; clean majority `kept` where `persisted-levels` runs (reserved) |

`<site>` is `review`, `buildreview` or `drivebuild` for the first three rows, and `consult`, `plan`, `specreview` or
`driver` for `regress-`.

### Verdict words

- `code-review-name`: `plain`, `internal`, `none`.

### Grammar of `code-review-name`

Fixed here, before any run; every pattern the check uses is listed. The existing checks `severity-name`,
`reviewer-name` and `persisted-levels` are unchanged and are not restated.

**Reading.** The capture is read as in "Reading the capture" and "Reader-facing text" above: logical lines, every
fenced block removed, every logical line that quotes a reviewer report verbatim removed (against the report files
given by `--reports`, read as the amendment says; at these sites that rule excludes nothing). The check takes
`--reports` like the other report checks, and a missing `--reports` is the same could-not-grade condition. Matching
is over the normalised text of each remaining logical line, case-insensitive (so `Code Reviewer` and `code reviewer`
are the same form). A **whole phrase** is a match not adjacent to a letter, a digit or a hyphen on either side;
inside the phrase, a space matches exactly one space (the normalised text has collapsed runs of whitespace). A table
cell is a logical line of its own.

| Id | Class | Pattern (whole phrase, any case) |
|---|---|---|
| C1 | internal | `code-reviewer` or `code-reviewers` |
| C2 | internal | `code reviewer` or `code reviewers` |
| C3 | internal | `craft:code-reviewer` |
| C4 | plain | `code review` or `code-review` |

C3 is the namespaced agent name; a whole-phrase `code-reviewer` already matches inside `craft:code-reviewer` (a colon
is not a letter, a digit or a hyphen), so C3 and C1 agree and C3 is listed so the form is named. `code-reviewer-agent`
does not match (a hyphen-joined word is adjacent to a hyphen); `code reviewer's` matches C2 (an apostrophe is not
blocking). `code review` does not match inside
`code reviewer` (a letter follows it), so the plain pattern cannot make an internal form read plain.

**Verdict.**

- `internal` when any one of C1, C2 or C3 matches any reader-facing logical line, whether or not a plain form also
  appears. Internal wins.
- Otherwise `plain` when C4 matches any reader-facing logical line.
- Otherwise `none`.

`plain` is therefore: only "code review" forms name the reviewer, at least one is present, and no internal form
appears anywhere in reader-facing text. `none` is neither, for example a capture that never names who raised the
findings.

**Trade-offs, written down.** The check is lexical. "Before the code review" and "I will run a code review" read
`plain` on C4 as well as "the code review found" and "raised by: code review" do; the check cannot tell naming from
mentioning, so it errs toward a pass, the unsafe direction for this instrument, and the by-eye protocol below
reads who-raised-it directly. "I asked the code reviewer to look again" reads `internal`, and so does an ordinary
use of the agent's name in a sentence that is not attribution; that errs against a pass, the safe direction. A
capture that names the reviewer as "the reviewer" or "the review" reads `none`, which is neither pass nor headroom.
The verdict words `SHIP`, `FIX_FIRST` and `BLOCK` are not patterns of this check.

**Reading pinned for `severity-name` at these sites.** The existing grammar already counts the lowercase phrases
"critical issue", "critical issues", "critical finding" and "critical findings" as `internal` (S2). That reading
stands unchanged at the code review sites: a capture that says "no critical issues" reads `internal`. A test pins it
over inputs that differ only in that phrase.

### Pass conditions, fixed before any run

Per new site and check, restated here and not referenced:

- **Headroom, per check:** the `baseline-<site>` arm is majority `internal` on `code-review-name` and majority
  `internal` on `severity-name`. **Majority** is more than half of the graded runs, so 2 of 3 is a majority. A
  baseline with no majority, or a majority of `none`, has no measurable headroom on that check: stop, change
  nothing, and report to the operator.
- **Treated, per check:** the `treated-<site>` arm reaches a clean majority `plain` on `code-review-name` and on
  `severity-name` (3 of 3 at three runs; 5 of 6 or 6 of 6 after a split; 4 of 6 is `AMBIGUOUS`, not a pass).
- A site passes when its headroom precondition holds on both checks and its treated arm meets both treated
  conditions. Any residual leak rate that is not zero is stated in the results, since the spec says "never".
- **Regression:** as in "Regression at the four council sites".

`none` counts as neither `internal` nor `plain` in any tally, as before. A baseline hand check applies, as at the
case's first baseline: one capture per verdict word per site, read by hand against the grader's verdict, before any
treated run.

### End states, fixed before any run

- A site whose baseline is already majority `plain` on a check closes that check on the baseline's script verdict
  (met without the edit) and is not recorded as unmeasured.
- A site whose baseline is majority `plain` on both checks has its new read directive dropped rather than shipped
  unmeasured: no `treated-<site>` arm is built for it and the skill-file edit for that route is not made.
- A site whose treated arm fails either check goes to the fallback.
- A site whose fallback also fails holds the slice from merging; the choice goes to the operator.
- A regression failure at any of the four council sites stops the slice for the operator.

### Fallback, fixed before any run

If a new site's treated arm fails either check, and only then, that site gets one point-of-use line naming the
finding shape: in `skills/review/SKILL.md` step 5 at the review site, or in `skills/_shared/execute.md` Phase 3's
"surface to the user" sentence at the build sites. The `execute.md` line names the contract's section by name and
never the file (a shared document may not name a sibling shared file). It is measured as its own arm,
`fallback-<site>.md`, which differs from the treated arm by that one file. If it also fails, it is reverted, the run
stops, and the choice goes to the operator. The fallback is never used for a regression failure.

### Stop rule

Stop and report, without running anything further, when: a baseline has no headroom on a check; a treated arm and
its fallback both fail; a regression arm fails; an arm cannot reach the floor; or any run needs a fifth
replacement. A treated arm is never run before this extension, the baseline captures and the grader's
`code-review-name` check exist and are committed, in that order. A regression arm is never built before the edit
commit exists.

### Amendment allowance

One dated amendment to the `code-review-name` grammar is allowed, after the baseline hand check and before any
treated or fallback output at a code review site exists. It is appended below under a dated heading, states what the
hand check showed, and changes only the patterns of this check. No amendment to `severity-name` or any other check
is allowed by this extension. After the first treated or fallback output exists, a flaw found later is reported in
the results, not repaired.

### The finding-order reading for code review findings: by eye

No script decides these. This extends "The finding-order reading (AC17, AC18): by eye" to the three code review
sites. For every capture the grader reads (baseline now; treated and fallback later), a person reads, in this order:

1. The findings the session shows the reader, not any narrative paragraph above them.
2. Every finding the canned report holds: all four, under whatever grouping the session uses.
3. For each finding, six observations, each written down as `yes`, `no` or `partly`:
   - **Opening:** does the finding's first sentence carry (a) the decision asked of the reader, (b) the
     recommendation and (c) what is at stake? Record each of the three.
   - **Order:** after the opening sentence, do the worst case, a labelled `Fix:` line and who raised it ("code
     review") follow in that order? Record `in order`, `out of order` (naming the first element out of place), or
     `missing` (naming the element).
   - **Shown:** is the finding shown at all (none dropped, merged into another, or softened to a hint), and does it
     keep its `file:line`?
   - **Grounded:** does the worst case follow from the fixture code at the cited line, rather than being invented?
     Read the cited line.
   - **Readings:** is severity expressed only as the guide's three plain readings (`needs your decision`, `worth
     fixing`, `nothing to decide`), with no invented fourth reading? Record any invented reading verbatim.
   - **Plain:** does the opening read without the change in hand? Record `yes` or `no`.
4. Per capture, two more observations: at a build site (`buildreview`, `drivebuild`), **no council dispatch is
   attempted** (the session does not announce or stage a council review because the contract is now in its
   instructions); and at the review site, whether step 5's triage by internal level ("Fix Critical issues
   immediately", "Note Minor issues for later") is **echoed** to the reader as level names.

The reading is written next to the capture's name in the results record. It is not counted in any pass condition,
not graded by script, and recorded as unmeasured. AC17 and AC18 at the code review sites close only when the
operator names them at slice close. The Known Unknown that "would build the wrong thing" does not fit code already
built, and might make the presenter drop or rephrase a plain reading, is read here (Readings) and in the treated
`severity-name` verdicts.

### The blocking post-install close-out

After the edit is installed, one real `/craft:review` run on a branch that has findings and one on a clean branch
are read by eye, against the same observations (on the clean branch: no finding is invented, and the shown text
uses no internal level name and not `code-reviewer`; the verdict words `SHIP`, `FIX_FIRST` and `BLOCK` may appear).
A real run sends a real reviewer's output through the same presenter, which is the path the canned report
substitutes for. The close-out is **blocking**: the slice is not closed, and AC24's code review part is not named
met, until both reads are recorded in the results.

### Not measured

- The finding-order criteria (AC17, AC18) at the code review sites: captured, read by eye, recorded as unmeasured.
- The reviewer's report to its caller, the verdict words and the persisted `## End Phases` notes and `##
  Measurement tally`, which keep their internal forms by design. The boundary is stated by the edit and is not run.
- A real reviewer's output, a real subagent run, and the build's other phases; only the surfacing of surviving
  findings is staged.
- A code review finding shown by any route other than the review ritual, the build's Phase 3 or the driver's build
  phase.
- The consult-calibrated site (`cal-consult`) is not re-run against the edited contract; the four regression
  sites are the case's main-line sites.
- The fix-shape criteria (AC19 to AC21), which a later slice measures at these sites.

### Limits

- Arms carry skill text through `--append-system-prompt`, not a real skill invocation, and the build arms omit the
  four other documents a real build reads.
- One model tier, the process default; three runs per arm resolve only a large effect.
- The staging starts at the presentation step with the report in hand, so the earlier dispatch, triage and fix
  steps are stubbed, and the Phase 3 prompt tells the session the re-review round has already run.
- The check is lexical (see its trade-offs); the plain form `code review` can appear without attribution.

## Results: code review baselines and headroom (2026-10-15)

Nine baseline captures, three per new site, run per "Dispatch" with the arms `baseline-review.md`,
`baseline-buildreview.md` and `baseline-drivebuild.md`, each from its site's prompt over a fresh copy of `fixtures/`
and an empty second directory. All nine exited 0, every capture is non-empty and every `.stderr.txt` is empty, so none
was excluded or replaced. Every capture is clean under `capture_scan.py` and `scripts/leak-gate` (both exit 0) and a
`grep` for host paths and user names finds none. The grader is `wording_grader.py` with `--reports
fixtures/reports-code-review.md`.

One deviation from the Dispatch text, on the host: `arms/baseline-drivebuild.md` is 154,938 bytes, over the Linux
limit of 131,072 bytes for one argument string, so `--append-system-prompt "<contents>"` cannot be started (the
shell refused with "Argument list too long" before any model ran, and those files were deleted, not counted).
`baseline-drivebuild-1` to `-3` were run with `--append-system-prompt-file arms/baseline-drivebuild.md`, which
supplies the same bytes. The other six captures used the inline form. Nothing else differs.

### Verdicts

| Capture | `code-review-name` | `severity-name` |
|---|---|---|
| `baseline-review-1`, `-2`, `-3` | `none`, `none`, `none` | `internal`, `internal`, `internal` |
| `baseline-buildreview-1`, `-2`, `-3` | `none`, `none`, `none` | `internal`, `internal`, `internal` |
| `baseline-drivebuild-1`, `-2`, `-3` | `none`, `none`, `none` | `internal`, `internal`, `internal` |

### Headroom, per site and check

| Site | `code-review-name` | `severity-name` |
|---|---|---|
| review | no headroom: 0 of 3 `internal`, 3 of 3 `none` | headroom: 3 of 3 `internal` |
| buildreview | no headroom: 0 of 3 `internal`, 3 of 3 `none` | headroom: 3 of 3 `internal` |
| drivebuild | no headroom: 0 of 3 `internal`, 3 of 3 `none` | headroom: 3 of 3 `internal` |

**The parent's Known Unknown, whether the baseline has headroom on `code-review-name`: it has none, at any of the
three sites.** No baseline capture writes `code review`, `code-review`, `code reviewer` or `code-reviewer` in any
form (a case-insensitive `grep` for `code[ -]review` over the nine captures finds nothing). They name the agent "the
reviewer" or "the review", which the grammar reads `none`, and a majority of `none` is no measurable headroom. No site
is majority `plain` on either check, so the end state for a baseline already `plain` (a check closed on the baseline,
a read directive dropped) does not apply. The stop rule applies: "a baseline has no headroom on a check" stops the run
and nothing further is run or changed; the choice goes to the operator.

### Hand check

One capture per verdict word seen, per site, read by eye against the grader's verdict.

- `baseline-review-1` (and `-3`): the session says "The reviewer's verdict is FIX_FIRST" and "They found one
  Critical issue, one Important issue and two Minor ones"; it never writes `code review` or the agent's name. By eye
  `none` for `code-review-name`; `internal` for `severity-name` (the level words `Critical`, `Important`, `Minor` and
  "Critical issue" head and name the findings). Agrees with the grader.
- `baseline-buildreview-2`: "The reviewer still says FIX_FIRST", headings and bold lead-ins `Critical: confirmed.`,
  `Important: confirmed.`, `Minor: confirmed.`. By eye `none` and `internal`. Agrees.
- `baseline-drivebuild-1`: "The reviewer's verdict is FIX_FIRST", sections headed `**Critical**`, `**Important**`,
  `**Minor**`. By eye `none` and `internal`. Agrees.

No capture reads `plain` or `internal` on `code-review-name`, so no capture of those words exists to hand check. The
`none` reading is not a misgrade: the phrase the check looks for is absent, not hidden by a boundary rule (the
plural, hyphen and fence rules removed nothing here, since the strings are not in the captures at all). No hand-confirmed
misgrade, so the one amendment allowance is unspent and no amendment is made.

### Not read here

The finding-order reading by eye (AC17, AC18) of these nine captures is not done in this section and stays recorded as
unmeasured.
