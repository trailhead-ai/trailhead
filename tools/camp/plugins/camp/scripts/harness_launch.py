"""Harness-launch seam — Slice 6 (config-shaped, claude default).

The single launch point at the tail of `camp ai`. A group-level optional
[harness] config block declares how to launch the harness:

    [harness]
    new    = ["claude", "--session-id", "{session_id}"]  # first-ever launch
    resume = ["claude", "--resume", "{session_id}"]       # existing workspace
    cwd    = "{workspace}"                                # launch dir

with {slug} / {workspace} / {session_id} substitution. When the block is ABSENT,
the baked-in claude default applies: new seeds a deterministic session id with
`--session-id`, resume continues it with `--resume`, both rooted at the workspace
dir. (Resume keys on the SESSION ID, not the slug — `claude --resume` resumes by
id, never by name; see session_identity.session_id_for.)

resolve_harness_profile merges the [harness] block over the claude default ONCE
into a frozen HarnessProfile (launch argv + cwd + doc_files + inject); callers
read fields off it directly. profile.launch() does the
{slug}/{workspace}/{session_id} substitution → (argv, cwd); launch() chdirs +
os.execvp's. Modality is terminal-exec (claude-specific); GUI/detached launch is
deferred.

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
#
# Resume keys on a deterministic SESSION ID, not the slug: `claude -r/--resume`
# resumes by session id (a UUID), never by name, so the old `-r {slug}` never
# matched and dumped the user in the resume picker. camp derives a stable id from
# (group, slug) (session_identity.session_id_for) and seeds it on the first launch
# with `--session-id`, then resumes it with `--resume`. (`--session-id` errors on
# an already-existing id — verified — so the new/resume split is required.)
_CLAUDE_DEFAULT = {
    "new": ["claude", "--session-id", "{session_id}"],
    "resume": ["claude", "--resume", "{session_id}"],
    "cwd": "{workspace}",
}


def _substitute(token: str, *, slug: str, workspace: str, session_id: str) -> str:
    return token.format(slug=slug, workspace=workspace, session_id=session_id)


def claude_session_exists(session_id: str, *, env: dict[str, str] | None = None) -> bool:
    """Whether a claude session transcript already exists for `session_id`.

    Claude stores transcripts at `<config>/projects/<encoded-cwd>/<uuid>.jsonl`.
    The deterministic id is globally unique to its (group, slug), so we glob by id
    across all projects — this is independent of claude's cwd-encoding scheme
    (resolved realpath with `/` and `.` → `-`), which is brittle to predict.

    Used to pick new-vs-resume by SESSION existence rather than workspace-dir
    existence: a provisioned-but-never-launched (or deleted-session) workspace must
    start fresh with `--session-id`, never `--resume` a missing id (→ picker).
    """
    import os

    env = env if env is not None else os.environ
    config_dir = env.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    projects = Path(config_dir) / "projects"
    if not projects.is_dir():
        return False
    return any(projects.glob(f"*/{session_id}.jsonl"))


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
    pretrust: bool  # opt-in to the claude trust pre-seed (see should_pretrust)

    def resolved_cwd(
        self, *, slug: str, workspace: Path | str, session_id: str = ""
    ) -> Path:
        """Single source of the substituted launch cwd.

        Accepts workspace as Path or str; returns the resolved Path after
        {slug}/{workspace}/{session_id} substitution.  Used by launch() and by
        pretrust_workspace to determine the trust target without duplicating the
        substitution logic. session_id defaults to "" because the cwd template is
        "{workspace}" by default and never references {session_id} in practice;
        launch() passes the real id so a custom cwd that does reference it works.
        """
        return Path(
            _substitute(
                self.cwd, slug=slug, workspace=str(workspace), session_id=session_id
            )
        )

    def is_claude_launch(self) -> bool:
        """True when the new-launch binary is `claude` (by basename).

        Keyed on Path(new[0]).name == "claude". Empty `new` is impossible in
        practice (group_config rejects it and resolve_harness_profile falls back
        to the non-empty default), but guard the index anyway so the predicate
        honors its "answer, don't raise" contract for a directly-built profile.
        """
        return bool(self.new) and Path(self.new[0]).name == "claude"

    def should_pretrust(self) -> bool:
        """Whether camp should pre-seed the claude trust flag for this launch.

        The single, declarative decision the bring-up call site asks (so harness
        scoping lives on the profile, not as a separate guard at the call site).

        Fires when pretrust is opted-in AND ANY positive claude signal holds:
          - the launch binary's basename is `claude` (covers the bare default and
            an explicit `[harness] new = ["claude", …]` block), OR
          - the native claude-hook inject channel is selected — the declarative
            opt-in for a claude launched under a wrapper/renamed binary, where the
            basename check alone would false-negative.

        Using OR (not the inject signal alone) is deliberate: an explicit
        `[harness]` block without `inject` defaults inject to "stdout", so an
        inject-only gate would wrongly skip pretrust for a plain `["claude", …]`
        launch.
        """
        return self.pretrust and (
            self.is_claude_launch() or self.inject == "claude-hook"
        )

    def launch(
        self, *, slug: str, workspace: str, is_resume: bool, session_id: str
    ) -> tuple[list[str], Path]:
        """Substitute {slug}/{workspace}/{session_id} into the templates → (argv, cwd)."""
        template = self.resume if is_resume else self.new
        argv = [
            _substitute(tok, slug=slug, workspace=workspace, session_id=session_id)
            for tok in template
        ]
        cwd = self.resolved_cwd(slug=slug, workspace=workspace, session_id=session_id)
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

    `pretrust` has NO such asymmetry — it defaults to True everywhere. Harness
    scoping is NOT carried by the default; it lives in HarnessProfile.should_pretrust()
    (pretrust AND a positive claude signal), which is what prevents a non-claude
    [harness] block from getting a claude trust write.
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


def launch(
    group: dict[str, Any],
    slug: str,
    workspace_dir: Path,
    *,
    is_resume: bool,
    session_id: str | None = None,
    profile: HarnessProfile | None = None,
) -> None:
    """Resolve then chdir + os.execvp the harness (terminal-exec). Replaces this
    process image — does not return on success.

    The caller may pass the once-resolved profile and/or the deterministic
    session id; either is derived here if omitted.
    """
    if profile is None:
        profile = resolve_harness_profile(group)
    if session_id is None:
        from session_identity import session_id_for

        session_id = session_id_for(group["group"]["name"], slug)
    argv, cwd = profile.launch(
        slug=slug,
        workspace=str(workspace_dir),
        is_resume=is_resume,
        session_id=session_id,
    )

    if os.environ.get("CAMP_TEST_NO_EXEC"):
        # Test-only escape hatch for subprocess-level CLI tests that cannot
        # monkeypatch this seam in-process. NEVER set in production.
        return

    os.chdir(str(cwd))
    os.execvp(argv[0], argv)
