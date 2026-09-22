"""Codex harness implementation — detection and home resolution skeleton.

This module owns everything Codex-specific about being recognised through the
trailhead :class:`~trailhead.harness.base.Harness` seam (Axiom 1). Nothing
Codex-specific lives outside this file — the shared install/compose/wire/
doctor path talks to it only through the generic interface.

Home resolution
----------------
Codex's own home directory is resolved by ``codex_home``: ``CODEX_HOME`` when
set (must be absolute), else ``HOME``/``USERPROFILE`` joined with ``.codex`` —
the same variable Codex itself reads, so trailhead needs no Codex-only test
seam the way it does for Claude Code's ``TRAILHEAD_CLAUDE_DIR``. This resolver
NEVER falls back to :func:`pathlib.Path.home`: a caller must inject an
environment (real or test-pinned) explicitly, so a missing injection fails
loudly instead of silently reading (or, worse, writing under) the operator's
real Codex home.

Detection
---------
``CodexHarness.detect`` is true when either signal is present: a ``codex``
executable on the given environment's ``PATH``, or a ``config.toml`` file
under the resolved Codex home. A bare, empty home directory (no
``config.toml``) is NOT detected — Codex has never been configured there.

Install-surface skeleton
-------------------------
Every registration/install method below is a vacuous, harness-agnostic-safe
default (no-op write, ``False``/``[]`` read) so this class can be instantiated
and registered before the create-phase (compose a manifest Codex can read,
register it, install/rewire/uninstall tools) is implemented. This makes
``trailhead doctor`` report Codex as detected with nothing installed, which is
the honest state until those methods are filled in — never a silent write to
a harness that can't yet read it, and never a raised error that would abort
``trailhead install``/``trailhead update`` on any machine with Codex detected.
Every other seam method (transcripts, live-session enumeration, launch,
account identity/authentication) stays at the base class's default too; this
skeleton adds only detection and registry presence.
"""

from __future__ import annotations

import os
from pathlib import Path

from trailhead.harness.base import Harness, HarnessError

#: The file whose presence under a Codex home means Codex has been configured
#: there — read by :meth:`CodexHarness.detect` as the "home has state" signal.
_CODEX_CONFIG_FILENAME = "config.toml"

#: The executable name looked up on the given environment's ``PATH`` — the
#: "Codex CLI is installed" signal for :meth:`CodexHarness.detect`.
_CODEX_EXECUTABLE_NAME = "codex"

#: A directory under ``.codex`` Codex itself creates. See :mod:`trailhead.harness`.
_CODEX_HOME_SUBDIR = ".codex"


def codex_home(env: dict[str, str]) -> Path:
    """Resolve Codex's home directory from *env* — the single choke point every
    other Codex-home-relative resolver in this seam calls through.

    Precedence: ``CODEX_HOME`` (the variable Codex itself reads) when set, else
    ``HOME``/``USERPROFILE`` joined with ``.codex``. Deliberately never falls
    back to :func:`pathlib.Path.home` — every caller must inject an
    environment, so a test that forgets to pin one fails loudly (via the
    ``HarnessError`` below) rather than silently resolving the operator's real
    Codex home.

    Raises:
        HarnessError: if ``CODEX_HOME`` is set but not an absolute path (a
            relative value would resolve differently depending on the
            launching process's working directory — mirroring how Claude
            Code's declared-account resolution refuses a relative value), or
            if neither ``CODEX_HOME`` nor ``HOME``/``USERPROFILE`` is set.
    """
    override = env.get("CODEX_HOME")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise HarnessError(
                f"CODEX_HOME={override!r} is not an absolute path. Codex resolves "
                "its home from this directory exactly as given, so a relative "
                "value would resolve differently depending on the launching "
                "process's working directory."
            )
        return path
    home = env.get("HOME") or env.get("USERPROFILE")
    if not home:
        raise HarnessError(
            "cannot resolve the Codex home: neither CODEX_HOME nor HOME/USERPROFILE "
            "is set in the given environment."
        )
    return Path(home) / _CODEX_HOME_SUBDIR


def _codex_on_path(env: dict[str, str]) -> bool:
    """True if an executable named ``codex`` is on the given env's ``PATH``.

    Mirrors the executable check ``trailhead.pathint`` uses for its own shims
    (``is_file()`` and ``os.access(..., os.X_OK)``) rather than shelling out to
    ``shutil.which``, which reads the real process environment instead of the
    injected one this seam is built to test against.
    """
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / _CODEX_EXECUTABLE_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return True
    return False


class CodexHarness(Harness):
    """Recognise Codex through the trailhead harness seam.

    See the module docstring for what's implemented (detection, home
    resolution) versus vacuous (every install/registration method) at this
    stage.
    """

    name = "codex"

    @classmethod
    def detect(cls, env: dict[str, str]) -> bool:
        if _codex_on_path(env):
            return True
        return (codex_home(env) / _CODEX_CONFIG_FILENAME).is_file()

    # -- manifest ---------------------------------------------------------

    def generate_manifest(self, tools: list[str], composed_root: Path) -> None:
        return None

    # -- registration state (on-disk truth) --------------------------------

    def is_registered(self, composed_root: Path, *, env: dict[str, str] | None = None) -> bool:
        return False

    def is_installed(
        self, tool: str, composed_root: Path, *, env: dict[str, str] | None = None
    ) -> bool:
        return False

    def installed_tools(
        self, composed_root: Path, *, env: dict[str, str] | None = None
    ) -> list[str]:
        return []

    # -- install / uninstall ------------------------------------------------

    def register(
        self, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def install_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def rewire_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def unregister_tool(
        self, tool: str, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None

    def unregister_marketplace(
        self, composed_root: Path, *, runner=None, env: dict[str, str] | None = None
    ) -> None:
        return None
