# lore eval corpus

Behavioural eval cases for lore's own prose — the vault CLI, its record taxonomy, and the librarian/investigator agents.

Each case is a directory here:

```
<case-name>/
    expected.md      the pass condition, written BEFORE any arm is run
    fixtures/        self-contained inputs the run is pointed at
    arms/            optional: edited prose copies, for a treatment arm
```

The authoring rules, the dispatch shape, and the trust boundary on the
instructions path are in `docs/eval-protocol.md` — read it before adding a case.
Results go in `tools/lore/MANUAL-EVAL.md`.

## Two case shapes

**Hand-dispatched** (`bash-write-gate`, `carve-out-does-not-generalise`): the shape
above, run in session per `docs/eval-protocol.md`. Their `fixtures/` hold a script
(`make-fixture-vault.sh`) and task prompts the operator dispatches by hand.

**Runner cases**: run by `claude plugin eval` against a throwaway vault the case
seeds for itself.

```
<case-name>/
    case.yaml        schema_version "1.1", name, context: {scaffold_script: fixtures/scaffold.sh}
    prompt.md        the task, with the cue to run ./lore (below)
    graders/         one file per grader
    expected.md      the pass condition, written BEFORE any run
    fixtures/
        scaffold.sh      a copy of ../../scaffold.sh, unchanged
        records.json     the records to seed
```

The runner does not discover `fixtures/scaffold.sh` on its own; `case.yaml` must
name it.

### Seeding the vault

`scaffold.sh` (the template in this directory) finds the repo seven directory
levels above its own `fixtures/` directory, so it works unchanged when copied to
`<case>/fixtures/scaffold.sh`. It runs `tools/lore/scripts/seed-eval-vault` on the
sibling `records.json`, using the repo's `.venv/bin/python` when there is one.

`records.json` is a JSON list of `{"kind", "title", "status", "body"}` objects.
A record's name is lore's slug of its title, so `"Use the queue"` as an `adr` is
`adr/use-the-queue`. Each record is created directly at its declared status.
A spec's criteria are only recognised as a `## Acceptance Criteria` heading
followed by top-level `- **AC1.** text. *Verified by: manual check.*` bullets.

A record may also carry `"related": ["KIND=NAME", ...]`: each entry becomes one
`--related KIND=NAME` link on that record, so `"related": ["decision=use-postgres"]`
links the record to `decision/use-postgres`. `NAME` is the slug lore derives from
the linked record's title, and that record must be listed earlier in the file; a
link naming no earlier record of that kind, a `related` that is not a list, an
entry that is not `KIND=NAME`, and any field other than `kind`, `title`, `status`,
`body` and `related` are refused before anything is written.

The seeder runs in the run's temp `HOME` and working directory, and writes there:
the vault at `./vault`, an executable `./lore` beside a copy of the repo's
`trailhead/` package, and, only when `~/.gitconfig` does not exist, a synthetic
one (lore stamps provenance from the git identity and refuses to write without
one). The runner pre-populates `~/.gitconfig` with its own identity; an existing
file is left byte-for-byte untouched and its identity is what lore stamps. It
refuses, writing nothing, if that file leaves `git config --global user.email`
empty, or unless lore's config and state directories (overrides such as `XDG_*`
and `LORE_*_DIR` included) resolve inside `HOME` and no lore config exists yet,
so it cannot be pointed at a live install.

### The prompt must say `./lore`

The agent's sandbox cannot read the operator's home, so a bare `lore` on `PATH`
exits 127 there. The `./lore` wrapper runs. Every prompt tells the agent to run
`./lore` wherever a document says `lore`, identically in every arm. The tool grant
for it is the operator's `--allow-tools 'Bash(./lore *)'` on the run command
below; nothing in the case files grants it.

### Case files

`case.yaml`:

```yaml
schema_version: "1.1"
name: <case-name>
runs: 3
context:
  scaffold_script: fixtures/scaffold.sh
```

- `runs` (1 to 50, default 3) sits at the top level of `case.yaml`; `--runs`
  overrides it.
- `model`, `max_turns` (default 10), `timeout_seconds` (default 300) and
  `allowed_tools` go under `execution:` in `case.yaml`, or in `prompt.md`
  frontmatter, which overrides `case.yaml`.
