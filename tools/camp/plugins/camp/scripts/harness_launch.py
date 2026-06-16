"""Harness-launch seam — Slice 6 (config-shaped, claude default).

The single launch point at the tail of `camp ai`. A group-level optional
[harness] config block declares how to launch the harness:

    [harness]
    new    = ["claude"]                  # first-ever launch
    resume = ["claude", "-r", "{slug}"]  # existing workspace
    cwd    = "{workspace}"               # launch dir

with {slug} / {workspace} substitution. When the block is ABSENT, the baked-in
claude default applies: new=["claude"] rooted at the workspace dir;
resume=["claude","-r","{slug}"]. resolve_launch resolves (config | default) +
is_resume → (argv, cwd); launch() chdirs + os.execvp's. Modality is terminal-exec
(claude-specific); GUI/detached launch is deferred.

Tests stub launch() (trailhead.paths is not isolated for the real claude runner —
memory: harness-cli-not-isolated-by-trailhead-env). The CAMP_TEST_NO_EXEC escape
hatch short-circuits the real os.execvp for the subprocess-level CLI tests that
cannot monkeypatch in-process.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Baked-in claude default (applied when no [harness] block is configured).
_CLAUDE_DEFAULT = {
    "new": ["claude"],
    "resume": ["claude", "-r", "{slug}"],
    "cwd": "{workspace}",
}


def _substitute(token: str, *, slug: str, workspace: str) -> str:
    return token.format(slug=slug, workspace=workspace)


def resolve_doc_files(group: dict[str, Any]) -> list[str]:
    """Resolve the workspace doc filenames to write.

    Returns the list of filenames from harness.doc_files when configured,
    or ["CLAUDE.md"] as the baked-in claude default (matches how resolve_launch
    falls back: harness = group.get("harness") or _CLAUDE_DEFAULT).
    """
    harness = group.get("harness")
    if harness and "doc_files" in harness:
        return list(harness["doc_files"])
    return ["CLAUDE.md"]


def resolve_launch(
    group: dict[str, Any],
    slug: str,
    workspace_dir: Path,
    *,
    is_resume: bool,
) -> tuple[list[str], Path]:
    """Resolve (config | claude default) + is_resume → (argv, cwd).

    Returns the substituted argv and the resolved launch directory.
    """
    harness = group.get("harness") or _CLAUDE_DEFAULT
    workspace = str(workspace_dir)

    template = harness["resume"] if is_resume else harness["new"]
    argv = [_substitute(tok, slug=slug, workspace=workspace) for tok in template]

    cwd_template = harness.get("cwd", "{workspace}")
    cwd = Path(_substitute(cwd_template, slug=slug, workspace=workspace))
    return argv, cwd


def launch(
    group: dict[str, Any],
    slug: str,
    workspace_dir: Path,
    *,
    is_resume: bool,
) -> None:
    """Resolve then chdir + os.execvp the harness (terminal-exec). Replaces this
    process image — does not return on success."""
    argv, cwd = resolve_launch(group, slug, workspace_dir, is_resume=is_resume)

    if os.environ.get("CAMP_TEST_NO_EXEC"):
        # Test-only escape hatch for subprocess-level CLI tests that cannot
        # monkeypatch this seam in-process. NEVER set in production.
        return

    os.chdir(str(cwd))
    os.execvp(argv[0], argv)
