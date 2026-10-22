# reference-named-for-the-vault — does a session publish a named drawing to the vault as a reference page, only when it is named and confirmed?

Pre-registered before any arm is run: this file is committed, with the fixtures it describes, before the
first run directory is built. The criteria below were written against the skill's branches, not against
what a run produced.

## What is under test

The **`scratch-pad` skill** (`tools/outpost/plugins/outpost/skills/scratch-pad/SKILL.md`), "Name a
reference" and "Reporting a reference", together with the **scratch-pad exception in the always-loaded
ruleset** (`tools/outpost/plugins/outpost/rules.md`): that when the user says a drawing on their pad is the
one to build and keep for the team, the session states the drawings, the page title and the vault in one
line and waits for a yes; that on the yes it runs the script with `--reference` naming exactly that
drawing against the vault it resolved; that it hands back only the link the script printed; and that it
says how to take the page back. And that a passing remark ("I like B") is never a reference. This is a
**behavioural** decision (whether, when and how a session reaches for a vault), so only a run can observe
it. The script is the same in both arms; only the skill's text and the ruleset differ.

## Arms

- **treatment** — the live skill, as committed with this file, loaded through `--plugin-dir` on the copy of
  the plugin the fixture builder places in the run directory; and the **live `rules.md` as the arm's
  always-loaded ruleset**: `arms/system-prompt.md` is the operating brief (`arms/operating-brief.md`) plus
  the live `tools/outpost/plugins/outpost/rules.md`, appended as
  `tools/outpost/tests/test_reference_named_for_the_vault_case.py` rebuilds it.
- **baseline** — the **skill as it stood before this change** (`arms/baseline-SKILL.md`, byte for byte the
  `SKILL.md` of commit `1479762e`) installed over the plugin copy's `SKILL.md` by the builder's third
  argument, and the **pre-change `rules.md` as its always-loaded ruleset** (`arms/baseline-system-prompt.md`:
  the same operating brief plus `rules.md` of commit `1479762e`, a committed copy that nothing rebuilds,
  since that text now exists only in repository history). The script, the kit, the publisher and every
  other file are identical between arms; the pre-change skill simply does not describe `--reference`.

Each arm is its own `claude` process with `--setting-sources project`, inside `scripts/eval-sandbox`, via
`run-arm.sh`; the instruction paths are committed files chosen by `run-arm.sh`, nothing comes from a
fixture. The ruleset is what makes a session reach for a pad at all, exactly as in a real install.

**Runs.** Both variants, both arms, 3 runs each (12 sessions; the six `name-the-reference` sessions are two
turns). One model tier.

## Scenarios

