# Circle membership — shared reference

The **circle** is a four-agent review panel. This file is the single source of
truth for who the members are. Both `/forge:consult` and the `planning` skill's
Circle Review step read membership from here and dispatch the agents **directly**
via the Agent tool — neither delegates to the other.

## Members

| Agent | Lens | Reviews for |
|-------|------|-------------|
| `builder`  | Builder     | *How* to build it — architecture, code structure, where it lives, libraries, prior art. |
| `breaker`  | Reliability | How we'll know it works and how it behaves when it doesn't — tests, edge cases, failure modes, recovery, abuse patterns. |
| `attacker` | Security    | Red-team — authn/authz, data handling, secrets, injection, PII, threat model. |
| `advocate` | User Advocate | The end user — UX clarity, error messaging, accessibility, device/platform behavior, where a user gets stuck. |

Each member resolves to `agents/<stem>.md` in this plugin. The stems are exactly:
`builder`, `breaker`, `attacker`, `advocate`.

## Dispatch contract

- Make **four parallel `Agent` tool calls** — one per member — in a single
  message so they run concurrently in **isolated contexts**. Each member answers
  the same question from its own lens and returns only a summary; members do not
  see each other's responses.
- Substitute the lens label (`Builder` / `Reliability` / `Security` / `Advocate`)
  into the per-member prompt.
- The **synthesizer is the main session, never a subagent** — it de-duplicates by
  issue (not by member), groups findings by severity, and notes which lenses
  raised each one.
