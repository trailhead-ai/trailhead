"""Lore's user-level ruleset content.

This module owns the *content* lore installs into a harness's user-level ruleset
(for Claude Code: ``~/.claude/rules/<ruleset-name>.md``).  The trailhead
``Harness`` seam owns the *delivery* (see ``trailhead/harness/base.py``); lore
only supplies the bytes.

``render_ruleset_content()`` assembles two parts, in this fixed order:

  * ``_WRITE_PROHIBITION`` — the mandatory vault-write rules.  The Claude Code
    PreToolUse guardrail blocks Edit/Write/MultiEdit/NotebookEdit but is OPAQUE
    to Bash-mediated writes — these rules are the ONLY protection for that gap,
    and the SOLE guardrail for harnesses without a PreToolUse hook.  The old
    "Drift caveat" paragraph (which described a since-removed per-project
    multi-rules-file model) is intentionally dropped. It MUST stay first: this
    is the load-bearing guardrail and a reorder must never demote it.  It also
    carries the one carve-out — a vault's top-level ``sites/`` directory is a
    free-write zone — because the prohibition and its scope have to be read
    together: stated apart, an agent applies the prohibition to the sites zone
    and refuses a publish the hook and the deny rules both allow.
  * ``PRIMER`` — a short (≤20-line) disposition primer: what lore is, the capture
    disposition (``lore session candidate`` is the default for findings during
    work; ``lore record`` is the direct-write exception for authored artifacts;
    ``/lore:flush`` promotes candidates), and the triggers for reaching for lore.

The content is static: two calls return byte-identical output, which is what
the whole-file drift compare (``user_ruleset_status``) requires to detect
``lore status`` drift.  Nothing else belongs here: per Claude 5-era guidance a
memory file carries concise load-time instructions only, so an exhaustive
command reference is a documentation anti-pattern rather than a guardrail.
"""

from __future__ import annotations

_WRITE_PROHIBITION = """\
## Lore vault — mandatory write rules

**All** lore vault records are written **only** via the `lore` CLI.
**Never** write to vault files by any other means, including:

- Direct file edits (Edit / Write / MultiEdit tools)
- Bash or shell redirection: `> file`, `>> file`, `tee`, `sed -i`, `cp`, `mv`

The Claude Code PreToolUse guardrail blocks Edit/Write/MultiEdit/NotebookEdit
but is **opaque to Bash-mediated writes** — this rule is the **only**
protection for that gap.  Violating it silently corrupts vault records.

For harnesses without a PreToolUse hook (Cursor, Codex, etc.) this block is
the **sole guardrail** — treat it as binding regardless of harness.

**One carve-out — `sites/`:** a vault's top-level `sites/` directory holds
static sites, not records, and is a **free-write zone**: write it with plain
file operations, following the `outpost:publish-site` skill. `lore sync`
distributes it like any other vault content. The carve-out is exactly that one
directory at the top level of a vault — a `sites/` directory anywhere else is
inside a record tree and stays CLI-only, as does everything else in the vault.

Capture and read records via the CLI: `lore session candidate …` to capture
findings during work and `lore record …` to write a durable record directly;
`lore search …` to read. See `lore --help` and the lore skills (`/lore:record`,
`/lore:search`, `/lore:flush`).

Mid-task capture: `lore session candidate` is the **default** — continuous capture
of findings during a session. `/lore:flush` promotes the outstanding candidates
into durable records and finalizes the session. Reserve a direct `lore record`
write for deliberately authored artifacts (`task`, `spec`, `area`) or an explicit
"record this one now"; incidental findings become candidates, not records.
"""

PRIMER = """\
## Lore — agent project memory

Lore is durable, searchable project memory: decisions, dead-ends, deferred
work, follow-ups, area notes, and session history — all in a git-backed vault.

- `lore search …` — read the vault before deciding; check for prior art,
  dead-ends, and decisions on the area you're about to touch.
- `lore session candidate …` — the **default** capture. As findings arise during
  work (a decision, a dead-end, a deferred item, a gotcha), log them to the
  session; they ride the session note and become durable records at flush.
  Capture liberally — judgment happens later.
- `lore record …` — write a durable record directly. Reserved for deliberately
  authored artifacts (`task`, `spec`, `area` profile) or an explicit "record this
  one now". Incidental findings go through a candidate instead.
- `/lore:flush` — promote the session's candidates into durable records.

Reach for lore when: starting work in an unfamiliar area, about to repeat an
approach that may have failed before, making a non-obvious design call, or
setting something aside to revisit later.
"""


def render_ruleset_content() -> str:
    """Render the full user-level ruleset: write-prohibition + primer."""
    return f"{_WRITE_PROHIBITION}\n{PRIMER}"
