# lore eval corpus

Behavioural eval cases for lore's own prose — the vault CLI, its record taxonomy, and the librarian/investigator agents.

Each case is a directory here, run by `claude plugin eval` against a throwaway vault the
case seeds for itself. The generic case shape, how to write and run one, and the sandbox
are in `docs/eval-protocol.md` — read it before adding a case. Results go in
`tools/lore/MANUAL-EVAL.md`.

## Runner cases and legacy cases

**Runner cases** carry a `case.yaml`; the layout below is what lore's seeded cases add to
the protocol's case shape.

**Legacy cases** (`bash-write-gate`, `carve-out-does-not-generalise`) have no
`case.yaml`. The runner does not run them and the push gate does not gate them. They are
listed in `_LEGACY_HAND_SHAPED` in `trailhead/tests/test_eval_corpus.py`, which is also
where a case is removed once it is ported to a runner shape.

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

The generic fields (`runs`, where `model`, `max_turns`, `timeout_seconds` and
`allowed_tools` go, and which tools need `--allow-tools`) are in
`docs/eval-protocol.md`, "Case shape". `scaffold_script` runs in the empty
workspace before the agent starts, only with `--scaffold`, with `HOME` set to the
run's temp home. It has a 120 second limit; a non-zero exit or a timeout scores
that run 0.

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

`file_exists`, and `regex` and `llm` with `target: files`, see only the files the
agent created during the run, never the ones it modified in place, so a seeded
record and an in-place edit of it are invisible to them. Grade a seeded record's
contents or change with a `regex` on its `source: file` path (its body and its
`.json` sidecar), one grader per record.

`tool_used` counts the agent's tool calls that match:

```markdown
---
type: tool_used
tool: Bash
input_match: '(?=.*record create)(?=.*--kind task)(?=.*spec=<slug>)'
min: 1
max: 1
---
```

`tool` is the tool name; `input_match` is a JavaScript regex tested against the
JSON text of the whole tool input (a heredoc body appears with `\n` escapes, and
a lookahead per required term matches in any order); `min` and `max` bound the
number of matching calls. It reads every call in the run, not a window of the
last events. JSON escapes quote characters in the input, so keep quote characters
out of the pattern.

`regex` with `source: file` does not glob: a `path` containing `*` fails the
grader whether or not a file matches, so name the exact path. `file_exists` does
glob, which makes it the grader for a record whose name the agent picks.

### Running a batch

Run it only on a branch you wrote or reviewed, because `--scaffold` runs the
case's scaffold script as you (see
[what the sandbox does not confine](../../../../../docs/eval-protocol.md#what-the-sandbox-confines-and-what-it-does-not)).

```sh
export TMPDIR=$(mktemp -d ~/.cache/ee.XXXXXXXX)      # Linux only: outside /tmp, short path
out=$(mktemp -d)
timeout SECS env -u LORE_CONFIG_DIR -u LORE_STATE_DIR -u XDG_CONFIG_HOME -u XDG_STATE_HOME \
  claude plugin eval tools/lore/plugins/lore --case '<glob>' --scaffold \
  --allow-tools 'Bash(./lore *)' --ablation none --trust-plugin \
  --runs N --model claude-opus-5-5 --json "$out/run.json" --max-cost-usd N \
  --output-dir "$out" --no-publish
```

What each flag does is in `docs/eval-protocol.md`, "Running a batch"; read its
"Runner gotchas" before a first run. On top of that:

- `timeout SECS` bounds the whole batch from outside, because the runner can
  overrun its own `timeout_seconds`. Size it to the batch: at least the
  matched cases times `--runs` times each case's `timeout_seconds`, plus margin.
- `--json <path>` must end in `.json`. The result carries each run's `tracePath`
  but not the trace: the runner deletes the trace when the run ends. To read a
  run's transcript, copy `claude-eval-*/**/*.jsonl` from the runner's temp
  directory (`$TMPDIR`, or `/tmp` when it is unset) while the run is still
  going.
- Unsetting the lore and XDG overrides is belt and braces: the seeder already
  refuses an override that points outside `HOME`.
- Real batches do not set `LORE_PUBLISH_DISABLE`, so lore's publish worker runs
  against the remoteless throwaway vault; it commits locally and skips the push.
- Diff the live lore state around every batch, as `docs/eval-protocol.md`
  describes in "What the sandbox confines, and what it does not".

### The gate mapping

`eval-gate.json` here maps each runner case to the plugin prose it measures, so
`portage eval-gate plan` can select the cases a branch touches. Each case lists
`measures`: paths relative to the plugin root (the directory holding `evals/`), taken
from the prose its `expected.md` names under "Under test". `skills/correct/SKILL.md` is
measured by all 22 cases; the four that also name deferred-work capture add
`skills/record/SKILL.md`. Prose in another plugin that a baseline arm reads (the craft
prose) is outside the plugin root and cannot be listed. A case with no entry is a
failure: add its entry when you add the case.

The `runner` block holds the grants of the batch command above (`Bash(./lore *)`), the
model `claude-opus-5-5`, and `max_cost_usd` 1.5 per case. The cap is sized from the
logged cost: a 22-case batch costs about 15 USD at list price (0.68 USD a case across
three runs, `tools/lore/MANUAL-EVAL.md`), and the batches above ran each case under
`--max-cost-usd` 1.2 to 1.5 without a stop.
