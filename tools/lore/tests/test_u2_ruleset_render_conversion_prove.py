"""PROVER ARTIFACT — resolves Known Unknown U2 for the doctrine-primer plan.

Ephemeral: this file exists only to validate/invalidate the assumption behind
Slice 2 (converting ``agent_ruleset.RULESET_CONTENT`` from a module constant
into a ``render_ruleset_content()`` function with a deferred
``from ..cli.dispatch import build_parser`` import). It is NOT part of the
permanent suite and should be deleted by the executor once Slice 2 lands its
own real tests (per the Slice 2 test contract in the plan).

U2 claims:
  1. ``import lore.config.agent_ruleset`` does not transitively import
     ``lore.cli.dispatch`` today (no live cycle to dodge via deferral).
  2. A deferred (function-body) import of ``build_parser`` only pulls in
     ``lore.cli.dispatch`` at CALL time, not at module-import time.
  3. Two calls to a render function built this way return byte-identical
     strings (determinism, not staticness).
  4. The harness's ``user_ruleset_status`` does plain ``str`` equality with no
     assumption baked in about where the string came from.

This file creates two tiny THROWAWAY sibling modules inside the real
``lore/config/`` package directory (never modifying any existing source file)
so relative imports (``..cli.dispatch``) resolve exactly as they would for a
real ``render_ruleset_content()``. Both are written and removed within a
fixture's try/finally, so they never survive a test run and are never
committed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CONFIG_DIR = PLUGIN_ROOT / "lore" / "config"

_DEFERRED_MODULE_NAME = "_u2_prove_deferred"
_EAGER_MODULE_NAME = "_u2_prove_eager"

_DEFERRED_SRC = '''\
"""Throwaway U2-prover module: mimics the planned render_ruleset_content()."""
from __future__ import annotations

from .agent_ruleset import _WRITE_PROHIBITION, PRIMER


def render():
    # Deferred import: only touches lore.cli.dispatch when this function is
    # actually CALLED, never at module-import time.
    from ..cli.dispatch import build_parser
    from .command_reference import build_reference

    parser = build_parser()
    return f"{_WRITE_PROHIBITION}\\n{PRIMER}\\n{build_reference(parser)}"
'''

_EAGER_SRC = '''\
"""Throwaway U2-prover module: the SAME shape but with an eager top-level
import, to empirically check whether that actually trips a live cycle today.
"""
from __future__ import annotations

from ..cli.dispatch import build_parser  # eager, at module top


def render():
    return build_parser() is not None
'''


@pytest.fixture
def deferred_module(tmp_path):
    path = CONFIG_DIR / f"{_DEFERRED_MODULE_NAME}.py"
    path.write_text(_DEFERRED_SRC, encoding="utf-8")
    try:
        yield f"lore.config.{_DEFERRED_MODULE_NAME}"
    finally:
        path.unlink(missing_ok=True)
        pycache = CONFIG_DIR / "__pycache__"
        if pycache.is_dir():
            for f in pycache.glob(f"{_DEFERRED_MODULE_NAME}.*"):
                f.unlink(missing_ok=True)


@pytest.fixture
def eager_module(tmp_path):
    path = CONFIG_DIR / f"{_EAGER_MODULE_NAME}.py"
    path.write_text(_EAGER_SRC, encoding="utf-8")
    try:
        yield f"lore.config.{_EAGER_MODULE_NAME}"
    finally:
        path.unlink(missing_ok=True)
        pycache = CONFIG_DIR / "__pycache__"
        if pycache.is_dir():
            for f in pycache.glob(f"{_EAGER_MODULE_NAME}.*"):
                f.unlink(missing_ok=True)


def _run_py(code: str) -> subprocess.CompletedProcess:
    """Run *code* in a fresh interpreter with PLUGIN_ROOT on sys.path.

    A fresh process is required so sys.modules pollution from other tests in
    this suite (which may have already imported lore.cli.dispatch) can't hide
    a real cycle or fake a clean result.
    """
    full_code = f"import sys; sys.path.insert(0, {str(PLUGIN_ROOT)!r})\n{code}"
    return subprocess.run(
        [sys.executable, "-c", full_code],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# 1. Today: importing agent_ruleset does NOT pull in lore.cli.dispatch.
# ---------------------------------------------------------------------------


def test_agent_ruleset_import_today_has_no_cli_dispatch_side_effect():
    res = _run_py(
        "import lore.config.agent_ruleset\n"
        "print('lore.cli.dispatch' in sys.modules)\n"
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "False", (
        f"lore.config.agent_ruleset must not transitively import "
        f"lore.cli.dispatch today; stdout={res.stdout!r} stderr={res.stderr!r}"
    )


# ---------------------------------------------------------------------------
# 2. A deferred import only loads cli.dispatch at CALL time.
# ---------------------------------------------------------------------------


def test_deferred_render_does_not_import_dispatch_at_module_load(deferred_module):
    res = _run_py(
        f"import {deferred_module} as m\n"
        "print('lore.cli.dispatch' in sys.modules)\n"
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "False", (
        f"importing the deferred-render module must not eagerly import "
        f"lore.cli.dispatch; stdout={res.stdout!r} stderr={res.stderr!r}"
    )


def test_deferred_render_imports_dispatch_only_after_call(deferred_module):
    res = _run_py(
        f"import {deferred_module} as m\n"
        "before = 'lore.cli.dispatch' in sys.modules\n"
        "content = m.render()\n"
        "after = 'lore.cli.dispatch' in sys.modules\n"
        "print(before, after)\n"
        "print('---CONTENT-STARTS---')\n"
        "print(content[:200])\n"
    )
    assert res.returncode == 0, res.stderr
    first_line = res.stdout.splitlines()[0]
    assert first_line == "False True", (
        f"dispatch must load lazily, only once render() is called; "
        f"got {first_line!r}; full stdout={res.stdout!r} stderr={res.stderr!r}"
    )


def test_deferred_render_is_byte_identical_across_two_calls(deferred_module):
    res = _run_py(
        f"import {deferred_module} as m\n"
        "a = m.render()\n"
        "b = m.render()\n"
        "print(a == b)\n"
        "print(len(a), len(b))\n"
    )
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == "True", (
        f"two render() calls must be byte-identical (deterministic, not "
        f"necessarily static); stdout={res.stdout!r} stderr={res.stderr!r}"
    )


def test_deferred_render_contains_all_three_sections(deferred_module):
    res = _run_py(
        f"import {deferred_module} as m\n"
        "content = m.render()\n"
        "from lore.config.agent_ruleset import _WRITE_PROHIBITION, PRIMER\n"
        "print(content.startswith(_WRITE_PROHIBITION))\n"
        "print(PRIMER in content)\n"
        "print('## Lore command reference (generated)' in content)\n"
    )
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines == ["True", "True", "True"], (
        f"rendered content must contain, in order, the write-prohibition "
        f"(first), the primer, and the generated command-reference block; "
        f"got {lines!r}; stderr={res.stderr!r}"
    )


# ---------------------------------------------------------------------------
# 3. Empirically check whether an EAGER top-level import direction would
#    actually trip a live cycle today (not just "is it good layering").
# ---------------------------------------------------------------------------


def test_eager_top_level_import_does_not_currently_cycle(eager_module):
    """Surprise-check: does a naive (non-deferred) conversion actually fail
    with an ImportError today, or does it just violate layering discipline?

    Traced statically first (see prover report): none of the cli submodules
    dispatch.py imports at module level (areas/flush/init/record/search/
    session/sync/task/vault) import ``lore.config.agent_ruleset`` (or
    ``lore.config`` generally) at THEIR module level — those imports are all
    deferred inside function bodies (e.g. init.py's cmd_init/cmd_status).  So
    an eager top-level import in the config layer should resolve cleanly
    today. This test confirms that empirically rather than trusting the trace.
    """
    res = _run_py(f"import {eager_module} as m\nprint(m.render())\n")
    assert res.returncode == 0, (
        f"expected the eager-import variant to succeed today (no LIVE cycle "
        f"yet — see docstring); if this FAILS, that's a more urgent finding "
        f"than U2 assumed. stderr={res.stderr!r}"
    )
    assert res.stdout.strip() == "True"


# ---------------------------------------------------------------------------
# 4. user_ruleset_status does plain str equality, agnostic to content origin.
# ---------------------------------------------------------------------------


def test_harness_user_ruleset_status_is_plain_string_equality(tmp_path, deferred_module):
    """Confirms base.py:144's contract directly: user_ruleset_status(name, content)
    only ever compares ``content`` (a str) against the file on disk — swapping
    a module-constant read for a function-call result requires zero changes
    to this comparison logic.
    """
    sys.path.insert(0, str(PLUGIN_ROOT))
    try:
        import importlib

        deferred = importlib.import_module(deferred_module)
        rendered = deferred.render()

        from trailhead.harness.claude_code import ClaudeCodeHarness

        harness = ClaudeCodeHarness()
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        env = {"TRAILHEAD_CLAUDE_DIR": str(home / ".claude")}

        assert harness.user_ruleset_status("probe", rendered, env=env) == "missing"
        harness.install_user_ruleset("probe", rendered, env=env)
        assert harness.user_ruleset_status("probe", rendered, env=env) == "current"
        # A second, independently-rendered string (not the same object) must
        # compare equal too — proves it's real string equality, not identity.
        rendered_again = deferred.render()
        assert rendered_again is not rendered
        assert harness.user_ruleset_status("probe", rendered_again, env=env) == "current"

        path = harness.user_ruleset_path("probe", env=env)
        path.write_text(path.read_text() + "\nmutated\n")
        assert harness.user_ruleset_status("probe", rendered, env=env) == "stale"
    finally:
        sys.modules.pop(deferred_module, None)
