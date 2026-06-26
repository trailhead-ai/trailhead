"""PATH integration for trailhead — a shim dir + a brew-style `shellenv` endpoint.

trailhead does NOT edit your shell rc.  Instead, `trailhead install` builds a
shim directory under ``state_dir("trailhead")/bin/`` containing one wrapper per
selected CLI (camp/lore), and you add a single line to your shell profile:

    eval "$(/path/to/trailhead/bin/trailhead shellenv)"

``trailhead shellenv`` prints the export lines (TRAILHEAD_ROOT + the shim dir on
PATH) for your shell — exactly like ``brew shellenv``.  Because the shim dir's
*contents* encode which CLIs were selected (``--no-camp`` etc.), a single PATH
entry is all the profile needs, and re-running install updates it in place.

camp is the forcing case: it's the front door, run outside any Claude Code
session.  Each shim hardcodes TRAILHEAD_ROOT so the monorepo is reachable
without CLAUDE_PLUGIN_ROOT; shellenv also exports it for good measure.

Shell detection: ``$SHELL`` basename, with a ``--shell`` override (fish|zsh|bash).
  fish → ``set -gx`` / ``fish_add_path``;  zsh/bash → ``export``.

Shim names are checked against a denylist of system binaries.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from trailhead.paths import ensure_dir, state_dir

# ---------------------------------------------------------------------------
# System-binary denylist
# ---------------------------------------------------------------------------

_SHIM_DENYLIST = frozenset(
    {
        "python",
        "python3",
        "git",
        "ssh",
        "curl",
        "install",
        "update",
        "sh",
        "bash",
        "fish",
        "zsh",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PathIntegrationError(Exception):
    """Raised when the shim directory cannot be created/written."""


class ShimDenylistError(Exception):
    """Raised when a requested shim name matches the system-binary denylist."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ShimDirResult:
    """Result of create_shims()."""

    shim_dir: Path
    shims: dict[str, Path]


# ---------------------------------------------------------------------------
# Shim directory
# ---------------------------------------------------------------------------


def resolve_shim_dir(*, env: dict[str, str] | None = None) -> Path:
    """Return the shim directory path (pure — does not create it).

    state_dir("trailhead")/bin/
    """
    _env = env if env is not None else dict(os.environ)
    return state_dir("trailhead", env=_env) / "bin"


def create_shims(
    wired_tools: dict[str, Path],
    trailhead_root: str,
    *,
    env: dict[str, str] | None = None,
) -> ShimDirResult:
    """Create the trailhead-managed shim directory, one wrapper per tool.

    Args:
        wired_tools:    Mapping of CLI name → absolute path to the tool's bin
                        wrapper (e.g. tools/camp/plugins/camp/bin/camp).
        trailhead_root: Absolute trailhead repo root, hardcoded into each shim.
        env:            Environment dict override for path resolution.

    Raises:
        ShimDenylistError:    If any name matches the system-binary denylist.
        PathIntegrationError: If the shim dir cannot be created/written.
    """
    for name in wired_tools:
        if name in _SHIM_DENYLIST:
            raise ShimDenylistError(
                f"refused to create shim named {name!r}: matches system-binary "
                f"denylist ({', '.join(sorted(_SHIM_DENYLIST))})"
            )

    shim_dir = resolve_shim_dir(env=env)
    try:
        ensure_dir(shim_dir, mode=0o700)
        shims: dict[str, Path] = {}
        for name, bin_path in wired_tools.items():
            shim_path = shim_dir / name
            shim_path.write_text(_shim_content(name, bin_path, trailhead_root))
            shim_path.chmod(0o700)
            shims[name] = shim_path
    except OSError as exc:
        raise PathIntegrationError(f"could not create shim dir at {shim_dir}: {exc}") from exc

    return ShimDirResult(shim_dir=shim_dir, shims=shims)


def _shim_content(name: str, bin_path: Path, trailhead_root: str) -> str:
    """Generate a single bash shim wrapper (TRAILHEAD_ROOT hardcoded)."""
    return (
        "#!/usr/bin/env bash\n"
        f"# trailhead-managed shim for {name}\n"
        f'export TRAILHEAD_ROOT="{trailhead_root}"\n'
        f'exec "{bin_path}" "$@"\n'
    )


