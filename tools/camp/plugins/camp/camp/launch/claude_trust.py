"""Pre-seed the Claude Code per-directory trust flag in its global config file.

This is camp's launch-time analog of trailhead/harness/claude_code.py — harness-
specific code that lives in the camp plugin alongside the existing claude-specific
hooks_writer.py.  It is invoked by bring_up_workspace immediately before
the harness is exec'd.

Verified entry shape (2026-06-16, manual interactive validation):
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
- The tmp file lives beside the target file.  Do NOT "fix" this by copying
  hooks_writer._save_settings — that helper writes into the workspace dir; our
  target is the Claude global config file.
- The target is resolved from the injected env dict by trailhead's exported
  `claude_config_file`: <CLAUDE_CONFIG_DIR>/.claude.json, else
  $HOME/.claude.json.  `TRAILHEAD_CLAUDE_DIR` deliberately does NOT move it —
  that seam relocates the config *directory* only, and a file resolved through
  it is one Claude Code never reads.  Every test passes env={"HOME": str(tmp_path)}
  and never touches the real ~/.claude.json (Axiom 6 — the harness CLI is not
  isolated by the trailhead env).
- Silent-miss limitation: a write that claude silently ignores (e.g. because
  Claude changed the file schema) produces no error signal here.  If the dialog
  reappears after bring-up, re-run the manual interactive check to validate the
  current entry shape.

Failure posture: every *expected* abort (out-of-confinement, a config file that
resolves to a relative path, malformed / unreadable / structurally-wrong existing file) emits a single `camp: …` line on
stderr and returns without raising.  An *unexpected* failure of the atomic write
itself (after the merged payload is built) unlinks the temp file and propagates —
the best-effort caller (bring_up_workspace) catches it, logs `camp: pretrust
failed`, and continues.  Either way launch proceeds and, on failure, the user
simply sees Claude Code's trust dialog instead.

Security (confinement):
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


def config_file(env: dict[str, str] | None) -> Path:
    """Resolve the Claude global config file the launched session will read.

    Delegates to trailhead's exported ``claude_config_file`` — the same resolver
    trailhead itself uses — so a session relocated with ``CLAUDE_CONFIG_DIR``
    receives its trust key instead of stalling at a prompt with no TTY. The import
    is deferred: camp ships as a standalone CLI, so a camp installed without
    trailhead must fail inside the caller's guard rather than at module import.
    """
    from trailhead.harness import claude_config_file

    return claude_config_file(env)


def trust_status(
    launch_dir: Path | str, *, env: dict[str, str] | None = None
) -> tuple[Path, bool | None]:
    """The config file a launched session reads, and whether it trusts *launch_dir*.

    The read-only companion to :func:`pretrust_workspace`, sharing its resolver
    and its notion of where the key lives, so a report about the seed cannot
    name a different file or a different key than the write used.

    Returns ``(path, True | False | None)``. ``None`` means camp could not tell —
    the file is absent, unreadable, or not shaped the way the seed expects — and
    is a reportable answer in its own right. ``False`` is the positive finding
    that a readable, well-shaped file grants this directory nothing.
    """
    path = config_file(env)
    try:
        with open(str(path), "r") as fh:
            data = json.loads(fh.read())
    except (OSError, json.JSONDecodeError):
        return path, None
    if not isinstance(data, dict):
        return path, None
    projects = data.get("projects")
    if projects is None:
        return path, False
    if not isinstance(projects, dict):
        return path, None
    entry = projects.get(str(Path(launch_dir).resolve()))
    if not isinstance(entry, dict):
        return path, False
    return path, entry.get("hasTrustDialogAccepted") is True


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
) -> bool:
    """Merge `hasTrustDialogAccepted: true` into the Claude config file for launch_dir.

    launch_dir   — the directory the harness will be launched in (the trust target).
    workspace_root — the workspace root; launch_dir must equal or be under this
                    (confinement).
    env          — optional environment dict; the Claude config file is resolved
                   from it (the harness relocation variable, else HOME) so a
                   relocated session is
                   trusted in the file it reads, and so tests can sandbox under
                   tmp_path without touching the real ~/.claude.json.

    Idempotent: if the entry already exists and is true, no write is performed.

    Returns True when trust is in place (a fresh write, or the already-trusted
    idempotent no-op). Returns False on every abort path below (out-of-confinement,
    a config file resolving to a relative path, unreadable / malformed /
    structurally-wrong existing file) — each still emits its camp: stderr line
    and does not raise.

    Failure posture: malformed / unreadable existing file → emit camp: stderr, return False.
    Out-of-confinement launch_dir → emit camp: stderr, return False. No exception raised
    on these paths. An atomic-write failure is a different case — see below — and
    propagates instead of returning False.
    """
    launch_dir = Path(launch_dir).resolve()
    workspace_root = Path(workspace_root).resolve()

    # Confinement check: launch_dir must be workspace_root or a descendant.
    try:
        launch_dir.relative_to(workspace_root)
    except ValueError:
        print(
            f"camp: pretrust skipped — {launch_dir} is not under workspace_root "
            f"{workspace_root} (confinement check)",
            file=sys.stderr,
        )
        return False

    claude_json_path = config_file(env)

    # A relative config file resolves against camp's cwd rather than the launched
    # session's, and the write below creates its parent tree — so a relocation
    # override that is not absolute would build an arbitrary directory and report
    # success on a trust key Claude never reads. Refused, matching the concierge's
    # own absolute-override rule. The resolved path is what is checked, not the
    # variable behind it: only the harness knows which variable that is.
    if not claude_json_path.is_absolute():
        print(
            f"camp: pretrust skipped — the harness config file resolves to "
            f"{str(claude_json_path)!r}; override paths must be absolute",
            file=sys.stderr,
        )
        return False

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
        return False
    else:
        try:
            existing_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"camp: pretrust skipped — {claude_json_path} contains malformed JSON "
                f"({exc}); not overwriting",
                file=sys.stderr,
            )
            return False

        # Parseable but structurally wrong (top-level or projects/entry not a
        # mapping) is treated like malformed: abort without overwriting so the
        # "never raises, never clobbers" contract holds on the build path too,
        # not just the read path (non-dict shapes).
        if not _is_mergeable(existing_data, str(launch_dir)):
            print(
                f"camp: pretrust skipped — {claude_json_path} has an unexpected "
                "structure (projects/entry is not an object); not overwriting",
                file=sys.stderr,
            )
            return False

    # Idempotency check: skip if already trusted.
    project_key = str(launch_dir)
    if existing_data is not None:
        entry = existing_data.get("projects", {}).get(project_key, {})
        if entry.get("hasTrustDialogAccepted") is True:
            return True

    # Build the merged payload.
    data = existing_data if existing_data is not None else {}
    projects = data.setdefault("projects", {})
    project_entry = projects.setdefault(project_key, {})
    project_entry["hasTrustDialogAccepted"] = True

    # Atomic write: tmp file beside the target (a relocated config dir may sit on
    # another filesystem, where a temp file under HOME could not be renamed onto
    # it), then os.replace.
    # The file lands 0o600 unconditionally — tempfile.mkstemp creates the tmp file
    # 0o600 by construction and we never widen it. This is deliberate: ~/.claude.json
    # holds OAuth secrets, so we always enforce owner-only perms rather than
    # preserving a (possibly looser) pre-existing mode (security).
    target_dir = claude_json_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=str(target_dir), prefix=".claude-", suffix=".tmp")
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

    return True
