#!/usr/bin/env python3
"""PostToolUse hook: route subagent harvest-candidate blocks into the vault's
`harvest-pending.md`.

Fires after a subagent (Agent) tool call. If the subagent's result contains a
trailing `## Harvest candidates` section, append its typed entries
(lesson/dead-end/deferred/radar/decision/gotcha) to
`<vault>/harvest-pending.md`, deduped by content hash. No-op when the block is
absent or the tool isn't a subagent.

Never raises — never blocks a tool call.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from vault import resolve_vault  # noqa: E402

# The block is anchored at end-of-message (trailing whitespace allowed). The
# entries group is greedy so we capture the whole list.
SECTION_RE = re.compile(
    r"(?:^|\n)## Harvest candidates\s*\n+((?:[ \t]*-[ \t]+.+\n?)+)\s*\Z",
    re.MULTILINE,
)
ENTRY_RE = re.compile(
    r"^[ \t]*-[ \t]+(lesson|dead-end|deferred|radar|decision|gotcha):[ \t]*(.+?)[ \t]*$"
)

# Tool names that denote a subagent invocation across Claude Code versions.
_SUBAGENT_TOOLS = {"Agent", "Task"}


def read_stdin_json() -> dict:
    try:
        data = sys.stdin.read()
        if not data.strip():
            return {}
        return json.loads(data)
    except Exception:
        return {}


def extract_text(tool_response) -> str:
    """tool_response shape varies — normalize to a string."""
    if tool_response is None:
        return ""
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, list):
        parts = []
        for p in tool_response:
            if isinstance(p, dict):
                parts.append(p.get("text") or p.get("content") or "")
            else:
                parts.append(str(p))
        return "\n".join(parts)
    if isinstance(tool_response, dict):
        for key in ("content", "text", "result", "output"):
            if key in tool_response:
                return extract_text(tool_response[key])
    return str(tool_response)


def existing_hashes(pending: Path) -> set[str]:
    if not pending.exists():
        return set()
    try:
        return set(re.findall(r"<!-- h:([a-f0-9]+) -->", pending.read_text(encoding="utf-8")))
    except Exception:
        return set()


def main() -> int:
    try:
        payload = read_stdin_json()
        tool = payload.get("tool_name") or payload.get("tool")
        if tool not in _SUBAGENT_TOOLS:
            return 0

        text = extract_text(payload.get("tool_response"))
        if not text or "## Harvest candidates" not in text:
            return 0

        match = SECTION_RE.search(text)
        if not match:
            return 0

        entries: list[tuple[str, str]] = []
        for raw_line in match.group(1).splitlines():
            m = ENTRY_RE.match(raw_line)
            if m:
                entries.append((m.group(1), m.group(2).strip()))
        if not entries:
            return 0

        pending = Path(resolve_vault()) / "harvest-pending.md"
        seen = existing_hashes(pending)
        new_lines: list[str] = []
        for kind, body in entries:
            h = hashlib.sha1(f"{kind}|{body}".encode("utf-8")).hexdigest()[:12]
            if h in seen:
                continue
            seen.add(h)
            new_lines.append(f"- {kind}: {body}  <!-- h:{h} -->")
        if not new_lines:
            return 0

        tool_input = payload.get("tool_input") or payload.get("input") or {}
        subagent = (
            tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
        ) or "unknown"
        cwd = payload.get("cwd") or os.environ.get("PWD") or os.getcwd()
        worktree = Path(cwd).name
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        pending.parent.mkdir(parents=True, exist_ok=True)
        if not pending.exists():
            pending.write_text(
                "# Harvest pending\n\n"
                "Subagent-emitted harvest candidates awaiting curation. See "
                "harvest-protocol.md for the format and pipeline.\n",
                encoding="utf-8",
            )

        with pending.open("a", encoding="utf-8") as f:
            f.write(f"\n## {timestamp} — {subagent} — {worktree}\n\n")
            for line in new_lines:
                f.write(line + "\n")
    except Exception:
        # Never block a tool call.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
