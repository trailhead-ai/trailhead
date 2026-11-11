"""One in-process lore runner for craft's tests.

``LoreRunner`` is a ``runner(argv, stdin=None) -> CompletedProcess`` — the seam
the follow-up scripts accept (``followup_gather.lore_runner`` is the production
default). It executes the real lore CLI inside this interpreter through lore's
own test harness (``_run_cli_in_process``, loaded by file path under a unique
module name because a bare ``import conftest`` would collide with craft's), so a
call costs no interpreter start or lore import.

Lore's harness applies none of lore's autouse isolation fixtures, so the runner
fences every call itself rather than trusting the calling test file to import
them:

- the child environment is built from scratch (PATH only is inherited):
  ``HOME``, ``XDG_STATE_HOME``, ``XDG_CONFIG_HOME`` and ``LORE_GROUPS_DIR`` all
  sit under the runner's root, ``LORE_PUBLISH_DISABLE=1`` is set, and the
  vault-guard variables are absent;
- ``lore.cli.publish._spawn_worker`` is replaced for the duration of the call by
  a recorder (``spawn_attempts`` lists what it was asked to spawn) that
  delegates to lore's own spawn only while ``LORE_PUBLISH_DISABLE`` is set and
  refuses otherwise, so a publish-triggering write can never start a worker;
- ``os.environ``, the working directory and stdin are restored by the harness.

The runner fails closed: before dispatching it raises ``LoreRunnerEscape`` if
any directory it hands lore, or any vault path in the config it is about to
hand lore, resolves outside its root.

Failure injection (``inject``) answers a matching call with a configured
returncode, stdout and stderr (or raises, as a missing or hung ``lore`` binary
would), instead of or after the real command.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_MODULE_NAME = "craft_tests_lore_runner_lore_conftest"

_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _LORE_TESTS / "conftest.py")
lore_conftest = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE_NAME] = lore_conftest
_spec.loader.exec_module(lore_conftest)

LORE_EMAIL = "tester@example.com"


class LoreRunnerEscape(RuntimeError):
    """A path lore would read or write lies outside the runner's root."""


class _Injection:
    def __init__(self, match, returncode, stdout, stderr, raises, run_first, times):
        self.match = match
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self.run_first = run_first
        self.times = times

    def matches(self, argv, stdin):
        if self.times is not None and self.times <= 0:
            return False
        if callable(self.match):
            return bool(self.match(argv, stdin))
        return argv[: len(self.match)] == list(self.match)


