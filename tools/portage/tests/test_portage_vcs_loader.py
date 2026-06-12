"""U1 permanent smoke: trailhead.vcs is importable from a portage script context.

This test encodes the U1 assumption-prover result (VALIDATED 2026-06-12) as a
permanent guard. It fails if the on-disk layout shifts such that the four-tier
``_bootstrap.py`` loader can no longer reach the repo root from a portage script.

Three invariants are locked:

1. **parents[6] layout** — a portage thin script lives at
   ``tools/portage/plugins/portage/scripts/<name>.py``. The Tier-2 upward marker
   walk in ``_bootstrap.py`` reaches the repo root (the dir containing
   ``trailhead/paths.py``) at ``parents[6]`` from the script's resolved path:
   file → scripts → portage → plugins → portage → tools → repo-root.

2. **S-5 in-process boundary** — once ``trailhead`` is importable (Tier-1
   already-importable, which is exactly the portage pattern of one
   ``ensure_trailhead_importable()`` call per process), a hostile
   ``$TRAILHEAD_ROOT`` in the environment does NOT change the resolved
   ``trailhead`` package. The env var is irrelevant once Tier-1 wins.

3. **S-5 cold-start boundary** — on a fresh process where Tier-1 fails (the
   normal thin-script state), the Tier-2 ``__file__`` walk wins over a hostile
   Tier-3 ``$TRAILHEAD_ROOT``; the env var is only a fallback when the walk
   finds no co-located checkout (camp's shim flow). See
   ``TestColdStartTier2Hardening`` below.

Depth note: the prover's "parents[6]" counts the file itself as element 0 of the
``(here, *here.parents)`` iteration the bootstrap walks — file(0) → scripts(1) →
portage(2) → plugins(3) → portage(4) → tools(5) → repo-root(6). In ``Path.parents``
terms (which excludes the file) that is ``parents[5]``. This test asserts the
marker lands at iteration index 6 of ``(here, *here.parents)`` — i.e. it mirrors
exactly what ``_bootstrap.py`` does, so a layout shift trips it.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# This test file lives at tools/portage/tests/test_portage_vcs_loader.py.
# A real portage thin script lives at tools/portage/plugins/portage/scripts/<name>.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage" / "scripts"
_BOOTSTRAP = _SCRIPTS_DIR / "_bootstrap.py"


def _load_bootstrap():
    """Load portage's _bootstrap.py module fresh (by path, like a thin script)."""
    spec = importlib.util.spec_from_file_location("_portage_bootstrap", _BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParents6Layout:
    """The marker walk must reach the repo root at the proven depth from a script path."""

    def test_bootstrap_exists(self):
        assert _BOOTSTRAP.exists(), (
            f"portage _bootstrap.py not found at {_BOOTSTRAP} — the thin scripts "
            "depend on it to make trailhead.vcs importable"
        )

    def test_marker_reachable_at_proven_depth(self):
        """A script at scripts/<name>.py reaches trailhead/paths.py at the proven depth.

        Mirrors the bootstrap's own walk over ``(here, *here.parents)``:
        file(0) → scripts(1) → portage(2) → plugins(3) → portage(4) → tools(5)
        → repo-root(6). The marker must be found at iteration index 6 — if anyone
        restructures the plugin layout, the index shifts and this trips.
        """
        fake_script = (_SCRIPTS_DIR / "some_thin_script.py").resolve()
        chain = [fake_script, *fake_script.parents]
        found_index = next(
            (i for i, p in enumerate(chain) if (p / "trailhead" / "paths.py").exists()),
            None,
        )
        assert found_index == 6, (
            f"expected the trailhead/paths.py marker at iteration index 6 of "
            f"(here, *here.parents) from a portage script, got {found_index}; "
            "the four-tier bootstrap marker walk depends on this depth"
        )
        assert chain[found_index] == _REPO_ROOT


class TestVcsImportableFromPortageContext:
    """trailhead.vcs.get_provider loads and works from the portage bootstrap."""

    def test_ensure_importable_then_get_provider(self):
        mod = _load_bootstrap()
        mod.ensure_trailhead_importable()
        from trailhead.vcs import get_provider

        provider = get_provider()
        # repos/pr/ci surfaces are the ones portage consumes.
        assert provider.repos is not None
        assert provider.pr is not None
        assert provider.ci is not None


class TestS5InProcessBoundary:
    """A hostile $TRAILHEAD_ROOT must not hijack an already-importable trailhead."""

    def test_hostile_trailhead_root_does_not_redirect(self, tmp_path, monkeypatch):
        """Tier-1 (already importable) wins before Tier-2 ($TRAILHEAD_ROOT).

        Plant a decoy ``trailhead/paths.py`` under a tmp dir, point
        ``$TRAILHEAD_ROOT`` at it, and assert that — because trailhead is already
        imported in this process — the resolved package still comes from the real
        repo root, not the decoy. This is the S-5 in-process invariant.
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
            "Tier-1 already-importable must win (S-5 boundary)"
        )
        assert str(decoy_root) not in str(Path(trailhead_after.__file__).resolve())


# ---------------------------------------------------------------------------
# Cold-start Tier-2/3 hardening
# ---------------------------------------------------------------------------
#
# TestS5InProcessBoundary above only covers the WARM path: a process where
# trailhead is *already* imported, so Tier-1 short-circuits before any env var
# is read. But a real thin-script invocation is a fresh, COLD Python process —
# trailhead is not yet importable, Tier-1 fails, and the loader actually walks
# its tiers. That is the normal execution path, and it is the redirect surface.
#
# These tests exercise the genuinely cold path in a child interpreter (the only
# faithful way to defeat Tier-1, since the pytest process already imported
# trailhead). They lock the S-5 cold-start hardening: the __file__ walk (Tier 2)
# wins over a hostile $TRAILHEAD_ROOT (Tier 3), while the camp shim's
# walk-finds-nothing → env-fallback flow still works.


def _run_cold(bootstrap: Path, *, env_overrides: dict[str, str], cwd: Path):
    """Run a cold child interpreter that bootstraps trailhead via ``bootstrap``.

    Tier-1 is defeated by using a child process with PYTHONPATH stripped (the
    parent pytest process has trailhead imported; the child must not). On
    success the child prints ``OK:<resolved repo root>``.
    """
    driver = textwrap.dedent(
        f"""
        import importlib.util, sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("_cold_bootstrap", r"{bootstrap}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.ensure_trailhead_importable()
        import trailhead.paths
        print("OK:" + str(Path(trailhead.paths.__file__).resolve().parent.parent))
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", driver],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


class TestColdStartTier2Hardening:
    """The __file__ walk must win over a hostile $TRAILHEAD_ROOT on a cold process."""

    def test_walk_wins_over_hostile_env_when_run_from_checkout(self, tmp_path):
        """Cold process, real script inside the checkout, hostile $TRAILHEAD_ROOT.

        The walk anchors on the script's own location (inside the repo), so the
        decoy env root is never consulted. This is the S-5 cold-start invariant
        the warm in-process test cannot reach.
        """
        decoy_root = tmp_path / "evil"
        (decoy_root / "trailhead").mkdir(parents=True)
        (decoy_root / "trailhead" / "paths.py").write_text("HIJACKED = True\n")

        result = _run_cold(
            _BOOTSTRAP,
            env_overrides={"TRAILHEAD_ROOT": str(decoy_root)},
            cwd=tmp_path,
        )

        assert result.returncode == 0, (
            f"cold bootstrap failed unexpectedly:\n{result.stderr}"
        )
        assert result.stdout.strip() == f"OK:{_REPO_ROOT}", (
            "a hostile $TRAILHEAD_ROOT redirected a cold-start import — the "
            f"__file__ walk must win (got {result.stdout!r})\n{result.stderr}"
        )
        assert str(decoy_root) not in result.stdout

    def test_env_fallback_used_when_walk_finds_nothing(self, tmp_path):
        """camp shim flow: bootstrap installed outside any checkout uses $TRAILHEAD_ROOT.

        Copy the loader to an isolated dir with no monorepo ancestor (so the walk
        exhausts and finds nothing), point $TRAILHEAD_ROOT at the real repo, and
        assert it resolves from the env root. This proves the swap did not break
        camp's front-door flow.
        """
        isolated = tmp_path / "installed" / "scripts"
        isolated.mkdir(parents=True)
        shutil.copyfile(_BOOTSTRAP, isolated / "_bootstrap.py")

        result = _run_cold(
            isolated / "_bootstrap.py",
            env_overrides={"TRAILHEAD_ROOT": str(_REPO_ROOT)},
            cwd=tmp_path,
        )

        assert result.returncode == 0, (
            f"env fallback failed — camp shim flow broken:\n{result.stderr}"
        )
        assert result.stdout.strip() == f"OK:{_REPO_ROOT}", (
            f"expected resolution from $TRAILHEAD_ROOT, got {result.stdout!r}\n"
            f"{result.stderr}"
        )

    def test_invalid_env_with_no_walk_target_exits_legibly(self, tmp_path):
        """Walk finds nothing and $TRAILHEAD_ROOT lacks the marker → legible exit 1.

        A stale or hostile env value that does not actually contain
        trailhead/paths.py must never be inserted on sys.path; the loader exits 1
        with a tool-named message instead of a raw ImportError.
        """
        isolated = tmp_path / "installed" / "scripts"
        isolated.mkdir(parents=True)
        shutil.copyfile(_BOOTSTRAP, isolated / "_bootstrap.py")

        empty_root = tmp_path / "empty"  # exists but has no trailhead/paths.py
        empty_root.mkdir()

        result = _run_cold(
            isolated / "_bootstrap.py",
            env_overrides={"TRAILHEAD_ROOT": str(empty_root)},
            cwd=tmp_path,
        )

        assert result.returncode == 1, (
            f"expected a legible exit 1, got {result.returncode}\n{result.stdout}\n"
            f"{result.stderr}"
        )
        assert "portage: the trailhead shared library isn't importable" in result.stderr
