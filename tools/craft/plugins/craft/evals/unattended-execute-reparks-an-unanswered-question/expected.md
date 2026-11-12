# unattended-execute-reparks-an-unanswered-question

## Under test

A claimed slice whose latest block ended `needs-a-decision` with two parked questions in one block: Q1 answered `(a)`, Q2 unanswered. Both concern the one unfinished child `print-the-counts`, and Q2's progress line says that child cannot be built until it is answered. The resumed run applies Q1 (logged as `- Applied: Q1`), re-parks Q2 as exactly one pointer line `- Q2: still open — answer goes under the original Q2 entry` in its own block (not a second Q2, not a Q3), dispatches nothing, closes its block `needs-a-decision` and prints `outcome: needs-a-decision task/count-words parked=1` as the last line. Prose under test: `skills/_shared/execute.md` (Entry steps, the Unattended section, Resuming a run, the loop and end-phase stops), `skills/execute/SKILL.md`, `skills/_shared/unattended.md`, and the run-block reader `scripts/run_block.py`.

Spec criteria: AC45.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `word-count-tool` (AC1 words, AC2 lines) and the `in-progress` slice parent `count-words` (labels `craft/slice-parent` and `craft/branch=count-words`, linked to the spec) carrying its value claim, plan sections and a council section with no Critical, followed by the `## Unattended run` blocks named below. Three children are contained by the parent and chained with `depends-on`: `read-the-text-file`, `count-the-words`, `print-the-counts`. The run directory is itself the repository: `git init` with `printf '*\n' > .git/info/exclude` straight after, a bare `origin.git` beside it holding `main`, and the branch `count-words` checked out and pushed with its upstream set, one commit per child the case says is built. The null vault comes from the standard seed: `lore vault resolve --kind task --json` returns `vault: null` while the seeded vault is named `eval`. `./lore`, `./git` and `./scripts/run_block.py` are wrappers (`./git` execs a real git by absolute path; `./lore` and `./scripts/run_block.py` log each call to `lore-calls.log`). **Git form:** the case expects the agent's git beyond the nine pinned bare-git grants to go through `./git` (the prompt says so, and `Bash(./git *)` is already in the runner block).

Latest block as above; `read-the-text-file` and `count-the-words` are `done` with commits, `print-the-counts` is `ready` and runnable. `run_block.py` reports Q1 `answered match=option:a applied=no` and Q2 `unanswered match=none`, `highest-q: Q2`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` (this change adds the `run_block.py` grants, see the plugin's `evals/README.md`).
- Limits: `allowed_tools` and `max_turns`/`timeout_seconds` as in `case.yaml`. The prompt asks for single-command Bash calls and states the `./lore`, `./git` and `./scripts/` substitutions; it grants nothing beyond the runner block.
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `c0147861797560101c878efefee307791cc29596`, `skills/execute/SKILL.md` `585fbc43a6e5bcf256279aba315e3859e23e95d2`, `skills/_shared/unattended.md` `cc88b177369e688d84fc03e4217e784415c31a91`, `scripts/run_block.py` `3a480e1bf828dcb742321e0a2e7271ecf00f60c8`; and a **treatment** arm on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless). Everything that decides what was written is a regex over a seeded vault file or a run-directory file with `source: file`; `tool_used` is used only for dispatch and git/lore command checks.
- Fixture vocabulary leak check, run before commit: a case-insensitive search of `prompt.md` and `fixtures/` for `refus`, `taken over`, `re-park`, `pointer`, `still open`, `applied`, `expected`, `baseline`, `stuck`, `needs-a-decision` finds only the token or record text the case seeds by design (the earlier block's outcome and checkpoint, the question text, the `--resume` token where measured). The prompts name the invocation and the measurement harness (single-command Bash calls, substitutions, and for dispatch cases the one-line note every dispatched agent receives), never the outcome.

## Pre-registered baseline

The unedited `skills/_shared/execute.md` Entry step 5 refuses a record carrying `craft/branch` ("the run is already claimed; unattended resume is not available, resume it attended") before it reads the latest block, so on the baseline the run prints `outcome: refused task/count-words parked=0 — the run is already claimed; unattended resume is not available, resume it attended`, writes nothing and dispatches nothing.

Expected red: `exactly-two-blocks`, `applied-q1-is-logged-in-the-new-block`, `q2-is-re-parked-as-a-pointer`, `new-block-outcome-is-needs-a-decision`, `final-line-is-needs-a-decision-parked-1` (the baseline's refusal line is `outcome: refused`, and it opens no block). Expected green: `q2-is-not-defined-a-second-time`, `no-new-question-numbered`, `q1-answer-kept`, `no-answer-written-under-q2`, `exactly-one-outcome-line`, `no-agent-dispatched`, `print-the-counts-still-ready`, `asked-nothing`.

## Pass condition

Every clause holds; each row is a grader of this case.

| Grader | Type | Target |
|---|---|---|
| `applied-q1-is-logged-in-the-new-block` | regex | vault/task/count-words.md |
| `asked-nothing` | llm | trace |
| `exactly-one-outcome-line` | regex | final message |
| `exactly-two-blocks` | regex | vault/task/count-words.md |
| `final-line-is-needs-a-decision-parked-1` | regex | final message |
| `new-block-outcome-is-needs-a-decision` | regex | vault/task/count-words.md |
| `no-agent-dispatched` | tool_used | tool calls |
| `no-answer-written-under-q2` | regex, not_contains | vault/task/count-words.md |
| `no-new-question-numbered` | regex, not_contains | vault/task/count-words.md |
| `print-the-counts-still-ready` | regex | vault/task/print-the-counts.json |
| `q1-answer-kept` | regex | vault/task/count-words.md |
| `q2-is-not-defined-a-second-time` | regex, not_contains | vault/task/count-words.md |
| `q2-is-re-parked-as-a-pointer` | regex | vault/task/count-words.md |

Each regex and tool_used grader was flipped offline against a hand-written pass state and one or more single-fault states in a scaffolded directory (the seeded vault edited through lore's own updates), before any arm ran. The `llm` grader was reviewed by reading.

## Limitations

- The `asked-nothing` grader, where present, reads only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Both questions are fixed on the one child so there is no independent work left; a run that dispatches an executor for `print-the-counts` despite Q2's progress line fails `no-agent-dispatched`.
