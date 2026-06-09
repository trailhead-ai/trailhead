# Lore Roadmap

Tiers capture deferred work in priority order. Tier 1 is shipped. Items within
a tier are roughly ordered by value, not necessarily by implementation order.

## Tier 1 (shipped)

Session lifecycle, five capture skills, subsystem recall via branch-keyword
matching, `lore` CLI, pre-commit status guard, vault-sync, and the
lore-librarian agent. See the README for the full feature list.

## Tier 1.5 — Near-term additions

### UserPromptSubmit classifier for live recall

Today, subsystem recall fires once at SessionStart based on the git branch
name. A UserPromptSubmit hook could re-run recall mid-session when the user's
prompt contains a subsystem keyword — loading the profile at the moment it
becomes relevant rather than only at session start.

Implementation note: UserPromptSubmit hooks run on every prompt and add latency.
The classifier must be cheap:

- **Primary path (macOS):** inline keyword scan against the loaded subsystem
  keyword map — zero network, sub-millisecond.
- **Portable fallback (non-macOS or no match):** same keyword scan; a Haiku
  call for richer semantic matching is optional and must be gated behind an
  opt-in flag (`$LORE_SMART_RECALL=1`) to avoid per-prompt LLM cost for
  adopters who don't want it.

The branch-name recall at SessionStart is the safe default; this tier adds
in-session refinement.

### Worktree derivation reconciliation (watch item)

`cli/lore` derives the current worktree name from `Path.cwd().name`. The
SessionStart hook uses `CLAUDE_PROJECT_DIR or cwd`. These agree in normal use
but may diverge when:

- Claude Code is opened from a directory that is not the git worktree root.
- `CLAUDE_PROJECT_DIR` is set to a path whose basename differs from `cwd`.

Before shipping the UserPromptSubmit classifier (which also needs a stable
worktree identity), audit that both paths produce the same worktree name in the
common non-standard launch scenarios and reconcile where they don't.

## Tier 2 — Decoupled rituals

Port the remaining session rituals as standalone lore skills. Each is currently
coupled to a specific project's stack; the port strips those dependencies and
makes them vault-generic.

- **`/lore:handoff`** — snapshot in-flight state, set session `status: shelved`,
  leave instructions for the next pickup.
- **`/lore:pickup`** — load a shelved session note, restore context, flip status
  back to `active`.
- **`/lore:morning-briefing`** — synthesize what changed in the vault since the
  last session; surface open deferred items and active radar entries.
- **`/lore:monthly-reflection`** — periodic review: what shipped, what's been
  deferred the longest, what patterns recur in lessons/dead-ends.
- **`/lore:brain-review`** — quality review of a harvest candidate before
  promoting it from `harvest-pending.md` into its permanent directory.

These are lower priority than the classifier because the core capture +
lifecycle loop is already functional without them.

## Tier 3 — Optional semantic search

Add an opt-in semantic-search MCP for the vault. Adopters who want
natural-language search over note bodies (beyond the keyword + Grep approach)
can spin up a local embedding index.

Design constraints:
- Must remain opt-in — lore's MCP-free baseline is a feature, not a gap.
- The MCP must be independently installable and not required for any Tier 1 or
  Tier 1.5 functionality.
- Local-first: no cloud embedding APIs by default; support at minimum
  `ollama` or `llama.cpp` for the embedding model.

Implementation is deferred until there is clear demand and at least one
adopter-validated use case that keyword + Grep cannot serve.
