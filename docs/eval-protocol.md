# Eval protocol — measuring what a tool's prose actually causes

A skill, agent definition, ruleset, or template is **source, not behaviour**. The
testing ruleset bars asserting on its text, and `scripts/inert-test-gate` enforces
that: a test that reads a markdown file and checks a sentence is present has
observed that someone typed it, not that an agent read it, understood it, or
obeyed it — which was the entire question.

That leaves exactly two honest ways to test prose. The first is to **run it
through its consumer** — the composer, the manifest loader, the front-matter
parser — and assert on what the consumer produced. Cheap, mechanical, and where
most prose testing belongs; the tool suites already do this.

This document is about the second: **run the agent against a scenario and judge
the outcome**. It is expensive, so it is reserved for the instructions that
actually decide something.

## Why these are run by hand

`claude plugin eval` is the automated harness these cases belong in, and it is
**not runnable for this account**. `claude plugin eval --help` resolves and prints
full flag documentation, but every execution path — `init`, `init --bare`, and a
direct case run — returns `plugin eval is currently in early access` and exits.
Enablement is per-organization with no self-service path.

Each case is therefore authored in the shape a real eval case reuses verbatim — a
fixture on disk plus a written expected verdict — and dispatched in session until
the harness opens up. Nothing about the authoring changes when it does; only the
dispatch does.

## Corpus layout

Each tool keeps its cases under its own plugin root, so they travel with the prose
they measure:

```
tools/<tool>/plugins/<tool>/evals/<case-name>/
    expected.md      the pass condition, written BEFORE any arm is run
    fixtures/        self-contained inputs the run is pointed at
    arms/            optional: edited copies of prose, for a treatment arm
tools/<tool>/MANUAL-EVAL.md   the results log for that tool's cases
```

`expected.md` is the load-bearing artifact. Writing it first is what stops a
result being retrofitted into a pass — a verdict authored after seeing the runs
is not evidence, and there is no way to tell the two apart later.

## How an arm is dispatched

Both arms point a **generic read-only agent** at two file paths: the prose to run
as its operating instructions, and the fixture to run it against. The baseline arm
points at the committed prose; the treatment arm points at the edited copy under
`arms/`. The arms then differ in exactly one variable.