class LoreRunner:
    """Run real lore in-process, confined to ``root``.

    ``vaults`` names the vaults the config declares, each created empty under
    ``root/vaults/<name>``; the first has the ``default`` scope, the rest
    ``team``. ``cwd`` defaults to ``root``.
    """

    def __init__(self, root, vaults=("default",), cwd=None):
        self.root = Path(root)
        self.cwd = Path(cwd) if cwd is not None else self.root
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.config_home = self.root / "xdg_config"
        self.groups = self.root / "groups"
        for d in (self.home, self.state, self.groups):
            d.mkdir(parents=True, exist_ok=True)
        self._vault_paths = {}
        triples = []
        for i, name in enumerate(vaults):
            path = self.root / "vaults" / name
            path.mkdir(parents=True, exist_ok=True)
            self._vault_paths[name] = path
            triples.append((name, "default" if i == 0 else "team", path))
        self.write_config(triples)
        self.calls = []
        self.spawn_attempts = []
        self._injections = []

    def vault_path(self, name):
        return self._vault_paths[name]

    def write_config(self, vaults):
        """Rewrite lore's config with ``(name, scope, path)`` triples. Unchecked:
        the confinement check runs on the next call."""
        lore_conftest.write_vault_config(self.config_home, vaults)

    def env(self):
        """The environment every call runs under; also what a real subprocess
        comparison should be given."""
        return {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.config_home),
            "LORE_GROUPS_DIR": str(self.groups),
            "LORE_PUBLISH_DISABLE": "1",
            "LORE_EMAIL": LORE_EMAIL,
        }

    def inject(self, match, *, returncode=1, stdout="", stderr="", raises=None,
               run_first=False, times=None):
        """Answer calls whose argv starts with ``match`` (a list) or for which
        ``match(argv, stdin)`` is true. ``raises`` is an exception instance
        raised instead of returning (``FileNotFoundError`` for a missing lore,
        ``subprocess.TimeoutExpired`` for a hung one). ``run_first`` runs the
        real command and then reports the injected failure, for a write that
        landed but reported an error. ``times`` limits how many calls it
        answers. The first matching injection wins."""
        self._injections.append(
            _Injection(match, returncode, stdout, stderr, raises, run_first, times)
        )

    def __call__(self, argv, stdin=None):
        argv = list(argv)
        self.calls.append((argv, stdin))
        self._assert_confined()
        injection = next((i for i in self._injections if i.matches(argv, stdin)), None)
        if injection is not None:
            if injection.times is not None:
                injection.times -= 1
            if injection.raises is not None:
                raise injection.raises
            if injection.run_first:
                self._run_real(argv, stdin)
            return subprocess.CompletedProcess(
                ["lore", *argv], injection.returncode, injection.stdout, injection.stderr
            )
        return self._run_real(argv, stdin)

    def _run_real(self, argv, stdin):
        from lore.cli import publish

        original = publish._spawn_worker
        publish._spawn_worker = lambda argv: self._refuse_spawn(original, argv)
        try:
            done = lore_conftest._run_cli_in_process(argv, self.env(), stdin, self.cwd)
        finally:
            publish._spawn_worker = original
        return subprocess.CompletedProcess(["lore", *argv], done.returncode, done.stdout, done.stderr)

    def _refuse_spawn(self, original, argv):
        """Record the request, then hand it to lore's own spawn, which under
        ``LORE_PUBLISH_DISABLE`` prints its notice and returns without a process
        (so stderr matches a real run). Without the variable it would spawn, so
        refuse outright."""
        self.spawn_attempts.append(list(argv))
        if not os.environ.get("LORE_PUBLISH_DISABLE"):
            raise LoreRunnerEscape("publish spawn requested without LORE_PUBLISH_DISABLE")
        original(argv)

    def _assert_confined(self):
        root = self.root.resolve()

        def inside(path):
            return Path(path).resolve().is_relative_to(root)

        env = self.env()
        for key in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "LORE_GROUPS_DIR"):
            if not inside(env[key]):
                raise LoreRunnerEscape(f"{key}={env[key]} is outside {root}")
        if not inside(self.cwd):
            raise LoreRunnerEscape(f"cwd {self.cwd} is outside {root}")
        config = Path(env["XDG_CONFIG_HOME"]) / "lore" / "config.json"
        if not config.exists():
            return
        try:
            vaults = json.loads(config.read_text(encoding="utf-8"))["vaults"]
            paths = [v["path"] for v in vaults]
        except (ValueError, KeyError, TypeError) as exc:
            raise LoreRunnerEscape(f"cannot verify vault paths in {config}: {exc}") from exc
        for path in paths:
            if not inside(path):
                raise LoreRunnerEscape(f"vault path {path} is outside {root}")


def seam_env(runner, tmp_path):
    """The runner's fenced environment, with a `lore` shim running this checkout's lore
    first on PATH: what a real subprocess (a script, or `lore`) is given. Raises
    ``LoreRunnerEscape`` if the runner is not confined, as a call through it would."""
    runner._assert_confined()
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "lore"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{lore_conftest.CLI_PATH}" "$@"\n')
    shim.chmod(0o755)
    env = runner.env()
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
    return env


@pytest.fixture
def lore_runner(tmp_path):
    """A ``LoreRunner`` with one default vault, confined to this test's ``tmp_path``."""
    return LoreRunner(tmp_path)
