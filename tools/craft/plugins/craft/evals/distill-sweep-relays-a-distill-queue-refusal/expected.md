# distill-sweep-relays-a-distill-queue-refusal

## Under test

When `lore distill-queue` exits 1 on a `lore: ...` refusal line (a vault that could not be read), the `/craft:distill` sweep stops and relays that line as printed, including its fallback (`/craft:distill spec/<name>`). It does not report an empty queue, derive the queue by hand, or proceed to clustering. The prose under test is `skills/distill/SKILL.md`, section "Sweep mode — building the queue". The unedited prose never calls the command, so it fails the first clause.

## Fixture

The scaffold of `distill-sweep-builds-its-queue-from-lore` with `ARM=refusal`: the same seeded vault, and `./lore` is a wrapper that answers `distill-queue` with the refusal line `lore: vault scratch could not be read, so the distill queue is refused rather than built partial; repair or remove the vault to sweep again, or distill one spec by name with /craft:distill spec/<name>` on stderr, exit 1, empty stdout, and runs the real lore otherwise.

## Conditions, sample and bar

As `distill-sweep-stops-when-lore-has-no-distill-queue`. Tier: `merge_only`. This arm restates the same council Critical as its own graders.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The sweep tried `lore distill-queue` | `ran-distill-queue` | `tool_used`, Bash, min 1 |
| It did not enumerate specs by hand | `did-not-search-specs-by-hand` | `tool_used`, Bash, `kind:spec`, max 0 |
| It did not proceed to clustering | `did-not-cluster` | `tool_used`, Bash, max 0 |
| No record was written or changed | `wrote-no-record` | `tool_used`, Bash, max 0 |
| The final message says the sweep stopped, relays the refusal (naming vault `scratch` and the `/craft:distill spec/<name>` fallback), and does not report an empty queue or "nothing waiting" | `stops-and-relays-the-refusal` | `llm`, `focus: trace` |

## Limitations

One refusal text, one model tier, three runs.
