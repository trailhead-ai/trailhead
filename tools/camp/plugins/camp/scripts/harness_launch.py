"""Harness-launch seam — Slice 6 (config-shaped, claude default).

The single launch point at the tail of `camp ai`. A group-level optional
[harness] config block declares how to launch the harness:

    [harness]
    new    = ["claude"]                  # first-ever launch
    resume = ["claude", "-r", "{slug}"]  # existing workspace
    cwd    = "{workspace}"               # launch dir

with {slug} / {workspace} substitution. When the block is ABSENT, the baked-in
claude default applies: new=["claude"] rooted at the workspace dir;
resume=["claude","-r","{slug}"].

resolve_harness_profile merges the [harness] block over the claude default ONCE
into a frozen HarnessProfile (launch argv + cwd + doc_files + inject). The legacy
resolve_launch / resolve_doc_files / resolve_inject are thin views over it, kept
for the callers/tests that read a single field. profile.launch() does the
{slug}/{workspace} substitution → (argv, cwd); launch() chdirs + os.execvp's.
Modality is terminal-exec (claude-specific); GUI/detached launch is deferred.

Tests stub launch() (trailhead.paths is not isolated for the real claude runner —
memory: harness-cli-not-isolated-by-trailhead-env). The CAMP_TEST_NO_EXEC escape
hatch short-circuits the real os.execvp for the subprocess-level CLI tests that
cannot monkeypatch in-process.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class HarnessProfile:
    """The fully-resolved harness profile: launch argv + cwd + doc_files + inject
    + pretrust.

    Built ONCE by resolve_harness_profile by merging a [harness] block over the
    baked-in claude default. Carries the still-unsubstituted templates for new /
    resume / cwd; launch() does the {slug}/{workspace} substitution at call time.
    `pretrust` gates the claude trust pre-seed (bring_up_workspace, Slice 2); it is
    only acted on for claude launches (see is_claude_launch).
    """

    new: list[str]
    resume: list[str]
    cwd: str
    doc_files: list[str]
    inject: str  # "stdout" | "claude-hook"
    pretrust: bool  # pre-seed the claude per-dir trust flag (claude launches only)

    def resolved_cwd(self, *, slug: str, workspace: Path | str) -> Path:
        """Single source of the substituted launch cwd.

        Accepts workspace as Path or str; returns the resolved Path after
        {slug}/{workspace} substitution.  Used by launch() and by
        pretrust_workspace (Slice 1) to determine the trust target without
        having to duplicate the substitution logic.
        """
        return Path(_substitute(self.cwd, slug=slug, workspace=str(workspace)))

    def is_claude_launch(self) -> bool:
        """True when the new-launch binary is `claude` (by basename).

        Keyed on Path(new[0]).name == "claude" — a false-negative for wrappers,
        but safe: skip pretrust when unsure (council/Security minor).
        """
        return Path(self.new[0]).name == "claude"

    def launch(
        self, *, slug: str, workspace: str, is_resume: bool
    ) -> tuple[list[str], Path]:
        """Substitute {slug}/{workspace} into the resolved templates → (argv, cwd)."""
        template = self.resume if is_resume else self.new
        argv = [_substitute(tok, slug=slug, workspace=workspace) for tok in template]
        cwd = self.resolved_cwd(slug=slug, workspace=workspace)
        return argv, cwd


def resolve_harness_profile(group: dict[str, Any]) -> HarnessProfile:
    """Merge the [harness] block over the claude default ONCE → a frozen profile.

    Per-field merge over _CLAUDE_DEFAULT for new/resume/cwd/doc_files: a partial
    block only overrides the fields it lists. The inject default is the one
    intentional asymmetry (preserved exactly):
      - NO [harness] block (bare claude default)  → "claude-hook" (native hook)
      - a [harness] block WITHOUT inject          → "stdout" (safe universal floor
        for a harness whose injection contract we have not opted into)
      - a configured inject value                 → that value (enum-validated by
        group_config at load time).

    `pretrust` has NO such asymmetry — it defaults to True everywhere; the
    is_claude_launch() gate at the call site (not the default) is what prevents a
    non-claude [harness] block from getting a claude trust write.
    """
    harness = group.get("harness")
    inject = "claude-hook" if harness is None else harness.get("inject", "stdout")
    harness = harness or {}

    return HarnessProfile(
        new=list(harness.get("new") or _CLAUDE_DEFAULT["new"]),
        resume=list(harness.get("resume") or _CLAUDE_DEFAULT["resume"]),
        cwd=harness.get("cwd") or _CLAUDE_DEFAULT["cwd"],
        doc_files=list(harness["doc_files"])
        if "doc_files" in harness
        else ["CLAUDE.md"],
        inject=inject,
        pretrust=harness.get("pretrust", True),
    )


def resolve_doc_files(group: dict[str, Any]) -> list[str]:
    """Resolve the workspace doc filenames to write (thin view over the profile)."""
    return resolve_harness_profile(group).doc_files


def resolve_inject(group: dict[str, Any]) -> str:
    """Resolve the mid-session context-injection strategy (thin view over the profile).

    "stdout" is the universal floor (print the doc to stdout); "claude-hook"
    enqueues the doc for the Claude Code PostToolUse → additionalContext channel.
    """
    return resolve_harness_profile(group).inject


def resolve_launch(
    group: dict[str, Any],
    slug: str,
    workspace_dir: Path,
    *,
    is_resume: bool,
) -> tuple[list[str], Path]:
    """Resolve (config | claude default) + is_resume → (argv, cwd).

    Thin view over the unified profile: resolve once, then substitute.
    """
    return resolve_harness_profile(group).launch(
        slug=slug, workspace=str(workspace_dir), is_resume=is_resume
    )


def launch(
    group: dict[str, Any],
    slug: str,
    workspace_dir: Path,
    *,
    is_resume: bool,
    profile: HarnessProfile | None = None,
) -> None:
    """Resolve then chdir + os.execvp the harness (terminal-exec). Replaces this
    process image — does not return on success.

    The caller may pass the once-resolved profile; otherwise it is resolved here.
    """
    if profile is None:
        profile = resolve_harness_profile(group)
    argv, cwd = profile.launch(
        slug=slug, workspace=str(workspace_dir), is_resume=is_resume
    )

    if os.environ.get("CAMP_TEST_NO_EXEC"):
        # Test-only escape hatch for subprocess-level CLI tests that cannot
        # monkeypatch this seam in-process. NEVER set in production.
        return

    os.chdir(str(cwd))
    os.execvp(argv[0], argv)
