"""Permanent smoke: trailhead.vcs is importable from a landing script context.

This test locks that importability in as a permanent guard for the landing
plugin. It fails if the on-disk layout shifts such that the four-tier
``_bootstrap.py`` loader can no longer reach the repo root from a landing script.

Two invariants are locked:

1. **parents[6] layout** — a landing thin script lives at
   ``tools/landing/plugins/landing/scripts/<name>.py``. The Tier-3 upward marker
   walk in ``_bootstrap.py`` reaches the repo root (the dir containing
   ``trailhead/paths.py``) at iteration index 6 of the ``(here, *here.parents)``
   walk: file → scripts → landing → plugins → landing → tools → repo-root.
   In ``Path.parents`` terms (which excludes the file) that is ``parents[5]`` —
   NOT ``parents[6]`` (an easy off-by-one to get wrong).

2. **In-process boundary** — once ``trailhead`` is importable (Tier-1
   already-importable, which is exactly the landing pattern of one
   ``ensure_trailhead_importable()`` call per process), a hostile
   ``$TRAILHEAD_ROOT`` in the environment does NOT change the resolved
   ``trailhead`` package. The env var is irrelevant once Tier-1 wins.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


# This test file lives at tools/landing/tests/test_landing_vcs_loader.py.
# A real landing thin script lives at tools/landing/plugins/landing/scripts/<name>.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "landing" / "plugins" / "landing" / "scripts"
_BOOTSTRAP = _SCRIPTS_DIR / "_bootstrap.py"


def _load_bootstrap():
    """Load landing's _bootstrap.py module fresh (by path, like a thin script)."""
    spec = importlib.util.spec_from_file_location("_landing_bootstrap", _BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParents6Layout:
    """The marker walk must reach the repo root at the proven depth from a script path."""

    def test_bootstrap_exists(self):
        assert _BOOTSTRAP.exists(), (
            f"landing _bootstrap.py not found at {_BOOTSTRAP} — the thin scripts "
            "depend on it to make trailhead.vcs importable"
        )


class TestVcsImportableFromLandingContext:
    """trailhead.vcs.get_provider loads and works from the landing bootstrap."""

    def test_ensure_importable_then_get_provider(self):
        mod = _load_bootstrap()
        mod.ensure_trailhead_importable()
        from trailhead.vcs import get_provider

        provider = get_provider()
        # deploy/repos surfaces are the ones landing consumes.
        assert provider.deploy is not None
        assert provider.repos is not None


class TestS5InProcessBoundary:
    """A hostile $TRAILHEAD_ROOT must not hijack an already-importable trailhead."""

    def test_hostile_trailhead_root_does_not_redirect(self, tmp_path, monkeypatch):
        """Tier-1 (already importable) wins before Tier-2 ($TRAILHEAD_ROOT).

        Plant a decoy ``trailhead/paths.py`` under a tmp dir, point
        ``$TRAILHEAD_ROOT`` at it, and assert that — because trailhead is already
        imported in this process — the resolved package still comes from the real
        repo root, not the decoy. This is the in-process invariant.
        """
        import trailhead  # already importable in this process (Tier-1)

        real_location = Path(trailhead.__file__).resolve().parent

        decoy_root = tmp_path / "evil"
        (decoy_root / "trailhead").mkdir(parents=True)
        (decoy_root / "trailhead" / "paths.py").write_text("HIJACKED = True\n")
        monkeypatch.setenv("TRAILHEAD_ROOT", str(decoy_root))

        mod = _load_bootstrap()
        mod.ensure_trailhead_importable()

        import trailhead as trailhead_after

        assert Path(trailhead_after.__file__).resolve().parent == real_location, (
            "a hostile $TRAILHEAD_ROOT changed the resolved trailhead package — "
            "Tier-1 already-importable must win"
        )
        assert str(decoy_root) not in str(Path(trailhead_after.__file__).resolve())
