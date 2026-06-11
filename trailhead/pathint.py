"""PATH integration for trailhead — shim dir + idempotent shell-rc block.

Manages a trailhead-controlled shim directory under state_dir("trailhead")/bin/
and an idempotent, marker-delimited block in the user's shell rc file so wired
tools' CLIs work from a plain shell.

camp is the forcing case: it's the front door run outside any Claude Code
session.  Its shim sets TRAILHEAD_ROOT (bootstrap tier 2) so the monorepo
is reachable without CLAUDE_PLUGIN_ROOT.

Shell rc detection
------------------
Shell is detected via os.environ["SHELL"] basename; a --shell override is
provided (fish | zsh | bash) to handle $SHELL vs interactive-shell divergence.

  fish  → ~/.config/fish/config.fish   idiom: fish_add_path --path "<abs>"
  zsh   → ~/.zshrc                      idiom: export PATH="<abs>:$PATH"
  bash  → ~/.bashrc (then ~/.bash_profile fallback)
          idiom: export PATH="<abs>:$PATH"

Markers
-------
  # >>> trailhead managed PATH >>>
  …block…
  # <<< trailhead managed PATH <<<

Inject algorithm:
  1. Read the rc file (empty string if absent).
  2. If open marker present but no close marker → corrupt/partial block: strip
     from the open marker to end-of-string and repair (R-4).
  3. If both markers present → replace the entire block (idempotent).
  4. Otherwise → append the block.
  Then mkdir -p the rc parent and write.

Remove algorithm:
  Regex-strip the block (re.DOTALL).  No-op if the block is absent.

S-5 — TRAILHEAD_ROOT is hardcoded as an absolute literal at shim-write time.
  The shim must never pass through $TRAILHEAD_ROOT from the caller's env.

S-6 — shim names are checked against a denylist of system binaries.

R-7 edges:
  - Missing rc → create (mkdir -p parent).
  - Symlinked rc → resolve(); refuse with SymlinkRefusalError if it resolves
    outside Path.home() (or the injected home kwarg used in tests).
  - Non-TTY (is_tty=False) → skip rc write, return the A-8 skip message.

A-8 — non-TTY skip message:
  "PATH integration skipped (non-interactive) — run `trailhead config
   path_integration on` in your shell to enable"

Python ≥ 3.10 note (doctor item): trailhead/paths.py uses X|Y unions so any
shim that invokes a python3 < 3.10 will fail cryptically.  The doctor should
check python3 ≥ 3.10 is on the shim's PATH (Slice 5 concern).
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from trailhead.paths import ensure_dir, state_dir

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

_OPEN_MARKER = "# >>> trailhead managed PATH >>>"
_CLOSE_MARKER = "# <<< trailhead managed PATH <<<"

# ---------------------------------------------------------------------------
# S-6 denylist
# ---------------------------------------------------------------------------

_SHIM_DENYLIST = frozenset({
    "python", "python3", "git", "ssh", "curl",
    "install", "update", "sh", "bash", "fish", "zsh",
})

# ---------------------------------------------------------------------------
# Non-TTY skip message (A-8)
# ---------------------------------------------------------------------------

_NON_TTY_SKIP_MSG = (
    "PATH integration skipped (non-interactive) — run "
    "`trailhead config path_integration on` in your shell to enable"
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PathIntegrationError(Exception):
    """Raised when the shell rc file cannot be written.

    Message format: "could not write PATH block to <rc>; add <shim-dir> to
    your PATH manually"
    """


class ShimDenylistError(Exception):
    """Raised when a requested shim name matches the system-binary denylist (S-6)."""


class SymlinkRefusalError(Exception):
    """Raised when a symlinked rc resolves outside the user's home directory (R-7)."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ShimDirResult:
    """Result of create_shims()."""

    shim_dir: Path
    """The created shim directory."""

    shims: dict[str, Path]
    """Mapping of tool name → absolute shim path."""


@dataclass
class PathIntegrationResult:
    """Result of install_path_integration()."""

    shim_dir: Path
    """The created shim directory."""

    rc_path: Optional[Path]
    """The rc file that was written (None if skipped due to non-TTY)."""

    skip_message: Optional[str]
    """Set when the rc write was skipped (A-8 non-TTY path)."""


# ---------------------------------------------------------------------------
# Public API: resolve_shim_dir
# ---------------------------------------------------------------------------


def resolve_shim_dir(*, env: dict[str, str] | None = None) -> Path:
    """Return the shim directory path (pure — does not create it).

    Args:
        env: Environment dict override for path resolution.  Use
             {"TRAILHEAD_STATE_DIR": str(tmp_path)} in tests for hermeticity.

    Returns:
        state_dir("trailhead")/bin/
    """
    _env = env if env is not None else dict(os.environ)
    return state_dir("trailhead", env=_env) / "bin"


# ---------------------------------------------------------------------------
# Public API: create_shims
# ---------------------------------------------------------------------------