- `allowed_tools` only lists read-only tools the case wants (`Read`, `Glob`,
  `Grep`, `Skill`, `Agent`, ...). `Bash`, `Write` and `Edit` are removed from the
  session unless the operator passes them with `--allow-tools`, which applies to
  every case in the run.
- `scaffold_script` runs in the empty workspace before the agent starts, only
  with `--scaffold`, with `HOME` set to the run's temp home. It has a 120 second
  limit; a non-zero exit or a timeout scores that run 0.

`prompt.md`: the body is the task, sent verbatim. Optional frontmatter:

```markdown
---
model: claude-opus-5-5
max_turns: 20
allowed_tools: [Read, Glob, Grep, Skill]
---
```

`graders/<name>.md`: one file per grader; the frontmatter `type` plus the type's
options (and optional `weight`). A run's score is the fraction of graders that
pass. Paths are relative to the run's working directory, which holds `vault/`
and `./lore`.

```markdown
---
type: regex
pattern: Superseded by
match: not_contains
target:
  source: file
  path: vault/adr/<slug>.md
---
```

```markdown
---
type: file_exists
path: vault/decision/*.md
---
```

`file_exists` counts only files the agent created during the run, so it cannot
see seeded records; grade a seeded record's contents with `regex` over a
`source: file` target instead.

`regex` with `source: file` does not glob: a `path` containing `*` fails the
grader whether or not a file matches, so name the exact path. `file_exists` does
glob, which makes it the grader for a record whose name the agent picks.

### Running a batch

```sh
out=$(mktemp -d)
env -u LORE_CONFIG_DIR -u LORE_STATE_DIR -u XDG_CONFIG_HOME -u XDG_STATE_HOME \
  claude plugin eval tools/lore/plugins/lore --case '<glob>' --scaffold \
  --allow-tools 'Bash(./lore *)' --ablation none --trust-plugin \
  --runs N --model claude-opus-5-5 --json "$out/run.json" --max-cost-usd N \
  --output-dir "$out" --no-publish
```

- `--scaffold` is required; the runner skips `scaffold_script` without it.
- `--case` takes one glob. Given more than once, only the last one runs, so run
  one batch per glob.
- `--output-dir` also keeps `trailhead/tests/test_eval_corpus.py` green: without it the runner
  writes `evals/results/<ts>/`, which the corpus test treats as a case with no `expected.md`.
- `--allow-tools` is variadic and takes every following value until the next
  flag, so keep it before another flag, as above.
- `--ablation none` runs the with-plugin arm only. The default, `with-without`,
  adds a no-plugin baseline arm and doubles the cost.
- `--runs N` sets runs per case; `--max-cost-usd N` stops starting new runs once
  the list-price cost estimate reaches N (exit 2, partial results); `--model`
  pins the agent's model so a model change is not mistaken for a prose change.
- `--json <path>` (ending in `.json`) writes the result document: per-run
  scores, cost and model, for the results log. It also holds the prompts and grader
  explanations, so write it into the same temp directory as `--output-dir`, as above,
  and never commit it. It carries each run's `tracePath` but not the trace: the runner
  deletes the trace when the run ends. To read a run's transcript, copy
  `/tmp/claude-eval-*/**/*.jsonl` while the run is still going.
- Unsetting the lore and XDG overrides is belt and braces: the seeder already
  refuses an override that points outside `HOME`.
- `--no-publish` always. Without it the runner publishes its report to claude.ai.
- `--trust-plugin` only for first-party branches you wrote or reviewed.
- Results stay untracked: never commit `results/` or the `--output-dir`.
- Real batches do not set `LORE_PUBLISH_DISABLE`, so lore's publish worker runs
  against the remoteless throwaway vault; it commits locally and skips the push.

### Snapshot the live lore state around every batch

Before and after a batch, snapshot `~/.local/state/lore/` and `~/.config/lore/`
(for example `find ... -type f -exec sha256sum {} +` into a file, then `diff`).
Any difference is a hard stop. The one exception: every changed path is
attributed to a commit made by another session, and that commit carries none of
the batch's fixture titles.
