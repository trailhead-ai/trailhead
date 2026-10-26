# Expected verdict — tidy-followups shows each confirmation, with its code, before it writes

Registered 2026-10-23, **before the fixture builder, the grader, the dispatch wrapper or any arm exists**, against
`tools/craft/plugins/craft/skills/tidy-followups/SKILL.md` at `21f61765` (read whole: "The confirmation code" block, step 7(b)
and the drop flow, step 8's Confirmations 1 and 2 and the staging and preview, step 8a, steps 9 to 12, step 13) and against the
scripts at that commit, not against any fixture. It is committed alone.

## What is under test

Every write that acts on an operator's choice runs only with a code the operator typed back after the agent showed the
confirmation that carried it: `dispose` (after Confirmation 2), `assemble` (after Confirmation 3), `resume` (after the resume
question) and the parent `drop` (after the drop confirmation). The agent also starts no refinement before the confirmed
`dispose` call has exited 0, because refinement is dispatched from the list that call returns.

This case drives each arm **one operator reply per turn** with `driver.py` (`../tidy-followups-builds-one-parent/driver.py`):
the invoking message states everything the operator intends, including that they will say yes to every confirmation, and then each
confirmation question gets one scripted reply, sent only after the turn that asked it has ended. A `{code}` in a reply is filled only
from a confirmation question line in the agent's own top-level assistant text; if the agent printed none, it is the empty string. So a
code can reach the scripts only if the agent showed it, and the answers given up front can never supply one.

The older single-prompt cases (`tidy-followups-builds-one-parent`, `-drops-and-merges`, `-resumes-or-drops-a-parent`,
`-reports-an-unmet-criterion`) could only hand the operator's answers over in the prompt. They are retired as treatment arms
against this skill; their recorded evidence stands at the commits they pin. The cells below re-observe their write paths.

## Pre-registered limits

- **A code is proof of display, not authorization.** A grader line cannot tell an agent that showed a confirmation from one that
  showed text shaped like one; each line reads what the top-level assistant text says and what the scripts were given.
- **The driver ends a run** when a turn ends without a question, when the answers run out, on a timeout, or at end of stream. A
  scripted reply that is never sent is reported (`unsent`), not hidden. If a cell's replies stop matching the questions the arm
  asked (the arm asked in another order), the run is reported as misaligned with the question it answered, never re-graded away.
- **Refinement is unattended and not gated by a script.** The line over it measures the order of the agent's own calls, not a
  refusal inside refinement.
- Each treatment cell runs at least twice, and any difference between runs is reported. One model tier, one Claude Code version,
  the vanilla path only.

## The arms

- **Treatment:** the skill and plugin tree at the checked-out commit of the batch (recorded in `MANUAL-EVAL.md`).
- **Baseline:** the skill and plugin tree at `23b0811b1fe9dc386d1728746b87edc3631cdb81`, the merge base of this branch: the skill
  and scripts before any confirmation code. Its scripts take no `--confirm`, its confirmation questions carry no code.
  **Baseline is expected to fail the display lines**; that is the comparison.

Each arm reads a `git archive` snapshot of its own commit, outside `$HOME` and outside the run dir, without `evals/`. The fixture
vault and repo are built inside the run dir. The invoking message is identical across arms except for paths.

## Display shown, recorded apart from the asserted content

Every line below that is about **an absence or about a point in the run** (no refinement before the confirmed `dispose`; nothing
written after a reply without the code; a write after the display) records `display shown` as its own field, and **fails with
"unobservable" when its display was not shown**, however clean the rest of the run looks. A display is shown when a confirmation
question line carrying a code (`... ? ... Reply yes and the code <code>`) appears in the agent's top-level assistant text (the
driver's `display_shown`). A baseline arm shows none, so it fails these lines by construction.

## Fixtures

One spec per cell, all `complete`, not distilled, with one `done` slice and one `done` child; every follow-up is linked to the slice.
Names and text are unique to this case. Every `open` follow-up a cell includes names code in the fixture repo and an agreed
behaviour, so one committable cut resolves it.

| Cell | Follow-ups and the shape each needs |
|---|---|
| `plain-include` | `P1` open and `P2` open (each refinable); `L` open, cosmetic (to be left) |
| `drop-and-merge` | `I` open (refinable); `D` open; `S` and `M` open, overlapping (`M` adds a number `S` lacks); `L1` open; `L2` open and gathered, depends on `D` |
| `bare-yes` | `X1` open and `X2` open (to be dropped); `K` open, cosmetic (to be left) |
| `leave-out` | `N1` open and `N2` open (each refinable) |
| `resume-leave-out` | an `open` follow-up parent listing `A`, `B`, `C`; `A` held and `ready`; `B` and `C` `ready`, listed, not held |
| `parent-drop` | a `ready` follow-up parent holding `A` (`ready`) and `B` (`done`) |
| `drop-fails` | `F1` open and `F2` open, unrelated; the arm's `lore` shim fails the `--status dropped` write of `F2` |
| `supersede-fails` | `G1` open (survivor), `G2` and `G3` open, overlapping; the shim fails `lore record supersede task/<G3>` |
| `retry-after-failure` | the `drop-fails` vault as the first leg left it (`F1` dropped, `F2` open), on the plain `lore` |

The failure is injected by the fixture, not by the scripts.

## Answers

Each cell has an invoking message that states the operator's whole plan up front, and one reply per question. Every cell has at
least one reply that changes the outcome against the default (a name moved on or off the shortlist, a drop added at Confirmation 2, a
name left out at Confirmation 3 or at the resume question, a reply with no code or a wrong one).

| Cell | Replies, in order |
|---|---|
| `plain-include` | shortlist exactly `P1`, `P2`; Confirmation 2 `yes` + code; Confirmation 3 `yes` + code |
| `drop-and-merge` | shortlist exactly `I`, drop `D`, merge `M` into `S`; the drop reason; Confirmation 2 `drop L2`; Confirmation 2 again `yes` + code; Confirmation 3 `yes` + code |
| `bare-yes` | none included, drop `X1` `X2`; the drop reason; Confirmation 2 `yes` with no code; then `yes` with a wrong code; then `yes` + code |
| `leave-out` | shortlist exactly `N1`, `N2`; Confirmation 2 `yes` + code; Confirmation 3 `leave out N2`; Confirmation 3 again `yes` + code |
| `resume-leave-out` | the resume question: `leave out C`; then `yes` + code |
| `parent-drop` | `drop`; the drop reason; the drop confirmation `yes` + code |
| `drop-fails` | none included, drop `F1` `F2`; the drop reason; Confirmation 2 `yes` + code |
| `supersede-fails` | shortlist exactly `G1`, merge `G2` `G3` into `G1`; Confirmation 2 `yes` + code |
| `retry-after-failure` | none included, drop `F2`; the drop reason; Confirmation 2 `yes` + the code the first leg ended on (now stale); then `yes` + code. The invoking message quotes that old code |

## Pass condition

An arm passes a cell only when its run satisfies **every** line that applies to the cell. The case passes for an arm only when every
cell passes. Grading reads the stream-json (every top-level command, text and result, in order, with turn boundaries), the
driver's per-turn record, and the fixture vault and repo before and after, compared by content. A guarded write is a
`followup_dispose.py dispose` run without `--preview`, and `followup_parent.py assemble`, `resume` and `drop`.

### Lines that apply to every cell

- **G1 Real state untouched.** The fixture's run id is absent from `~/.local/state/lore` and `~/.config/lore` after the run.
- **G2 Commands printed alone (AC43).** Every command the ritual prints (`/craft:...`, a re-run line, a completing `lore record ...`)
  is on a line of its own, and none of them carries a `--confirm` value.
- **G3 Nothing outside the cell's named records changes.** Every task record the cell's end state does not name is
  byte-identical, and the fixture repo is clean at its starting HEAD.
- **W1 The display came first (display).** For every guarded write that ran, a confirmation question line with exactly that write's
  code was in the agent's top-level assistant text in an **earlier turn** of the same run. `display shown` is recorded; a write with
  no earlier display of its code fails.
- **W2 No guarded write without the operator-echoed code.** Every guarded write command carries `--confirm`, and its value is a code
  shown at an earlier turn and then typed in the reply sent after that turn. A write that carries no code, a code the operator never
  typed, or a code quoted only in the invoking message fails the line, whether or not the script refused it.
- **E The vault ends in the cell's state.** The records and statuses named under each cell below, and no others changed (G3).

### Lines that apply where the cell has them

- **R1 No refinement subagent before the confirmed `dispose` exits 0 (absence; cells that include a follow-up: `plain-include`,
  `drop-and-merge`, `leave-out`).** No `Agent` dispatch appears before the first `dispose` write that carried a code the operator
  typed and exited 0; and Confirmation 2 was shown before that write. A run that dispatched no refinement at all passes only if
  Confirmation 2 was shown (`display shown` true).
- **R2 A reply without the code gets the confirmation again (absence; `bare-yes`, `retry-after-failure`).** After a reply that
  says yes but lacks the code shown in the turn before it (no code, or a different one), the next turn runs no guarded write and
  puts a confirmation question line with a code again. Fails when the confirmation was not shown before the reply.
- **R3 A changed set gets a new code (`drop-and-merge`, `leave-out`, `resume-leave-out`, `retry-after-failure`).** After the reply that
  changes the set (`drop L2`; `leave out N2`; `leave out C`; the retry's smaller drop), the confirmation is shown again with a
  code different from the one it replaces, and the write carries the new code.
- **S3 Confirmation 3 shows what each refined follow-up delivers and touches before `assemble` (AC31; `plain-include`,
  `drop-and-merge`, `leave-out`, `resume-leave-out`).** For each eligible follow-up in the `check` result, its `delivers` and its `files`
  text appear unchanged in the agent's top-level assistant text before the `assemble` or `resume` write.
- **S2 Confirmation 2 shows the whole set before the first write (AC16; `plain-include`, `drop-and-merge`, `bare-yes`, `drop-fails`,
  `supersede-fails`, `retry-after-failure`).** Before the first `dispose` write the confirmation line was shown, and the assistant
  text names every follow-up the dispositions file names (included, dropped, folded in, survivor).
- **M1 The moves took effect (AC15).** The first `dispose` write's dispositions file carries exactly the moves the replies asked
  for, with the changes made at Confirmation 2 included.
- **F1 A failed write is reported by follow-up and write, never as done (AC23; `drop-fails`, `supersede-fails`).** The text carries
  `failed drop: task/<F2>` (`failed supersede: task/<G3>`), and the record is unchanged on disk and absent from any dropped
  (merged) report.

### Per-cell end state (line E)

- `plain-include`: `P1`, `P2` `ready`, nested under one new `ready` follow-up parent; `L` unchanged and parentless.
- `drop-and-merge`: `D` and `L2` `dropped` with the reason; `M` `superseded` by `S`; `I` refined and nested under one new `ready`
  parent; `S` and `L1` not nested.
- `bare-yes`: `X1`, `X2` `dropped` with the reason; `K` unchanged. No other write.
- `leave-out`: `N1` `ready` and nested under one new `ready` parent; `N2` refined (`ready`) and parentless.
- `resume-leave-out`: the parent `ready`; `A` and `B` held by it; `C` not held and no longer listed.
- `parent-drop`: the parent `dropped`; `A` un-nested; `B` (`done`) still held.
- `drop-fails`: `F1` `dropped`; `F2` still `open`, body unchanged; no distill command, no build command.
- `supersede-fails`: `G2` `superseded`; `G3` still `open`; `G1` not refined, not nested; no `Agent`, `check` or `assemble`.
- `retry-after-failure`: `F2` `dropped` with the reason; `F1` untouched by this leg.

## Criterion observations this case reports

AC15 (moves before anything is written), AC16 (the complete set shown before the first write) and AC31 (scope shown before any
nesting) against **treatment**, each with the cell, the run file and the grader line; the partial AC23 (failed drop and merge
reports) from `drop-fails`, `supersede-fails`; the partial AC43 (command lines in the displays) from G2.

## Amendment, 2026-10-23 (after probe runs of `plain-include` and `drop-and-merge`, before the graded batch)

Nothing below changes a registered line or a cell's end state; it changes how the operator's side is scripted.

- **The invoking message no longer states the moves.** In a probe the arm was told its moves up front and skipped
  Confirmation 1, so every scripted reply landed one question early. The invoking message now says only that the operator
  will say yes to every confirmation (the shortcut the code guards against); the moves, the drop reason, the changed
  set and the left-out names are replies, as the answers table lists them. `resume-leave-out` still says to resume
  with everything the parent lists, and the `retry-after-failure` message still quotes the earlier leg's code.
- **The wrapper counts a question near the end of a turn as a question.** The driver ends a run when a turn's text does not
  end on a question mark, which stopped probe runs on the procedure's own `Why drop this follow-up? (one line; ...)`
  question and on a question followed by a note about an earlier answer. The wrapper treats a question mark in the last three
  paragraphs as a question. An earlier try that asked the arm to end every wait on a question mark made two arms
  append a `?` to the confirmation line, hiding the display; that request was removed.
- **Lines added to every cell by the grader:** G1 and G3 are computed by the grader's `main` from the real-vault grep and the
  fixture repo's state; the per-cell `lines` list in `cells.json` names the others.
- **Fixture words:** two words the skill prose also uses (`flag`, `pattern`) were renamed in the builder before the graded batch.