**Do not dispatch `<tool>:<agent>` for the treatment arm.** That subagent type
resolves to the live composed install, not the worktree, so it re-runs unedited
prose and reports a false result. Editing the composed install to work around this
is barred by Axiom 6 (never touch the developer's real install from a test).

### A subagent inherits the ruleset you are trying to measure

Dispatching both arms as subagents **cannot measure a user-level ruleset**, which
is what most of these cases are about. A subagent inherits `~/.claude/rules/`, so
if the prose under test lives there, the baseline arm is already carrying it and
both arms are the treatment arm. A probe agent told to use no tools will quote the
ruleset back verbatim; run that probe before trusting any baseline.

Dispatch each arm as its own `claude` process with `--setting-sources project`,
which drops user-level settings and rules while leaving authentication intact.
Pass the arm's prose with `--append-system-prompt` and enumerate tools with
`--allowedTools` — a blanket `--permission-mode bypassPermissions` is refused by
the auto-mode classifier, correctly.

Two consequences, both of which belong in the case's limitations:

- The clean room has **no PreToolUse hook**, because hooks are installed into
  user-level settings. For a guardrail that claims to work in harnesses without
  hooks, that is the faithful condition; for anything else it removes a
  protection production has.
- Grade an `Edit`-tool write separately from a shell write. They say different
  things about a system whose hook covers one and not the other.

### The clean room is not a sandbox

`--setting-sources project` isolates **settings, not the filesystem**. It keeps a
user-level ruleset out of the baseline arm, and that is all it does. An arm with
shell access can still read the developer's real repos, find the installed plugin
cache under `~/.claude/plugins/`, and invoke the genuine CLI against live data.

Both cases written so far escaped their fixture this way. `bash-write-gate`
mutated the developer's real lore config; `publish-routing` published into a real
vault and pushed the commit to origin. In each case the agent behaved sensibly —
it was handed a task it could not complete the sanctioned way, so it went and
found the machinery that would work. A stub on `PATH` is a decoy, not a boundary.

Until these cases run under real isolation — a container, or at minimum a
scrubbed `PATH` and `HOME` with the plugin cache and real vaults out of reach —
treat every run with shell access as capable of touching live infrastructure:

- **Diff the developer's real state after every batch.** `~/.config/`, the real
  vaults, and any repo the fixture names. Escapes are silent; nothing in the run
  output announces them, and the arm will report success either way.
- **Grade from the fixture's filesystem, not from a log line.** A run that
  reports a URL may have published somewhere else entirely. Both cases produced a
  first grading pass that was wrong because it trusted captured output over the
  tree on disk.
- **Never point a fixture at a real vault, repo, or config**, even read-only. The
  fixture is data; live infrastructure is not part of it.

### A fixture must let the sanctioned path succeed

If a case forbids one mechanism, the fixture has to make the permitted mechanism
actually work. A task that cannot be completed the sanctioned way does not
measure obedience — it measures resourcefulness, and a compliant agent becomes a
determined one. In the `bash-write-gate` case the fixture vault was deliberately
unregistered, so agents went looking for the config that would register it, read
the developer's real repo, and mutated the real `~/.config/lore/config.json`.

Give the run a throwaway config it can succeed against (`XDG_CONFIG_HOME` and
`XDG_STATE_HOME` under the scratch directory), and diff the developer's real
config after any run that had shell access. Escapes are silent otherwise.

**The instructions-file path is a trust boundary — pin it.** The dispatcher
resolves that path itself, and it must always name a trusted, review-gated,
in-repo artifact — a committed agent or skill file, ideally at a stated SHA. It
must **never** be taken from the fixture, a spec body, a lore record, a label, or
any other value an untrusted party can write. That file becomes the agent's
operating instructions verbatim, so a path sourced from untrusted input is
instruction injection with extra steps. `claude plugin eval`, which this shape is
written to be reused by, is documented as *not* an OS sandbox: network is
unblocked and there is no path jail. The fixture path is data and may vary; the
instructions path may not.

## Reading a result honestly

These are the rules that make a recorded result worth trusting later. Each exists
because ignoring it produced a wrong read at least once.

- **Run the baseline too.** A treatment arm that scores 3/3 proves nothing without
  a baseline, because the behaviour may already have been there. A change measured
  only after the fact has measured nothing.
- **More than one run per arm.** Agent behaviour varies between runs. A single run
  is an anecdote; the failure mode that matters is usually *inconsistency*, which
  one run cannot show.
- **Check the fixture for contamination.** If the fixture reproduces the prose's
  own worked example, a run can score full marks by recall rather than reasoning.
  Vary the surface cues against the answers so surface form carries no
  information, and confirm the fixture is self-contained — no `[[wikilink]]` or
  cross-reference that resolves to a real record.
- **Record authoring errors as authoring errors.** When a run disagrees with the
  expected verdict and the run is right, that is a defect in the test material,
  not a result. Say so in the log rather than quietly repairing the fixture.
- **State the limitations.** Number of fixtures, number of runs, and the one model
  tier they ran on. A result that does not say what it does not cover will be read
  as covering more than it does.

## Corpus hygiene

`trailhead/tests/test_eval_corpus.py` runs over every tool: each case directory
must carry an `expected.md` and a non-empty `fixtures/`, and each `expected.md`
must state what is under test and its pass condition. That is structure only — it
cannot tell you a case is worth running, and it deliberately does not try.

A tool with no cases yet is not a failure. An empty corpus is an honest report
that nobody has written an eval for that tool.
