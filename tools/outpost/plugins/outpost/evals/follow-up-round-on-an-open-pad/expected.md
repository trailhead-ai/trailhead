# follow-up-round-on-an-open-pad — does a session add a follow-up visual question to the pad the user already has?

Pre-registered before any arm is run: this file is committed before the first run directory
is built. The verdicts below were written against the skill's branches, not against what a run
produced.

**Revised after batch 1 (2026-10-19, commit `f15e8fe7`).** The file as first committed (`dd40bedd`)
differed as follows, so the criteria below are not exactly the pre-registered ones:

- **C4** read "the final message names the three row styles in words and puts the choice to the
  user"; **N3** read "the final message names the three status styles and puts the choice". Both
  now read "names at least two of the ... styles in words and invites the user to choose, in words
  or on the pad".
- The "Arms" paragraph described both arms as running without `rules.md`; it now says both carry
  the live `rules.md`. A sentence was also added to Limitations.
- The Pass condition section is byte-identical across the revision.

The rewording, and the matching widening of `grade-run.py`'s `_asks_in_words`, were made after
seeing batch 1 runs and are post-hoc. They did not decide the verdict: re-graded under the old
and the new rule, all 12 batch 2 finals score the same (0 flips, every treatment final contains
`?`). See `MANUAL-EVAL.md`, "Case: follow-up-round-on-an-open-pad".

## What is under test

The **`scratch-pad` skill** (`tools/outpost/plugins/outpost/skills/scratch-pad/SKILL.md`) as it
describes rounds: that a follow-up visual question about the same design goes into the pad the
user already has, with `--add`; that a later round's `settled` list says what earlier rounds
settled; that after an add the user is told the new round is ready and to open the link again;
and that a different topic gets a pad of its own. This is a **behavioural** decision (whether,
where and how a session reaches for a round), so only a run can observe it. The script is the
same in both arms; only the skill's text differs.

## Arms

- **treatment** — the live skill, as committed with this file, loaded through `--plugin-dir` on
  the copy of the plugin the fixture builder places in the run directory.
- **baseline** — `arms/baseline-SKILL.md`, the skill byte for byte as it stood at commit
  `5512014a` (before rounds were described), installed over the plugin copy's `SKILL.md` by the
  builder's third argument. The script, the kit and every other file are identical between arms.

Both arms get `arms/system-prompt.md` as the system prompt: the operating brief
(`arms/operating-brief.md`) plus the live `tools/outpost/plugins/outpost/rules.md`, appended as
`tools/outpost/tests/test_follow_up_round_on_an_open_pad_case.py` rebuilds it. The ruleset is what makes a
session reach for a pad at all (its "Scratch pads" section), exactly as in a real install; it
says nothing of rounds, so what happens once a pad exists is decided by the skill alone. An
earlier batch ran without the ruleset and is logged in `MANUAL-EVAL.md` as an authoring error:
most of its sessions never reached for a pad, so it measured nothing about the skill. Each arm
is its own `claude` process with `--setting-sources project`, inside `scripts/eval-sandbox`, via
`run-arm.sh`. The instructions paths are committed files chosen by `run-arm.sh`; nothing comes
from a fixture.

**Runs.** Both variants, both arms, 3 runs each (12). One model tier.

## Scenarios

The fixture builds, with the real `scratch_pad.py` in throwaway state (`xdg-state/outpost/pads/`
under the run directory), a one-round pad named `audit-filters` ("Audit log filters": where the
filters of an audit-log viewer sit). The working directory is a plain project folder outside any
camp workspace. The brief carries the earlier turn (the pad, the user's pasted reply picking the
left rail) and a new message.

| Variant | The new message asks | Expected of treatment |
|---|---|---|
| `follow-up-same-design` | how one entry of the same viewer's table looks: single line, two lines, or a card | C1-C4 below |
| `follow-up-new-topic` | how an invoice's status shows in a list on a different screen: dot, badge, or edge strip | N1-N3 below |

Signals, read from the run directory's tree and the capture's final message, finished runs only
(`run-<id>.done` present). `grade-run.py` computes them; the message criteria are phrase
heuristics and every final message is hand-read against them.

**follow-up-same-design**

- **C1 round added to the same pad** — `audit-filters` holds exactly `round-1` and `round-2`, no
  other pad exists, and the session's `scratch_pad.py` command carried `--add`.