def create_shims(
    wired_tools: dict[str, Path],
    trailhead_root: str,
    *,
    env: dict[str, str] | None = None,
) -> ShimDirResult:
    """Create the trailhead-managed shim directory and write one shim per tool.

    Args:
        wired_tools:    Mapping of tool name → absolute path to the tool's
                        bin/<tool> wrapper (e.g. tools/camp/plugins/camp/bin/camp).
        trailhead_root: Absolute path to the trailhead repo root, hardcoded into
                        each shim at write time (S-5).
        env:            Environment dict override for path resolution.

    Returns:
        ShimDirResult with the created shim_dir and a shims dict.

    Raises:
        ShimDenylistError: If any tool name matches the system-binary denylist (S-6).
    """
    # S-6: check all names before creating anything
    for name in wired_tools:
        if name in _SHIM_DENYLIST:
            raise ShimDenylistError(
                f"refused to create shim named {name!r}: "
                f"matches system-binary denylist ({', '.join(sorted(_SHIM_DENYLIST))})"
            )

    shim_dir = resolve_shim_dir(env=env)
    ensure_dir(shim_dir, mode=0o700)

    shims: dict[str, Path] = {}
    for name, bin_path in wired_tools.items():
        shim_path = shim_dir / name
        shim_content = _shim_content(name, bin_path, trailhead_root)
        shim_path.write_text(shim_content)
        shim_path.chmod(0o700)
        shims[name] = shim_path

    return ShimDirResult(shim_dir=shim_dir, shims=shims)


def _shim_content(name: str, bin_path: Path, trailhead_root: str) -> str:
    """Generate the content for a single bash shim wrapper.

    S-5: TRAILHEAD_ROOT is hardcoded as an absolute literal — not propagated
    from the caller's environment.
    """
    return (
        "#!/usr/bin/env bash\n"
        f"# trailhead-managed shim for {name}\n"
        f'export TRAILHEAD_ROOT="{trailhead_root}"\n'
        f'exec "{bin_path}" "$@"\n'
    )


# ---------------------------------------------------------------------------
# Public API: inject_path_block
# ---------------------------------------------------------------------------


def inject_path_block(
    rc_path: Path,
    shim_dir: Path,
    shell: str,
    *,
    is_tty: bool = True,
    home: Path | None = None,
) -> str:
    """Inject (or replace) the trailhead-managed PATH block in a shell rc file.

    Args:
        rc_path:  Path to the shell rc file.  Created (with mkdir -p) if absent.
        shim_dir: Absolute path to the shim directory to add to PATH.
        shell:    Shell name: "fish", "zsh", or "bash".
        is_tty:   Whether the calling context is interactive (A-8).  When False,
                  the rc write is skipped and the skip message is returned.
        home:     Override home directory for symlink-refusal check (R-7).
                  Defaults to Path.home().

    Returns:
        Empty string on success, or the A-8 skip message when is_tty=False.

    Raises:
        PathIntegrationError: If the rc file cannot be written.
        SymlinkRefusalError:  If rc_path is a symlink resolving outside home (R-7).
    """
    # A-8: non-TTY skips the rc write
    if not is_tty:
        return _NON_TTY_SKIP_MSG

    # R-7: symlink check
    _check_symlink(rc_path, home=home)

    # Read existing rc (empty string if absent)
    if rc_path.exists():
        existing = rc_path.read_text()
    else:
        existing = ""

    # Build the new block
    block = _build_block(shim_dir, shell)

    # Inject algorithm (R-4 corrupt-marker handling)
    new_content = _inject_block(existing, block)

    # Write (mkdir -p parent)
    try:
        rc_path.parent.mkdir(parents=True, exist_ok=True)
        rc_path.write_text(new_content)
    except OSError as exc:
        raise PathIntegrationError(
            f"could not write PATH block to {rc_path}; "
            f"add {shim_dir} to your PATH manually"
        ) from exc

    return ""


def _check_symlink(rc_path: Path, home: Path | None) -> None:
    """R-7: if rc_path is a symlink, resolve it and refuse if outside home."""
    if not rc_path.is_symlink():
        return
    resolved = rc_path.resolve()
    _home = home if home is not None else Path.home()
    try:
        resolved.relative_to(_home)
    except ValueError:
        raise SymlinkRefusalError(
            f"rc file {rc_path} is a symlink resolving to {resolved}, "
            f"which is outside your home directory ({_home}); "
            f"refusing to write PATH block to a symlink outside home"
        )


def _build_block(shim_dir: Path, shell: str) -> str:
    """Build the marker-delimited PATH block for the given shell."""
    if shell == "fish":
        path_line = f'fish_add_path --path "{shim_dir}"'
    else:
        # zsh and bash use the same export idiom
        path_line = f'export PATH="{shim_dir}:$PATH"'

    return f"{_OPEN_MARKER}\n{path_line}\n{_CLOSE_MARKER}\n"


