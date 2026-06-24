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

Failure posture: every *expected* abort (out-of-confinement, malformed /
unreadable / structurally-wrong existing file) emits a single `camp: …` line on
stderr and returns without raising.  An *unexpected* failure of the atomic write
itself (after the merged payload is built) unlinks the temp file and propagates —
the best-effort caller (bring_up_workspace) catches it, logs `camp: pretrust
failed`, and continues.  Either way launch proceeds and, on failure, the user
simply sees Claude Code's trust dialog instead.

Security (council/Security C2 — confinement):
  pretrust_workspace only writes when launch_dir is workspace_root or a
  descendant of it (realpath comparison).  A crafted cwd = "/etc" in the group
  config is silently refused — same posture as compose.py's dual-end confinement.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _home_from_env(env: dict[str, str] | None) -> Path:
    """Resolve HOME from the injected env dict, falling back to Path.home()."""
    if env:
        for key in ("HOME", "USERPROFILE"):
            if key in env:
                return Path(env[key])
    return Path.home()


def _is_mergeable(data: object, project_key: str) -> bool:
    """True if `data` is shaped so the trust flag can be merged without raising.

    Guards the build path against parseable-but-wrong JSON: the top level, the
    `projects` map, and the existing per-project entry must each be objects.
    A real ~/.claude.json is always dict-shaped; this keeps the module's own
    "never raises" promise from depending on the caller's try/except.
    """
    if not isinstance(data, dict):
        return False
    projects = data.get("projects")
    if projects is not None and not isinstance(projects, dict):
        return False
    if isinstance(projects, dict):
        entry = projects.get(project_key)
        if entry is not None and not isinstance(entry, dict):
            return False
    return True


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

    # Load existing file, or start from scratch when absent. Exception-based
    # detection (no pre-check exists() stat): a missing file is the create case;
    # any other read error aborts without overwriting. This closes the
    # exists()→read TOCTOU window.
    existing_data: dict | None = None
    try:
        with open(str(claude_json_path), "r") as fh:
            raw = fh.read()
    except FileNotFoundError:
        pass  # absent → create from scratch below
    except OSError as exc:
        print(
            f"camp: pretrust skipped — could not read {claude_json_path}: {exc} "
            "(unreadable file; aborting to avoid overwriting)",
            file=sys.stderr,
        )
        return
    else:
        try:
            existing_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"camp: pretrust skipped — {claude_json_path} contains malformed JSON "
                f"({exc}); not overwriting",
                file=sys.stderr,
            )
            return

        # Parseable but structurally wrong (top-level or projects/entry not a
        # mapping) is treated like malformed: abort without overwriting so the
        # "never raises, never clobbers" contract holds on the build path too,
        # not just the read path (council/Reliability — non-dict shapes).
        if not _is_mergeable(existing_data, str(launch_dir)):
            print(
                f"camp: pretrust skipped — {claude_json_path} has an unexpected "
                "structure (projects/entry is not an object); not overwriting",
                file=sys.stderr,
            )
            return

    # Idempotency check: skip if already trusted.
    project_key = str(launch_dir)
    if existing_data is not None:
        entry = existing_data.get("projects", {}).get(project_key, {})
        if entry.get("hasTrustDialogAccepted") is True:
            return

    # Build the merged payload.
    data = existing_data if existing_data is not None else {}
    projects = data.setdefault("projects", {})
    project_entry = projects.setdefault(project_key, {})
    project_entry["hasTrustDialogAccepted"] = True

    # Atomic write: tmp file in HOME (not in a .claude/ subdir), then os.replace.
    # The file lands 0o600 unconditionally — tempfile.mkstemp creates the tmp file
    # 0o600 by construction and we never widen it. This is deliberate: ~/.claude.json
    # holds OAuth secrets, so we always enforce owner-only perms rather than
    # preserving a (possibly looser) pre-existing mode (council/Security).
    fd, tmp_path_str = tempfile.mkstemp(dir=str(home), prefix=".claude-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path_str, str(claude_json_path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
