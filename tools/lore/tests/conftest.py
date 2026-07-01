"""Shared pytest fixtures and import helpers for the lore test suite."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"

# Makes the `lore` package (plugins/lore/lore/) importable by its dotted name,
# for modules already migrated off the flat scripts/ bag — see load_script().
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def write_default_config(config_home: Path, vault_path: Path) -> None:
    """Seed config.json under config_home with a single default-scope vault.

    Writes ``config_home/lore/config.json`` (matching ``_resolve_config_path``'s
    ``XDG_CONFIG_HOME → <home>/lore/config.json`` derivation) so that the CLI
    subprocess resolves the active vault from config.  Idempotent: overwrites any
    existing config.json.

    Config is the only resolution path — ``LORE_VAULT`` is not injected by
    the harnesses, so this seeding is what points the CLI at the test vault.
    """
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {
                        "name": "default",
                        "scope": "default",
                        "path": str(vault_path),
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_cli(args, *, vault, state_dir, stdin_text=None, env_extra=None, cwd=None):
    """Run the lore CLI as a subprocess; returns CompletedProcess.

    Shared harness for the record/session CLI tests — fences XDG_STATE_HOME +
    XDG_CONFIG_HOME (+ a stable LORE_EMAIL) so tests never touch the real vault,
    state, or config dir. ``env_extra`` overlays extra env vars.

    ``config.json`` pointing at ``vault`` so config-based resolution — the
    only resolution path, since ``LORE_VAULT`` is not injected — resolves to
    the test vault. Because ``record create``/``update``/``delete`` consult
    ``config_dir("lore")/config.json``, an inherited ambient config (e.g. on a CI
    runner) would otherwise reroute records away from the test vault.
    Callers that exercise layered vaults pass their own XDG_CONFIG_HOME via
    ``env_extra`` (applied last, so their config wins over the seeded default).

    ``cwd`` sets the subprocess working directory. The group-default routing path
    resolves the active camp group from ``Path.cwd()``, so a test that exercises
    routing inside a bound member repo passes that repo as ``cwd`` (paired with a
    ``LORE_GROUPS_DIR`` override via ``env_extra``).
    """
    full_env = dict(os.environ)
    full_env["XDG_STATE_HOME"] = str(state_dir)
    _xdg_config = Path(state_dir) / "_xdg_config"
    full_env["XDG_CONFIG_HOME"] = str(_xdg_config)
    full_env["LORE_EMAIL"] = "tester@example.com"
    write_default_config(_xdg_config, Path(vault))
    if env_extra:
        full_env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=stdin_text,
        cwd=str(cwd) if cwd is not None else None,
    )


def make_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(vault_dir, state_dir)`` under ``tmp_path``, creating both."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return vault, state


def load_script(name: str):
    """Load a lore module freshly each call, isolating state across tests.

    ``name`` is either a bare stem of a still-flat ``scripts/`` module (e.g.
    ``"frontmatter"``) or a dotted path into the ``lore`` package (e.g.
    ``"lore.vault.vault"``) for a module that has already migrated there.

    Bare stems load via ``spec_from_file_location`` + ``exec_module``,
    bypassing the import system entirely — each call gets a brand-new module
    object, deliberately never registered in ``sys.modules``.

    Dotted paths load via ``importlib.import_module`` once, then
    ``importlib.reload`` per call: relative imports (``from . import x``)
    only resolve under the real import system, so the fresh-module-per-test
    isolation the bare-stem path gets from ``exec_module`` comes from
    ``reload`` instead.
    """
    if "." in name:
        mod = importlib.import_module(name)
        return importlib.reload(mod)
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