def _inject_block(existing: str, block: str) -> str:
    """Inject or replace the marker block in existing rc content.

    Algorithm:
      1. Open marker present but no close marker → corrupt: strip from open
         marker to end of string, then append the new block (R-4).
      2. Both markers present → regex-replace the block (re.DOTALL).
      3. Neither present → append.
    """
    has_open = _OPEN_MARKER in existing
    has_close = _CLOSE_MARKER in existing

    if has_open and not has_close:
        # R-4: corrupt/partial marker — strip from the open marker to end
        idx = existing.index(_OPEN_MARKER)
        before = existing[:idx]
        return before + block

    if has_open and has_close:
        # Both markers present: replace the block (re.DOTALL for multi-line)
        pattern = re.escape(_OPEN_MARKER) + r".*?" + re.escape(_CLOSE_MARKER) + r"\n?"
        return re.sub(pattern, block, existing, flags=re.DOTALL)

    # Neither present: append
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + block


# ---------------------------------------------------------------------------
# Public API: remove_path_block
# ---------------------------------------------------------------------------


def remove_path_block(rc_path: Path) -> None:
    """Remove the trailhead-managed PATH block from a shell rc file.

    No-op if the file doesn't exist or the block is absent.
    The rest of the file is left byte-identical.

    Args:
        rc_path: Path to the shell rc file.
    """
    if not rc_path.exists():
        return

    content = rc_path.read_text()
    if _OPEN_MARKER not in content:
        return

    pattern = re.escape(_OPEN_MARKER) + r".*?" + re.escape(_CLOSE_MARKER) + r"\n?"
    new_content = re.sub(pattern, "", content, flags=re.DOTALL)
    rc_path.write_text(new_content)


# ---------------------------------------------------------------------------
# Public API: install_path_integration (for Slices 4/5)
# ---------------------------------------------------------------------------


def install_path_integration(
    wired_tools: dict[str, Path],
    trailhead_root: str,
    *,
    shell: str | None = None,
    rc_path: Path | None = None,
    is_tty: bool = True,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> PathIntegrationResult:
    """Create shims and inject the PATH block into the shell rc.

    This is the primary entry point for Slices 4 (install) and 5 (config
    path_integration on, update).

    Args:
        wired_tools:    Tool name → absolute bin/<tool> path for each wired tool.
        trailhead_root: Absolute trailhead repo root (hardcoded into shims, S-5).
        shell:          Override shell detection ("fish", "zsh", "bash").
                        Defaults to os.environ["SHELL"] basename detection.
        rc_path:        Override the rc file path (primarily for tests).
                        If None, the default rc for the detected shell is used.
        is_tty:         Interactive context flag (A-8).  When False, rc write is
                        skipped.
        env:            Environment dict for path resolution.
        home:           Override home directory (for symlink-refusal check, tests).

    Returns:
        PathIntegrationResult with shim_dir, rc_path (or None), and skip_message.

    Raises:
        PathIntegrationError: If the rc file cannot be written.
        ShimDenylistError:    If a tool name matches the denylist (S-6).
        SymlinkRefusalError:  If rc_path is a symlink outside home (R-7).
    """
    _env = env if env is not None else dict(os.environ)
    _home = home if home is not None else Path.home()

    # Detect shell
    _shell = shell or _detect_shell(_env)

    # Resolve rc path
    _rc_path = rc_path if rc_path is not None else _default_rc(_shell, _home)

    # S-5/S-6: create shims first (shim dir must exist before fish_add_path fires)
    shim_result = create_shims(wired_tools, trailhead_root, env=_env)

    # Inject PATH block (A-8: non-TTY skips rc write)
    skip_msg = inject_path_block(
        _rc_path, shim_result.shim_dir, _shell, is_tty=is_tty, home=_home
    )

    if skip_msg:
        return PathIntegrationResult(
            shim_dir=shim_result.shim_dir,
            rc_path=None,
            skip_message=skip_msg,
        )

    return PathIntegrationResult(
        shim_dir=shim_result.shim_dir,
        rc_path=_rc_path,
        skip_message=None,
    )


# ---------------------------------------------------------------------------
# Public API: remove_path_integration (for Slices 4/5)
# ---------------------------------------------------------------------------


def remove_path_integration(
    *,
    rc_path: Path | None = None,
    shell: str | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> None:
    """Remove the trailhead PATH block from the shell rc file.

    Used by `trailhead config path_integration off` and uninstall.

    Args:
        rc_path: Override the rc file path (for tests / explicit use).
        shell:   Override shell detection.  If rc_path is provided, this is
                 not needed.
        env:     Environment dict for shell detection fallback.
        home:    Override home directory.
    """
    _env = env if env is not None else dict(os.environ)
    _home = home if home is not None else Path.home()

    if rc_path is None:
        _shell = shell or _detect_shell(_env)
        rc_path = _default_rc(_shell, _home)

    remove_path_block(rc_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_shell(env: dict[str, str]) -> str:
    """Detect the shell from the SHELL env var basename.

    Returns "fish", "zsh", or "bash".  Defaults to "bash" if unrecognized.
    """
    shell_path = env.get("SHELL", "")
    name = Path(shell_path).name.lower()
    if name == "fish":
        return "fish"
    if name == "zsh":
        return "zsh"
    return "bash"


def _default_rc(shell: str, home: Path) -> Path:
    """Return the default rc file path for the given shell."""
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    if shell == "zsh":
        return home / ".zshrc"
    # bash: prefer ~/.bashrc; the caller can fall back to ~/.bash_profile
    return home / ".bashrc"
