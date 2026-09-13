"""ASSUMPTION PROBE (ephemeral — delete before merge).

Resolves the parent task's Known Unknown: can the sending host locate AND
relocate its own crossed transcripts entirely through the existing harness
location seam (`camp.cli.transfer._locate_transcript`, which reaches the
harness only via `_addressable_harnesses` / `HarnessStore.session_transcript_path`),
without widening what that seam exposes past what
`test_projects_key_confinement.py` permits?

This probe does not implement the archive feature. It drives the REAL
production `_locate_transcript` callable against a real `ClaudeCodeHarness`
writing to a temp `TRAILHEAD_CLAUDE_DIR`, uses ONLY the `Path` it hands back
to relocate the file (a plain `shutil.move`, no re-derivation of the projects
key), and then re-asks the seam to confirm the source is gone.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import _bootstrap  # noqa: E402

_bootstrap.ensure_trailhead_importable()


def _transfer_module():
    return importlib.import_module("camp.cli.transfer")


class _RealStore:
    """A minimal stand-in for `camp.launch.profile.HarnessStore`: proxies
    `session_transcript_path` straight to a real `ClaudeCodeHarness` bound to
    a fixed `env`, exactly the shape `_locate_transcript` iterates over."""

    def __init__(self, env: dict[str, str]) -> None:
        from trailhead.harness.claude_code import ClaudeCodeHarness

        self._harness = ClaudeCodeHarness()
        self.env = env

    def session_transcript_path(self, session_id: str, root, *, env=None):
        return self._harness.session_transcript_path(session_id, root, env=env or self.env)


def _make_transcript(claude_dir: Path, session_id: str, workspace_root: Path) -> Path:
    """Seed a real transcript the way `ClaudeCodeHarness` itself would, using
    its own `session_transcript_destination` seam rather than re-deriving the
    projects-key encoding by hand."""
    from trailhead.harness.claude_code import ClaudeCodeHarness

    harness = ClaudeCodeHarness()
    env = {"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
    dest = harness.session_transcript_destination(session_id, workspace_root, env=env)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text('{"cwd": %r}\n' % str(workspace_root), encoding="utf-8")
    return dest


def test_locate_then_relocate_through_the_existing_seam_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transfer = _transfer_module()
    session_mod = importlib.import_module("camp.cli.session")

    claude_dir = tmp_path / "claude"
    workspace_root = (tmp_path / "ws").resolve()
    workspace_root.mkdir()
    session_id = "aaaaaaaa-1111-4111-8111-111111111111"

    original_path = _make_transcript(claude_dir, session_id, workspace_root)
    assert original_path.is_file()
    original_bytes = original_path.read_bytes()

    env = {"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
    monkeypatch.setattr(
        session_mod, "_addressable_harnesses", lambda groups, **kw: [_RealStore(env)]
    )

    # Q1: locate every transcript that would cross, through the production
    # seam alone.
    locate = transfer._locate_transcript(session_groups=["G"], resolved_env=env)
    located = locate(session_id, workspace_root)
    assert located == original_path

    # Q2: relocate using nothing but the Path the seam handed back — a plain
    # filesystem move to a camp-owned archive path camp derives freely
    # (never re-deriving the projects-key encoding).
    archive_root = tmp_path / "camp-archive"
    archive_root.mkdir()
    archive_dest = archive_root / f"{session_id}.jsonl"
    shutil.move(str(located), str(archive_dest))

    # The archived bytes are identical, and the source is now unresolvable
    # through the SAME seam — re-asked, not filesystem-inspected.
    assert archive_dest.read_bytes() == original_bytes
    assert not original_path.exists()
    assert locate(session_id, workspace_root) is None