# ---------------------------------------------------------------------------
# camp() cd-wrapper (validated across bash, zsh, and fish)
# ---------------------------------------------------------------------------
#
# shellenv emits a `camp()` shell function so `camp new <slug>` drops the parent
# shell into the workspace dir with NO subshell. Design notes (binding):
#   - `command camp` (not bare `camp`) avoids PATH recursion into this wrapper.
#   - The function runs in the CURRENT shell (brace body, not a `( … )` subshell),
#     so the `cd` reaches the user's interactive shell. Only the stdout capture
#     `$( … )` runs in a subshell; the `cd` itself does not.
#   - cd is quote-safe: bash/zsh `cd -- "$p"`; fish `cd -- $p` (fish cmd-sub splits
#     on newlines, not spaces, so a one-line path-with-spaces is a single element).
#   - The CAMP_SHELL_INTEGRATION marker is exported ONLY around the `camp new`
#     invocation so the handler suppresses its bare-binary shellenv nudge; every
#     other verb passes through with NO marker.
#   - fish MUST use function-scoped `set -lx` — `env VAR=val command camp` breaks
#     (env tries to exec a binary literally named `command`).
#
# These are LITERAL shell snippets: never let Python interpolate $ / {} here.

_CAMP_WRAPPER_POSIX = """\
camp() {
    if [ "$1" = "new" ]; then
        local p
        p="$(CAMP_SHELL_INTEGRATION=1 command camp "$@")" || return $?
        if [ -n "$p" ]; then
            cd -- "$p" || return $?
        fi
    else
        command camp "$@"
    fi
}
"""

_CAMP_WRAPPER_FISH = """\
function camp
    if test "$argv[1]" = new
        set -lx CAMP_SHELL_INTEGRATION 1
        set -l p (command camp $argv)
        set -l rc $status
        if test $rc -ne 0
            return $rc
        end
        if test -n "$p"
            cd -- $p
        end
    else
        command camp $argv
    end
end
"""


# ---------------------------------------------------------------------------
# shellenv (brew-style)
# ---------------------------------------------------------------------------


def detect_shell(env: dict[str, str] | None = None) -> str:
    """Detect the shell from $SHELL basename. Returns fish | zsh | bash (default bash)."""
    _env = env if env is not None else dict(os.environ)
    name = Path(_env.get("SHELL", "")).name.lower()
    if name in ("fish", "zsh"):
        return name
    return "bash"


def shellenv_lines(
    *,
    shell: str | None = None,
    env: dict[str, str] | None = None,
    trailhead_root: str | None = None,
) -> str:
    """Return the shell-eval lines that put the trailhead CLIs on PATH.

    Mirrors ``brew shellenv``: exports ``TRAILHEAD_ROOT`` and prepends the shim
    dir to PATH.  The user adds ``eval "$(.../bin/trailhead shellenv)"`` to their
    shell profile.

    Args:
        shell:          Override shell (fish|zsh|bash); defaults to $SHELL detection.
        env:            Environment dict for path resolution + shell detection.
        trailhead_root: Repo root to export; defaults to the repo containing this file.

    Returns:
        A newline-terminated string of shell statements.
    """
    _env = env if env is not None else dict(os.environ)
    _shell = shell or detect_shell(_env)
    shim_dir = resolve_shim_dir(env=_env)
    root = trailhead_root or str(Path(__file__).parent.parent)

    if _shell == "fish":
        lines = [
            f'set -gx TRAILHEAD_ROOT "{root}";',
            f'fish_add_path "{shim_dir}";',
        ]
        wrapper = _CAMP_WRAPPER_FISH
    else:
        lines = [
            f'export TRAILHEAD_ROOT="{root}";',
            f'export PATH="{shim_dir}:$PATH";',
        ]
        wrapper = _CAMP_WRAPPER_POSIX
    return "\n".join(lines) + "\n" + wrapper
