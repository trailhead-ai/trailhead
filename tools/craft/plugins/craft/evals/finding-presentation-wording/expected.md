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
