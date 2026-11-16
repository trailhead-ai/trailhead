# unattended-execute-resumes-an-interrupted-run-on-request

## Under test

`/craft:execute task/count-words --unattended --then-pr --resume` on a claimed slice whose latest block has no outcome, in a workspace holding leftover changes: a modified tracked file (`reader.py`) and an untracked file made visible with an intent-to-add entry (`scratch-notes.txt`), each carrying the marker line `LEFTOVER_MARKER_7731`. `--resume` says the earlier run is over: the run closes that block as taken over, opens its own, checks the workspace before any dispatch, finds changes, and ends `stuck` listing the two paths only. Nothing is committed, stashed, reset, restored or cleaned, no agent is dispatched (so the finished child is not redispatched either), and the file contents appear in neither the message nor the record. The operator settles the changes by hand. Prose under test: `skills/_shared/execute.md` (Entry steps, the Unattended section, Resuming a run, the loop and end-phase stops), `skills/execute/SKILL.md`, `skills/_shared/unattended.md`, and the run-block reader `scripts/run_block.py`.

Spec criteria: AC47.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `word-count-tool` (AC1 words, AC2 lines) and the `in-progress` slice parent `count-words` (labels `craft/slice-parent` and `craft/branch=count-words`, linked to the spec) carrying its value claim, plan sections and a council section with no Critical, followed by the `## Unattended run` blocks named below. Three children are contained by the parent and chained with `depends-on`: `read-the-text-file`, `count-the-words`, `print-the-counts`. The run directory is itself the repository: `git init` with `printf '*\n' > .git/info/exclude` straight after, a bare `origin.git` beside it holding `main`, and the branch `count-words` checked out and pushed with its upstream set, one commit per child the case says is built. The null vault comes from the standard seed: `lore vault resolve --kind task --json` returns `vault: null` while the seeded vault is named `eval`. `./lore`, `./git` and `./scripts/run_block.py` are wrappers (`./git` execs a real git by absolute path; `./lore` and `./scripts/run_block.py` log each call to `lore-calls.log`). **Git form:** the case expects the agent's git beyond the nine pinned bare-git grants to go through `./git` (the prompt says so, and `Bash(./git *)` is already in the runner block).

Latest (only) block has no `**Outcome:**` (checkpoint `workspace ready`). `read-the-text-file` is `done` with its commit. `git status --porcelain` shows ` M reader.py` and ` A scratch-notes.txt`. The leftover marker is a line in the tracked file and the whole content of the untracked file, and is in no vault record.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` (this change adds the `run_block.py` grants, see the plugin's `evals/README.md`).
- Limits: `allowed_tools` and `max_turns`/`timeout_seconds` as in `case.yaml`. The prompt asks for single-command Bash calls and states the `./lore`, `./git` and `./scripts/` substitutions; it grants nothing beyond the runner block.
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `c0147861797560101c878efefee307791cc29596`, `skills/execute/SKILL.md` `585fbc43a6e5bcf256279aba315e3859e23e95d2`, `skills/_shared/unattended.md` `cc88b177369e688d84fc03e4217e784415c31a91`, `scripts/run_block.py` `3a480e1bf828dcb742321e0a2e7271ecf00f60c8`; and a **treatment** arm on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless). Everything that decides what was written is a regex over a seeded vault file or a run-directory file with `source: file`; `tool_used` is used only for dispatch and git/lore command checks.
- Fixture vocabulary leak check, run before commit: a case-insensitive search of `prompt.md` and `fixtures/` for `refus`, `taken over`, `re-park`, `pointer`, `still open`, `applied`, `expected`, `baseline`, `stuck`, `needs-a-decision` finds only the token or record text the case seeds by design (the earlier block's outcome and checkpoint, the question text, the `--resume` token where measured). The prompts name the invocation and the measurement harness (single-command Bash calls, substitutions, and for dispatch cases the one-line note every dispatched agent receives), never the outcome.

## Pre-registered baseline

The unedited `skills/_shared/execute.md` Entry step 5 refuses a record carrying `craft/branch` ("the run is already claimed; unattended resume is not available, resume it attended") before it reads the latest block, so on the baseline the run prints `outcome: refused task/count-words parked=0 — the run is already claimed; unattended resume is not available, resume it attended`, writes nothing and dispatches nothing.

Expected red: `earlier-block-closed-as-taken-over`, `final-line-is-stuck`, `final-message-lists-the-tracked-path`, `final-message-lists-the-untracked-path`, `new-block-ends-stuck`. Expected green: the rest (nothing runs on the baseline: `no-state-changing-git-issued`, `no-agent-dispatched`, `leftover-*`, `no-file-contents-*`, `exactly-one-outcome-line` which sees the refusal line only).

## Pass condition

Every clause holds; each row is a grader of this case.

| Grader | Type | Target |
|---|---|---|
| `asked-nothing` | llm | trace |
| `earlier-block-closed-as-taken-over` | regex | vault/task/count-words.md |
| `exactly-one-outcome-line` | regex | final message |
| `final-line-is-stuck` | regex | final message |
| `final-message-lists-the-tracked-path` | regex | final message |
| `final-message-lists-the-untracked-path` | regex | final message |
| `leftover-change-still-in-the-tracked-file` | regex | reader.py |
| `leftover-note-still-in-place` | regex | scratch-notes.txt |
| `new-block-ends-stuck` | regex | vault/task/count-words.md |
| `no-agent-dispatched` | tool_used | tool calls |
| `no-file-contents-in-the-message` | regex, not_contains | final message |
| `no-file-contents-in-the-record` | regex, not_contains | vault/task/count-words.md |
| `no-state-changing-git-issued` | tool_used | tool calls |

Each regex and tool_used grader was flipped offline against a hand-written pass state and one or more single-fault states in a scaffolded directory (the seeded vault edited through lore's own updates), before any arm ran. The `llm` grader was reviewed by reading.

## Limitations

- The `asked-nothing` grader, where present, reads only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- The sweep is graded by its observable consequences (stuck line, paths, files still in place, no state-changing git command, no dispatch); the order of the close against the sweep is not graded.
- The seeded leftover is the only dirt, so a run that stashes or commits it to proceed is the failure the case pins; `no-state-changing-git-issued` also trips on a harmless `git add`, which the procedure has no reason to run.

## Amended after the treatment run

2026-11-16: the `no-state-changing-git-issued` grader matched across the whole Bash input, so words in a `description` could trip it. The fault was in the grader, not in the bar: it now matches only inside the `"command"` string. The bar itself is unchanged.
