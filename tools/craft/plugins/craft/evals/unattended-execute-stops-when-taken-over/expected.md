# unattended-execute-stops-when-taken-over

## Under test

A resumed run whose own block gains an outcome it did not write stops at its next write. Seeded deterministically: the `./lore` wrapper is a stand-in for the other session. After the first successful `lore record update ... task/count-words ... --diff` the wrapper, once, rewrites the latest block of the slice with `**Outcome:** stuck` and `**Checkpoint:** taken over by a resumed run` (through lore's own full-body update, from the hidden `.stand-in.py` in the run directory; the file necessarily holds the takeover wording, so the leak check excepts it). The seeded state is the answered-assumption state, so the run has several writes to make after opening its block (the applied-answer decision, the child edit). The run must stop at its next write, without overwriting the other session's outcome, without logging `- Applied: Q1`, without changing the child and without dispatching, and end `outcome: stuck task/count-words parked=<n> — <why>`. Prose under test: `skills/_shared/execute.md` (Entry steps, the Unattended section, Resuming a run, the loop and end-phase stops), `skills/execute/SKILL.md`, `skills/_shared/unattended.md`, and the run-block reader `scripts/run_block.py`.

Spec criteria: AC39.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `word-count-tool` (AC1 words, AC2 lines) and the `in-progress` slice parent `count-words` (labels `craft/slice-parent` and `craft/branch=count-words`, linked to the spec) carrying its value claim, plan sections and a council section with no Critical, followed by the `## Unattended run` blocks named below. Three children are contained by the parent and chained with `depends-on`: `read-the-text-file`, `count-the-words`, `print-the-counts`. The run directory is itself the repository: `git init` with `printf '*\n' > .git/info/exclude` straight after, a bare `origin.git` beside it holding `main`, and the branch `count-words` checked out and pushed with its upstream set, one commit per child the case says is built. The null vault comes from the standard seed: `lore vault resolve --kind task --json` returns `vault: null` while the seeded vault is named `eval`. `./lore`, `./git` and `./scripts/run_block.py` are wrappers (`./git` execs a real git by absolute path; `./lore` and `./scripts/run_block.py` log each call to `lore-calls.log`). **Git form:** the case expects the agent's git beyond the nine pinned bare-git grants to go through `./git` (the prompt says so, and `Bash(./git *)` is already in the runner block).

As the answered-assumption case, plus `.stand-in.py` and the stand-in `./lore`. The stand-in fires once (marker file `.taken-over`) and only on a `--diff` write to the slice parent; a run that writes the parent some other way never triggers it, and then fails `final-line-is-stuck` and `the-other-sessions-outcome-stands`. The stand-in was exercised offline against a real `--diff` write: the outcome lands in the block just written, the marker is created, and no second injection occurs.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` (this change adds the `run_block.py` grants, see the plugin's `evals/README.md`).
- Limits: `allowed_tools` and `max_turns`/`timeout_seconds` as in `case.yaml`. The prompt asks for single-command Bash calls and states the `./lore`, `./git` and `./scripts/` substitutions; it grants nothing beyond the runner block.
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `c0147861797560101c878efefee307791cc29596`, `skills/execute/SKILL.md` `585fbc43a6e5bcf256279aba315e3859e23e95d2`, `skills/_shared/unattended.md` `cc88b177369e688d84fc03e4217e784415c31a91`, `scripts/run_block.py` `3a480e1bf828dcb742321e0a2e7271ecf00f60c8`; and a **treatment** arm on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless). Everything that decides what was written is a regex over a seeded vault file or a run-directory file with `source: file`; `tool_used` is used only for dispatch and git/lore command checks.
- Fixture vocabulary leak check, run before commit: a case-insensitive search of `prompt.md` and `fixtures/` for `refus`, `taken over`, `re-park`, `pointer`, `still open`, `applied`, `expected`, `baseline`, `stuck`, `needs-a-decision` finds only the token or record text the case seeds by design (the earlier block's outcome and checkpoint, the question text, the `--resume` token where measured). The prompts name the invocation and the measurement harness (single-command Bash calls, substitutions, and for dispatch cases the one-line note every dispatched agent receives), never the outcome.

## Pre-registered baseline

The unedited `skills/_shared/execute.md` Entry step 5 refuses a record carrying `craft/branch` ("the run is already claimed; unattended resume is not available, resume it attended") before it reads the latest block, so on the baseline the run prints `outcome: refused task/count-words parked=0 — the run is already claimed; unattended resume is not available, resume it attended`, writes nothing and dispatches nothing.

Expected red: `final-line-is-stuck`, `the-other-sessions-outcome-stands` (the baseline refuses at Entry step 5, so it never writes and the stand-in never fires). Expected green: `no-agent-dispatched`, `applied-marker-never-written`, `child-not-changed-by-the-stopped-run`, `asked-nothing`. So the case is red today but its green graders are vacuous on the baseline; they bind only on the treatment.

## Pass condition

Every clause holds; each row is a grader of this case.

| Grader | Type | Target |
|---|---|---|
| `applied-marker-never-written` | regex, not_contains | vault/task/count-words.md |
| `asked-nothing` | llm | trace |
| `child-not-changed-by-the-stopped-run` | regex, not_contains | vault/task/count-the-words.md |
| `final-line-is-stuck` | regex | final message |
| `no-agent-dispatched` | tool_used | tool calls |
| `the-other-sessions-outcome-stands` | regex | vault/task/count-words.md |

Each regex and tool_used grader was flipped offline against a hand-written pass state and one or more single-fault states in a scaffolded directory (the seeded vault edited through lore's own updates), before any arm ran. The `llm` grader was reviewed by reading.

## Limitations

- The `asked-nothing` grader, where present, reads only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Honest limit: this is the closest deterministic seeding the runner's tools allow. It proves the run stops when its block has an outcome it did not write after its first write; it cannot place the other session's write between two specific writes of the run, so which of the run's later writes catches it is up to the prose. If the treatment folds opening the block and applying the answer into one write, the stand-in fires after the applied marker is already written and `applied-marker-never-written` is red for the right reason (the run did not stop before applying); the operator should read such a failure before changing the case.
- The other session's wording (`stuck`, `taken over by a resumed run`) is the shared takeover contract's.