- **C2 settled names the earlier answer** — the new round's `settled` list (decoded from
  `round-2/round.js`) has a line naming the left rail.
- **C3 told to open the link again** — the final message tells the user the new round is ready
  and to open the link (or the pad) again, or says an open tab will not show it.
- **C4 asked in words** — the final message names at least two of the row styles in words and
  invites the user to choose, in words or on the pad.

**follow-up-new-topic**

- **N1 a new pad under its own name** — exactly one pad besides `audit-filters` exists.
- **N2 `audit-filters` unchanged** — it still holds only `round-1`.
- **N3 asked in words** — the final message names at least two of the status styles in words
  and invites the user to choose, in words or on the pad.

## Pass condition

Treatment arm:

- **PASS** — `follow-up-same-design` meets C1-C4 in 3/3 runs and `follow-up-new-topic` meets
  N1-N3 in 3/3 runs; or in at least 5/6 of a variant's runs after one variant that missed once
  is re-run three more times.
- **FAIL** — either variant misses in 2 or more of 3 runs, or in 2 or more of 6 after a re-run.

The baseline is recorded for the contrast, never graded. The baseline-versus-treatment gap on
C1-C3 is reported as the skill text's measured effect; a baseline that matches treatment is
recorded as "skill prose unmeasured", not as a failure. If treatment fails, the log says whether
the skill's prose or the case is at fault, and the case is not tuned to pass.

**No fixed policy passes.** Always adding to the existing pad hits the same-design variant and
misses the new-topic one; always starting a new pad does the reverse.

## Runner contract

`run-arm.sh <repo> <runs-root> <run-id> <variant> <arm>`, once per run, with run directories
named neutrally (`run-<id>`) under a runs root outside the home directory, the batch's ids in
`<runs-root>/ids.txt` and the id-to-(variant, arm) map in `<runs-root>/manifest.tsv`, which the
runs cannot read. The script builds the run directory (`fixtures/make-fixture-env.sh`, which
exits 2 on an unknown variant, a missing skill file, a non-empty run directory or an interpreter
without `tomllib`), then runs `claude -p --setting-sources project --plugin-dir <run>/plugin
--append-system-prompt <operating brief> --allowedTools Bash,Read,Write,Edit,Skill
--output-format stream-json --verbose <prompt>` inside `scripts/eval-sandbox` with
`env -i` and the `run.env` lines, the real `HOME` and `USER` (the `claude` login needs both),
a per-run `TMPDIR` under `/private/tmp/claude-<uid>`, and every other run directory, TMPDIR and
capture in `DENY_EXTRA`. The capture goes beside the run directory, and `run-<id>.done`,
holding the exit code, is written last.

Before the batch the real `scratch_pad.py --add` is run through the sandbox against a built
fixture and must exit 0.

After the batch the developer's real `~/.config/` (lore, camp, outpost), `~/.local/state/outpost/pads`
and the real lore vaults' git status are compared with their state before it.

## Contamination analysis

- No brief reuses the skill's worked example (a settings page with a header question, "Split
  header" / "Stacked header", search placement) or its option names. The fixture's pad is about an
  audit-log viewer's filters; the follow-up asks about entry rows or invoice status.
- Surface cues are decorrelated from the answer: the two briefs share one shape, one earlier turn
  and one pasted reply, and differ only in whether the new question is about the same design.
  A session that adds to the pad whenever one exists fails the new-topic variant; one that
  never adds fails the same-design one.
- The brief says nothing of `--add`, rounds, `settled` or "open the link again"; the pasted reply
  carries `"round": 1` as the kit writes it.
- The plugin copy leaves out `evals/` and `rules.md`, so an arm cannot read the answer key; the
  builder's `round-1.json` is read from the repo, not copied into the run.

## Limitations

Two scenarios, three runs per arm, one model tier, single-turn `claude -p`: the earlier turn is
simulated by a transcript excerpt and an already-built pad, not lived. The pad is outside a
camp workspace only; the workspace path (publisher, "not confirmed" note) is not measured. The
refusals (does not exist, can't take another round, another session is adding, duplicate title,
`settled`) are not exercised; the script's tests own their behaviour. Each arm receives `rules.md` through `--append-system-prompt`, which may weigh more than the
installed `~/.claude/rules/` file a real session reads. No PreToolUse hook in the
clean room. Runs need macOS for `scripts/eval-sandbox`.
