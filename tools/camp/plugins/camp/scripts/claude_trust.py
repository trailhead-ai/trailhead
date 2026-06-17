"""Pre-seed the Claude Code per-directory trust flag in ~/.claude.json.

This is camp's launch-time analog of trailhead/harness/claude_code.py — harness-
specific code that lives in the camp plugin alongside the existing claude-specific
hooks_writer.py.  It is invoked by bring_up_workspace (Slice 2) immediately before
the harness is exec'd.

U1-verified entry shape (2026-06-16, manual interactive validation):
  {
    "projects": {
      "<realpath>": {"hasTrustDialogAccepted": true}
    }
  }
A **minimal** entry containing only `hasTrustDialogAccepted` suppresses Claude
Code's trust dialog for a fresh directory.  The project key is the **realpath**
(os.path.realpath / Path.resolve()) — required on macOS where /tmp → /private/tmp.
No companion keys are written alongside hasTrustDialogAccepted.

Design notes:
- The tmp file lives in HOME (not a .claude/ subdir).  Do NOT "fix" this by
  copying hooks_writer._save_settings — that helper writes into the workspace
  dir; our target is ~/.claude.json at the root of HOME.
- HOME is resolved from the injected env dict (env["HOME"] or env["USERPROFILE"]),
  falling back to Path.home() — mirrors claude_code.py:detect().  Every test
  passes env={"HOME": str(tmp_path)} and never touches the real ~/.claude.json
  (Axiom 6 / lesson: harness-cli-not-isolated-by-trailhead-env).
- Silent-miss limitation: a write that claude silently ignores (e.g. because
  Claude changed the file schema) produces no error signal here.  If the dialog
  reappears after bring-up, re-run the manual interactive check to validate the
  current entry shape.

Failure posture: any abort path emits a single `camp: …` line on stderr and
returns without raising.  The caller (bring_up_workspace) treats this as best-
effort — launch continues and the user sees Claude Code's trust dialog instead.

Security (council/Security C2 — confinement):
  pretrust_workspace only writes when launch_dir is workspace_root or a
  descendant of it (realpath comparison).  A crafted cwd = "/etc" in the group
  config is silently refused — same posture as compose.py's dual-end confinement.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _home_from_env(env: dict[str, str] | None) -> Path:
    """Resolve HOME from the injected env dict, falling back to Path.home()."""
    if env:
        for key in ("HOME", "USERPROFILE"):
            if key in env:
                return Path(env[key])
    return Path.home()


def pretrust_workspace(
    launch_dir: Path | str,
    *,
    workspace_root: Path | str,
    env: dict[str, str] | None = None,
) -> None:
    """Merge `hasTrustDialogAccepted: true` into ~/.claude.json for launch_dir.

    launch_dir   — the directory the harness will be launched in (the trust target).
    workspace_root — the workspace root; launch_dir must equal or be under this
                    (confinement / council/Security C2).
    env          — optional environment dict; HOME is resolved from it so tests
                   can sandbox under tmp_path without touching the real ~/.claude.json.

    Idempotent: if the entry already exists and is true, no write is performed.

    Failure posture: malformed / unreadable existing file → emit camp: stderr, return.
    Out-of-confinement launch_dir → emit camp: stderr, return.  No exception raised.
    """
    launch_dir = Path(launch_dir).resolve()
    workspace_root = Path(workspace_root).resolve()

    # Confinement check (C2): launch_dir must be workspace_root or a descendant.
    try:
        launch_dir.relative_to(workspace_root)
    except ValueError:
        print(
            f"camp: pretrust skipped — {launch_dir} is not under workspace_root "
            f"{workspace_root} (confinement check)",
            file=sys.stderr,
        )
        return

    home = _home_from_env(env)
    claude_json_path = home / ".claude.json"

    # Load existing file, or start from scratch when absent.
    existing_data: dict | None = None
    existing_mode: int | None = None

    if claude_json_path.exists():
        try:
            existing_mode = claude_json_path.stat().st_mode & 0o777
            with open(str(claude_json_path), "r") as fh:
                raw = fh.read()
        except OSError as exc:
            print(
                f"camp: pretrust skipped — could not read {claude_json_path}: {exc} "
                "(unreadable file; aborting to avoid overwriting)",
                file=sys.stderr,
            )
            return

        try:
            existing_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"camp: pretrust skipped — {claude_json_path} contains malformed JSON "
                f"({exc}); not overwriting",
                file=sys.stderr,
            )
            return

    # Idempotency check: skip if already trusted.
    project_key = str(launch_dir)
    if existing_data is not None:
        projects = existing_data.get("projects", {})
        if isinstance(projects, dict):
            entry = projects.get(project_key, {})
            if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
                return

    # Build the merged payload.
    data = existing_data if existing_data is not None else {}
    projects = data.setdefault("projects", {})
    project_entry = projects.setdefault(project_key, {})
    project_entry["hasTrustDialogAccepted"] = True

    # Atomic write: tmp file in HOME (not in a .claude/ subdir), then os.replace.
    # The tmp file is created 0o600 via an opener so there is no world-readable
    # window even for the brief moment the file exists in HOME (council/Security).
    write_mode = existing_mode if existing_mode is not None else 0o600

    def _opener(path: str, flags: int) -> int:
        return os.open(path, flags, 0o600)

    import tempfile

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(home), prefix=".claude-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        # Restore the original mode before promoting (covers the case where the
        # original file had a non-0o600 mode; the tmp was created 0o600).
        if write_mode != 0o600:
            os.chmod(tmp_path_str, write_mode)
        os.replace(tmp_path_str, str(claude_json_path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
