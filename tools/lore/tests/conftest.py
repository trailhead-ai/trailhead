"""Shared pytest fixtures and import helpers for the lore test suite."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def run_cli(args, *, vault, state_dir, stdin_text=None, env_extra=None):
    """Run the lore CLI as a subprocess; returns CompletedProcess.

    Shared harness for the record/session CLI tests — injects LORE_VAULT +
    XDG_STATE_HOME (+ a stable LORE_EMAIL) so tests never touch the real vault or
    state dir. ``env_extra`` overlays extra env vars.
    """
    full_env = dict(os.environ)
    full_env["LORE_VAULT"] = str(vault)
    full_env["XDG_STATE_HOME"] = str(state_dir)
    full_env["LORE_EMAIL"] = "tester@example.com"
    if env_extra:
        full_env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=stdin_text,
    )


def make_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(vault_dir, state_dir)`` under ``tmp_path``, creating both."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return vault, state


def load_script(name: str):
    """Load a module from plugins/lore/scripts/ by stem, freshly each call."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