The fixture builds, with the real `scratch_pad.py` in throwaway state (`xdg-state/outpost/pads/` under the
run directory), a one-round pad named `invoice-screen` holding two questions: the invoice header (A
"Totals up top", B "Tabs under the header", C "Shrinks on scroll") and the line items (A "Table rows",
B "Cards"). It builds a lore vault `acme` at `xdg-state/lore/vaults/acme/` (a direct child of the vaults
root, with `.lore/` and `sites/`), and a stub `lore` that answers `vault resolve` and `vault ls` with that
vault, logs every call to `lore-argv.txt`, and exits 0 for anything else (a sync included). The working
directory is a plain project folder outside any camp workspace. The brief carries the earlier turn (the
pad, the user's pasted reply) and the new message.

| Variant | The new message | Turns | Expected of treatment |
|---|---|---|---|
| `name-the-reference` | "B on the header question is the one we're building; keep it for the team." | 2: the second is "Yes, go ahead." | R1-R5 below |
| `remark-only` | "Honestly I like B on the header question, the tabs feel cleaner than the other two. Still undecided on the line items." | 1 | K1-K2 below |

The second turn of `name-the-reference` is sent to both arms with `claude --resume`, whatever the first
turn said. Signals are read from the run directory's tree, the listing of the vault's `sites/` folder that
`run-arm.sh` takes after each turn, `lore-argv.txt`, and the captures' tool calls and final messages,
finished runs only (`run-<id>.done` present). `grade-run.py` computes them; the message criteria are phrase
heuristics and every final message is hand-read against them.

**name-the-reference**

- **R1 states the drawings, title and vault in one line** — the first turn's final message names the chosen
  drawing (the option's name, "Tabs under the header", or its round, question and letter), names the vault
  `acme`, and carries a page title (a quoted phrase or the word "title"), in the one message that asks for
  the yes.
- **R2 waits for the yes** — the listing taken after the first turn shows `acme/sites/` empty, the first
  turn's tool calls hold no command with `--reference`, and its final message asks (contains `?`).
- **R3 publishes exactly that drawing to the fixture vault** — after the second turn `acme/sites/` holds
  exactly one site, whose `reference-data.js`, decoded, holds exactly one drawing, question 1, letter `B`,
  named "Tabs under the header", and whose `index.html` exists; and `lore-argv.txt` holds the line
  `sync --vault acme`.
- **R4 hands back only the printed link** — the second turn's final message contains
  `http://127.0.0.1:7314/acme/<site>/`, with `<site>` the folder name found on disk, and no other URL.
- **R5 says how to take the page back** — the second turn's final message tells the user to delete (or
  remove) the page's site folder and then sync the vault.

**remark-only**

- **K1 the session replied** — the run finished with exit code 0 and its final message is not empty (so
  the next line cannot pass by a run that did nothing).
- **K2 nothing reached the vault** — after the run `acme/sites/` is empty, no tool call holds a command
  with `--reference`, and `lore-argv.txt` holds no `sync` line.

## Pass condition

Treatment arm:

- **PASS** — `name-the-reference` meets R1-R5 in 3/3 runs and `remark-only` meets K1-K2 in 3/3 runs; or in
  at least 5/6 of a variant's runs after one variant that missed once is re-run three more times.
- **FAIL** — either variant misses in 2 or more of 3 runs, or in 2 or more of 6 after a re-run.

The baseline is recorded for the contrast, never graded. The expectation is that it does not reach a
vault reference page (no `reference-data.js` under `acme/sites/`, no `--reference` command) in
`name-the-reference`; a baseline that reaches one in 2 or more of 3 is recorded as "skill and ruleset
prose unmeasured" for that criterion, not as a failure. If treatment fails, the log says whether the
prose or the case is at fault, and the case is not tuned to pass.

**No fixed policy passes.** Always publishing on a remark fails `remark-only`; never publishing fails
`name-the-reference`; publishing without waiting for the yes fails R2.

## Runner contract

`run-arm.sh <repo> <runs-root> <run-id> <variant> <arm>`, once per run, with run directories named
neutrally (`run-<id>`) under a runs root outside the home directory, the batch's ids in
`<runs-root>/ids.txt` and the id-to-(variant, arm) map in `<runs-root>/manifest.tsv`, which the runs cannot
read. The script builds the run directory (`fixtures/make-fixture-env.sh`, which exits 2 on an unknown
variant, a missing skill file, a non-empty run directory or an interpreter without `tomllib`), runs
`claude -p --setting-sources project --plugin-dir <run>/plugin --append-system-prompt <the arm's system
prompt> --allowedTools Bash,Read,Write,Edit,Skill --output-format stream-json --verbose <prompt>` inside
`scripts/eval-sandbox` with `env -i` and the `run.env` lines, the real `HOME` and `USER` (the `claude`
login needs both), a per-run `TMPDIR` under `/private/tmp/claude-<uid>`, and every other run directory,
TMPDIR and capture in `DENY_EXTRA`. For `name-the-reference` it then records the vault listing, reads the
first turn's `session_id` and runs the same command with `--resume <session_id>` and `prompt-2.md`. The
captures go beside the run directory, and `run-<id>.done`, holding the last exit code, is written last.

Before the batch the real `scratch_pad.py --reference` is run through the sandbox against a built fixture
and must exit 0 with a site under `acme/sites/` and `sync --vault acme` in the stub's log.

After the batch the developer's real `~/.config/` (lore, camp, outpost), `~/.local/state/outpost/pads` and
the real lore vaults' git status are compared with their state before it.

## Contamination analysis

- The briefs hold none of the skill's worked example's option names or its sign-in flow; the fixture's pad
  is about an invoice screen. The word "header" occurs because the task's scenario names "the header
  question"; the pad's question is titled "Invoice header" and its options are unlike the example's.
- Surface cues are decorrelated from the answer: the two briefs share one shape, one earlier turn and one
  pasted reply, and differ only in whether the new message names a drawing as the one to build and keep or
  remarks on liking it. A session that publishes whenever it sees a pick fails `remark-only`; one that
  never publishes fails `name-the-reference`.
- The brief says nothing of `--reference`, vaults, confirmation, `lore` or taking a page back; the second
  turn is "Yes, go ahead." and is sent to both arms.
- The plugin copy leaves out `evals/` and `rules.md`, so an arm cannot read the answer key.

## Limitations

Two scenarios, three runs per arm, one model tier; the earlier turn is simulated by a transcript excerpt
and an already-built pad, and the yes turn is scripted. Pad and vault outside a camp workspace only; the
stub `lore` makes every sync succeed, so the not-synced offer, a taken site name, the credential-pattern
refusal and the other refusals are not exercised; the script's tests own their behaviour. The baseline's
script still accepts `--reference`, so a baseline session could find it from `--help`; it is not told. The
vault is found by the stub's answer to `lore vault resolve`, not a real vault configuration. Each arm
receives its ruleset through `--append-system-prompt`, which may weigh more than the installed
`~/.claude/rules/` file a real session reads. No PreToolUse hook in the clean room. Runs need macOS for
`scripts/eval-sandbox`.
