# distill-sweep-stops-when-lore-has-no-distill-queue

## Under test

When `lore distill-queue` cannot run (an older lore: unknown-command error, non-zero exit, no `lore distill-queue — ` first line), the `/craft:distill` sweep stops before anything is queued and says to update lore (`trailhead update`). It does not report an empty queue, does not fall back to deriving the queue by hand, and does not proceed to clustering. The prose under test is `skills/distill/SKILL.md`, section "Sweep mode — building the queue". The unedited prose has no such stop: it never calls the command, so it fails the first clause.

## Fixture

The scaffold of `distill-sweep-builds-its-queue-from-lore` with `ARM=unknown`: the same seeded vault (so a hand re-derivation would find specs to queue), and `./lore` is a wrapper that answers `distill-queue` with an argparse-style unknown-command error on stderr, exit 2, empty stdout, and runs the real lore otherwise.

## Conditions, sample and bar

As the main case: `--ablation none`, `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`; a run passes only at 1.0; 3 of 3 after the change; one rerun of a failed batch permitted, stated before any run. Baseline on the unedited prose before any edit. Tier: `merge_only`. This arm restates a council Critical (a failure read as an empty queue) as its own graders.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The sweep tried `lore distill-queue` | `ran-distill-queue` | `tool_used`, Bash, min 1 |
| It did not enumerate specs by hand | `did-not-search-specs-by-hand` | `tool_used`, Bash, `kind:spec`, max 0 |
| It did not proceed to clustering: no `lore task graph` and no `lore record show` | `did-not-cluster` | `tool_used`, Bash, max 0 |
| No record was written or changed | `wrote-no-record` | `tool_used`, Bash, max 0 |
| The final message says the sweep stopped, tells the user to update lore (`trailhead update`), and does not report an empty queue or "nothing waiting" | `stops-and-says-update-lore` | `llm`, `focus: trace` |

## Limitations

One phrasing of the unknown-command error, one model tier, three runs. `lore search` for non-spec kinds is not graded.
