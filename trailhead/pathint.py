"""PATH integration for trailhead — a shim dir + a brew-style `shellenv` endpoint.

trailhead does NOT edit your shell rc.  Instead, `trailhead install` builds a
shim directory under ``state_dir("trailhead")/bin/`` containing one wrapper per
selected CLI (camp/lore), and you add a single line to your shell profile:

    eval "$(/path/to/trailhead/bin/trailhead shellenv)"

``trailhead shellenv`` prints the export lines (TRAILHEAD_ROOT + the shim dir on
PATH) for your shell — exactly like ``brew shellenv``.  For the plugin CLIs
(camp/lore), the shim dir's *contents* encode which were selected
(``--no-camp`` etc.), so a single PATH entry is all the profile needs for
them, and re-running install updates it in place. The management CLI itself
(``trailhead``) is handled separately — see below — never via a shim.

camp is the forcing case: it's the front door, run outside any Claude Code
session.  Each shim hardcodes TRAILHEAD_ROOT so the monorepo is reachable
without CLAUDE_PLUGIN_ROOT; shellenv also exports it for good measure.

``shellenv`` also emits a ``trailhead`` shell function so the management CLI
itself is invokable by bare name — self-refreshing: it always calls
``<repo-root>/bin/trailhead``, re-resolved from the profile line's own root at
every shell startup, never from install-time state. Emitted unconditionally of
which CLIs were selected, and only when ``<repo-root>/bin/trailhead`` exists
and is executable — a non-editable pip install has no checkout alongside it,
so nothing is emitted and the rest of the output stays eval-valid. The
function never enters the shim dir or the CLI selection machinery.

Shell detection: ``$SHELL`` basename, with a ``--shell`` override (fish|zsh|bash).
  fish → ``set -gx`` / ``fish_add_path``;  zsh/bash → ``export``.

Shim names are checked against a denylist of system binaries.

Every path interpolated into eval'd output (TRAILHEAD_ROOT, the shim dir, the
trailhead() target) is checked for shell metacharacters (``"``, backtick,
``$``, newline, backslash) that could break out of their quoted context; a
match raises ``PathIntegrationError`` instead of emitting unsafe output.
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
    """Raised when the shim directory cannot be created/written, or when a
    value destined for interpolation into eval'd shellenv output contains a
    shell metacharacter that would make the output unsafe to eval."""


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
# Repo root
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Return the trailhead repo root containing this file, resolved.

    The single source of truth for "what checkout am I running from" —
    reused by shellenv_lines()'s default trailhead_root and by install.py's
    module-level repo-root constant, so the computation isn't duplicated
    (and doesn't drift) across call sites.
    """
    return Path(__file__).resolve().parent.parent


def trailhead_bin_executable(root: Path | str) -> bool:
    """Return True when ``<root>/bin/trailhead`` exists and is executable.

    The single source of truth for "does this checkout have a runnable
    ``bin/trailhead``" — used by ``_trailhead_function`` to decide whether
    shellenv emits the bare-name ``trailhead`` function, and by install's
    summary to decide whether it names ``trailhead`` as a command the
    shellenv line provides. A non-editable pip install has no checkout
    alongside it, so this is False there.
    """
    bin_path = Path(root) / "bin" / "trailhead"
    return bin_path.is_file() and os.access(bin_path, os.X_OK)


# ---------------------------------------------------------------------------
# Shell-injection guard
# ---------------------------------------------------------------------------

_UNSAFE_SHELL_CHARS = ('"', "`", "$", "\n", "\\")


