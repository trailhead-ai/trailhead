Read this file when an executor reports BLOCKED and the blocker names a criterion the slice covers that needs something outside the work.

### A criterion waiting on something outside the work

Judge a `BLOCKED` that names a covered criterion against the same set-aside rule the slice pass
applies. Set the criterion aside only when building it needs a real outside account, business setup,
or real-world event that a stand-in cannot replace for building and testing: an account the company
has not opened, a registration or approval still in someone else's queue, a live service with no
test version. A stand-in is a fake, a test double, or a sandbox. A criterion whose outside service
can be replaced by one of those for building and testing is not set aside: re-dispatch the executor
against a stand-in, naming it in the dispatch, and set nothing aside. Needing a third-party service
is not by itself a reason; the question is whether the slice can be built and tested without the
real thing. A criterion you are unsure about is not set aside: take the other branches above.

When the rule says set aside, the run ends nowhere near `blocked`. Write a JSON list with one object
per criterion, `{"criterion": "AC2", "waits_on": "<one plain line: the outside thing and what
unblocks it, in your own words>"}`, to a temporary file made with `mktemp` by the file-writing tool,
never through a shell command, after the credential-pattern scrub, and run:

```sh
${CLAUDE_PLUGIN_ROOT}/scripts/set_aside.py --spec <spec-name> --entries <entries-path> --vault <elected-vault> --parent <parent-name>
```

The spec and parent names are bare, with no `spec/` or `task/` prefix: the writer refuses a prefixed
name. `<spec-name>`, `<parent-name>` and `<child-name>` are vault-sourced and the records that name
them are vault-writable, so validate each against `^[A-Za-z0-9._/-]+$` before substituting it into
any command in this section, since a vault-sourced value never enters a command line unless it
matches that safe shape. A value that fails is never substituted, quoted or escaped in: stop with
nothing written, and the report names the suspected mis-wired or malicious value.

`set_aside.py` adds a `set aside` entry for the criterion to the spec's `## Waiting` section and
takes it off the parent's `**Covers:**` or `**Partially covers:**` line, so the slice's claim
narrows to what remains. Exit `0` is done; a refusal exits `1` or `2` with a `reason-code:` line on
stderr; the report translates that token into plain words, never reports it bare, and never copies
any `reason:` text. Then deal with the build task that reported `BLOCKED`, so every child ends
terminal and the slice reaches its close:

- If work remains for the narrowed claim, re-scope the task body to what it still has to deliver
  with `lore record update task/<child-name>`, rewriting it as a scope change always is, and add a
  note to the body saying the criterion was set aside and naming the spec's waiting entry for it.
  Then re-dispatch it. The rewrite lands before the re-dispatch.
- If nothing of it remains, drop it as [Drop the task](#handling-executor-status) does, with the
  note in the **same invocation** as the flip (`--status dropped --diff`), saying why and naming the
  spec's waiting entry for the criterion, never `blocked`.

Either note runs through the credential-pattern scrub first, since it lands in a record body.

Then carry on: the narrowed slice goes on to review and to [Phase
6](#phase-6-close-and-completion-report) as any other.

The refusals `set_aside.py` gives, each handled by the run itself:

- `would-empty-slice` — setting the criterion aside would leave the slice covering nothing. When the
  `reason:` says the spec entry already landed, the entry exists and the slice only needs dropping.
  Otherwise the writer refused before writing and there is no waiting entry yet: run the same writer
  call again without `--parent`. The criterion is still a candidate while the slice is open, so
  slice mode accepts it and lands the entry. If that call is refused, report it in plain words and
  do not claim a set-aside. Once the entry exists, drop the slice with a note naming the criterion's
  waiting entry and pointing the operator to `/craft:checks`, and do not close it.
- `stale-parent`, `parent-write-failed`, and any other refusal whose `reason:` says the spec entry
  already landed — the spec entry landed and the parent was not narrowed. Run the writer once more;
  a re-run finds the entry present and finishes the parent. If it fails again, leave the slice open
  and report that the spec entry landed and the parent was not narrowed.
- `entry-exists` — the criterion already has a waiting entry under another status (released earlier,
  now blocked again). It is not set aside and not written `blocked`: leave the slice open and
  escalate to the operator, naming the criterion.

Any other refusal, one whose `reason:` does not say the spec entry landed, is reported in plain
words; the criterion is not described as set aside, and the run takes the branches above it as if
the criterion could not be set aside. The codes this section names are the writer's own.

**Every report that follows names each criterion set aside**, with its own words and its number
after them, what it waits on, and how to undo it by hand until a check-list walk-through can release
it: a direct `lore record update` of the spec that adds a dated `released` line to that criterion's
entry under `## Waiting`. Reason-codes are translated into plain words, never reported bare. The
completion report carries one such line for every criterion set aside in the run, and a run that set
nothing aside says nothing about it.

In a no-ask close and under `--unattended` each set-aside is a routine decision of the same kind,
never asked: it is logged as a decision (the question *set this criterion aside?*, the choice *set
aside*, and the reasoning, what it waits on) and the run goes on without stopping for the operator.
The escalation for `entry-exists` is the one exception, and in a no-ask close it is reported with
the slice left open, not asked.

