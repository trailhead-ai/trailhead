"""Write camp-owned workspace docs (CLAUDE.md + AGENT.md) at the workspace root.

Both files are written idempotently — calling write_workspace_doc twice with the
same inputs produces identical output with no duplication or appending.

The docs embed:
  - the member list (each member name)
  - a verbatim, invocable command table with exact strings:
      camp enter <member>   -- activate a member for the current session
      camp status           -- check provisioning status
      camp setup --retry    -- retry failed/pending provisioning
  - guidance that members are INERT until `camp enter <member>`
  - guidance that setup may be in flight (background provisioner)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _render_doc(
    group: dict[str, Any],
    slug: str,
) -> str:
    """Render the workspace doc content for CLAUDE.md and AGENT.md.

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
you explicitly activate it** with `camp enter <member>`. Do not attempt to
work in a member directory until you have entered it.

Setup may be in flight (background provisioner is running). Check status
before acting on any member.

## Commands

| Command | Purpose |
|---------|---------|
| `camp enter <member>` | Activate a member for the current session |
| `camp status` | Check provisioning status (exit 0=ready, 2=pending, 3=failed) |
| `camp setup --retry` | Retry failed or pending member provisioning |

## Workflow

1. Run `camp status` to see which members are ready.
2. If setup is still in flight (pending), wait or run `camp setup --retry`.
3. Run `camp enter <member>` to activate a member — this prints its CLAUDE.md
   and marks it active for the session.
4. Work in the activated member's worktree directory.

Members: {', '.join(member_names)}
"""


def write_workspace_doc(
    workspace_dir: Path,
    group: dict[str, Any],
    slug: str,
) -> None:
    """Write CLAUDE.md and AGENT.md at the workspace root.

    Idempotent: both files are fully rewritten on each call, so the result
    is always stable for the same inputs with no duplication.

    Args:
        workspace_dir: Absolute path to the workspace root directory.
        group:         Parsed group config dict.
        slug:          The workspace slug (used in the doc header).
    """
    content = _render_doc(group, slug)
    (workspace_dir / "CLAUDE.md").write_text(content)
    (workspace_dir / "AGENT.md").write_text(content)
