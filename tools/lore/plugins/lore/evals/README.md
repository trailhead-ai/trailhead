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

The seeder runs in the run's temp `HOME` and working directory, and writes there:
a synthetic `~/.gitconfig` (lore stamps provenance from the git identity and
refuses to write without one), the vault at `./vault`, and an executable `./lore`
beside a copy of the repo's `trailhead/` package. It refuses, writing nothing,
unless lore's config and state directories (overrides such as `XDG_*` and
`LORE_*_DIR` included) resolve inside `HOME` and no lore config or `~/.gitconfig`
exists yet, so it cannot be pointed at a live install.

### The prompt must say `./lore`

The agent's sandbox cannot read the operator's home, so a bare `lore` on `PATH`
does not run there. The `./lore` wrapper does. Every prompt tells the agent to run
`./lore` wherever a document says `lore`, identically in every arm, and
`case.yaml` grants `Bash(./lore *)`.

### Running a batch

```sh
env -u LORE_CONFIG_DIR -u LORE_STATE_DIR -u XDG_CONFIG_HOME -u XDG_STATE_HOME \
  claude plugin eval tools/lore/plugins/lore --scaffold --no-publish \
  --output-dir "$(mktemp -d)" --case '<case-name>' [--trust-plugin]
```

- `--scaffold` is required; the runner skips `scaffold_script` without it.
- Unsetting the lore and XDG overrides is belt and braces: the seeder already
  refuses an override that points outside `HOME`.
- `--no-publish` always. Without it the runner publishes its report to claude.ai.
- `--trust-plugin` only for first-party branches you wrote or reviewed.
- Results stay untracked: never commit `results/` or the `--output-dir`.

### Snapshot the live lore state around every batch

Before and after a batch, snapshot `~/.local/state/lore/` and `~/.config/lore/`
(for example `find ... -type f -exec sha256sum {} +` into a file, then `diff`).
Any difference is a hard stop. The one exception: every changed path is
attributed to a commit made by another session, and that commit carries none of
the batch's fixture titles.
