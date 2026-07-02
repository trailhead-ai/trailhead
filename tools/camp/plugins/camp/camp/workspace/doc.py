"""Write camp-owned workspace docs at the workspace root.

The filenames written are determined by harness.doc_files in the group config
(via resolve_harness_profile(...).doc_files). The claude default (no [harness]
block, or block without doc_files) writes only CLAUDE.md. A Codex/Copilot/Cursor
harness can configure
doc_files = ["AGENTS.md"] to write AGENTS.md instead.

All files are written idempotently — calling write_workspace_doc twice with the
same inputs produces identical output with no duplication or appending.

The doc embeds:
  - the member list (each member name, in a ## Members bulleted block)
  - a verbatim, invocable command table with exact strings:
      camp activate <member>   -- activate a member for the current session
      camp status              -- check provisioning status
      camp setup               -- retry failed/pending provisioning
  - guidance that members are INERT until `camp activate <member>`
  - guidance that setup may be in flight (background provisioner)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..harness.profile import resolve_harness_profile


def _render_doc(
    group: dict[str, Any],
    slug: str,
) -> str:
    """Render the workspace doc content.

    The content is deterministic for the same group + slug inputs.
    """
    group_name = group["group"]["name"]
    members = group["members"]
    member_names = [m["name"] for m in members]
    member_list = "\n".join(f"  - {name}" for name in member_names)

    return f"""\
# Camp Workspace: {slug}

**Group:** {group_name}
**Workspace slug:** {slug}

## Members

{member_list}

## Important: Members are INERT until activated

Each member worktree is provisioned in the background and is **inert until
you explicitly activate it** with `camp activate <member>`. Do not attempt to
work in a member directory until you have activated it.

Setup may be in flight (background provisioner is running). Check status
before acting on any member.

## Commands

| Command | Purpose |
|---------|---------|
| `camp activate <member>` | Activate a member for the current session |
| `camp status` | Check provisioning status (exit 0=ready, 2=pending, 3=failed) |
| `camp setup` | Retry failed or pending member provisioning |

## Workflow

1. Run `camp status` to see which members are ready.
2. If setup is still in flight (pending), wait or run `camp setup`.
3. Run `camp activate <member>` to activate a member — this prints its CLAUDE.md
   and marks it active for the session.
4. Work in the activated member's worktree directory.
"""


def write_workspace_doc(
    workspace_dir: Path,
    group: dict[str, Any],
    slug: str,
    *,
    profile: Any | None = None,
) -> None:
    """Write workspace doc file(s) at the workspace root.

    The filenames written come from the resolved HarnessProfile's doc_files
    (defaults to ["CLAUDE.md"] for the claude harness). The caller may pass the
    once-resolved profile; otherwise it is resolved from group here. All files
    receive identical rendered content. Idempotent: files are fully rewritten on
    each call, so the result is always stable for the same inputs.

    Args:
        workspace_dir: Absolute path to the workspace root directory.
        group:         Parsed group config dict.
        slug:          The workspace slug (used in the doc header).
        profile:       Optional once-resolved HarnessProfile.
    """
    if profile is None:
        profile = resolve_harness_profile(group)
    content = _render_doc(group, slug)
    for filename in profile.doc_files:
        (workspace_dir / filename).write_text(content)