def _reject_unsafe_for_eval(value: str, *, label: str) -> None:
    """Raise if ``value`` contains a character that would break out of the
    double-quoted context it's interpolated into in eval'd shellenv output.

    Applies to every path interpolated into shellenv output (TRAILHEAD_ROOT,
    the shim dir, and the trailhead() function body's target path) — a path
    containing one of these characters could inject arbitrary shell code into
    a profile that gets eval'd on every new shell.
    """
    for ch in _UNSAFE_SHELL_CHARS:
        if ch in value:
            raise PathIntegrationError(
                f"refused to emit shellenv output: {label} contains an unsafe "
                f"character ({ch!r}) that would break out of its quoted context"
            )


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
    _reject_unsafe_for_eval(trailhead_root, label="TRAILHEAD_ROOT")
    _reject_unsafe_for_eval(str(bin_path), label="the shim's target binary path")
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
# shell into the workspace dir — and `camp remove`/`camp rm` (run from inside a
# workspace) drops it back into the group's first-member repo — with NO subshell.
# Design notes (binding):
#   - `command camp` (not bare `camp`) avoids PATH recursion into this wrapper.
#   - The function runs in the CURRENT shell (brace body, not a `( … )` subshell),
#     so the `cd` reaches the user's interactive shell. Only the stdout capture
#     `$( … )` runs in a subshell; the `cd` itself does not.
#   - cd is quote-safe: bash/zsh `cd -- "$p"`; fish `cd -- $p` (fish cmd-sub splits
#     on newlines, not spaces, so a one-line path-with-spaces is a single element).
#   - The intercepted verbs are new|remove|rm — BOTH remove spellings, because the
#     wrapper sees the raw token; alias→canonical resolution happens inside the
#     CLI. Each such command prints a cd target as its ONLY stdout line (or, for
#     remove outside the workspace / any failure, nothing — empty capture means
#     no cd, so the shell stays put).
#   - resume is a SEPARATE intercepted branch with a two-line machine contract:
#     line 1 is the bare, unquoted absolute workspace root to cd into; line 2 is
#     a POSIX-shlex-quoted command to run there. The POSIX dialect `eval`s line 2
#     directly — safe because line 2 is already POSIX-shlex-quoted and eval is
#     one more parse pass through the SAME grammar, so `$( … )`, backticks, `$var`,
#     and quotes inside a quoted token stay literal. The fish dialect must NEVER
#     eval line 2 natively — fish-active syntax (`( … )`, `$var`) would be
#     reinterpreted before the POSIX quoting is honored — so it hands line 2 to
#     `sh -c` instead, keeping POSIX-shlex quoting authoritative in both dialects.
#   - The CAMP_SHELL_INTEGRATION marker is exported ONLY around the intercepted
#     camp INVOCATION (new|remove|rm and resume) so the handlers suppress their
#     bare-binary shellenv nudges; every other verb passes through with NO marker,
#     and — crucially — the resumed harness on line 2 runs WITHOUT it. Were it to
#     leak into that process, a nested `camp resume` inside the resumed session
#     would pass the integration guard with no wrapper listening and print its
#     inert machine lines as though they had worked.
#   - bash gets that scoping free from its `VAR=val cmd` prefix assignment. fish
#     cannot use `env VAR=val command camp` (env would try to exec a binary
#     literally named `command`), and a bare `set -lx` in the case body would stay
#     exported for the REST of the function, including the `sh -c` on line 2 — so
#     fish wraps just the invocation in a `begin … end` block and scopes the
#     `set -lx` to that block.
#
# These are LITERAL shell snippets: never let Python interpolate $ / {} here.

_CAMP_WRAPPER_POSIX = """\
camp() {
    case "$1" in
        new|remove|rm)
            local p
            p="$(CAMP_SHELL_INTEGRATION=1 command camp "$@")" || return $?
            if [ -n "$p" ]; then
                cd -- "$p" || return $?
            fi
            ;;
        resume)
            local out p cmd
            out="$(CAMP_SHELL_INTEGRATION=1 command camp "$@")" || return $?
            p="$(printf '%s\\n' "$out" | sed -n '1p')"
            cmd="$(printf '%s\\n' "$out" | sed -n '2p')"
            cd -- "$p" || return $?
            eval "$cmd"
            ;;
        *)
            command camp "$@"
            ;;
    esac
}
"""

_CAMP_WRAPPER_FISH = """\
function camp
    switch "$argv[1]"
        case new remove rm
            set -l p
            begin
                set -lx CAMP_SHELL_INTEGRATION 1
                set p (command camp $argv)
            end
            set -l rc $status
            if test $rc -ne 0
                return $rc
            end
            if test -n "$p"
                cd -- $p
            end
        case resume
            set -l lines
            begin
                set -lx CAMP_SHELL_INTEGRATION 1
                set lines (command camp $argv)
            end
            set -l rc $status
            if test $rc -ne 0
                return $rc
            end
            cd -- $lines[1]
            if test $status -ne 0
                return $status
            end
            sh -c $lines[2]
        case '*'
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
    root = trailhead_root or str(repo_root())

    _reject_unsafe_for_eval(root, label="TRAILHEAD_ROOT")
    _reject_unsafe_for_eval(str(shim_dir), label="the shim directory path")

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

    trailhead_fn = _trailhead_function(root, shell=_shell)
    return "\n".join(lines) + "\n" + wrapper + trailhead_fn


def _trailhead_function(root: str, *, shell: str) -> str:
    """Return the per-shell `trailhead` function snippet, or "" when
    ``<root>/bin/trailhead`` isn't an executable file (e.g. a non-editable
    pip install with no checkout alongside it) — output stays eval-valid
    either way.

    The function is self-refreshing: it always invokes the checkout the
    profile's shellenv line currently points at, re-resolved at every shell
    startup — never install-time state.
    """
    if not trailhead_bin_executable(root):
        return ""

    target = str(Path(root) / "bin" / "trailhead")
    _reject_unsafe_for_eval(target, label="the trailhead binary path")

    if shell == "fish":
        return f'\nfunction trailhead\n    "{target}" $argv\nend\n'
    return f'\ntrailhead() {{\n    "{target}" "$@"\n}}\n'
