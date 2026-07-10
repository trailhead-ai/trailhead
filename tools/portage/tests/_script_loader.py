"""Shared dynamic-module-loader for portage thin-script tests.

The portage scripts under `plugins/portage/scripts/` are PATH entry points, not
an importable package, so tests load them by file path via
`importlib.util.spec_from_file_location` rather than a normal import.
test_portage_thin_scripts.py and test_pr_pair.py both need this same loader;
it lives here once so neither file re-derives `SCRIPTS_DIR` or the loader.

Named `_script_loader.py` rather than `conftest.py`: this directory (like its
sibling tool test dirs) carries no `__init__.py`, so pytest's default import
mode resolves every un-packaged `conftest.py` in the repo to the same bare
module name — a second one here would collide with
`tools/lore/tests/conftest.py`. A uniquely-named plain module, imported
directly by the tests that need it, sidesteps that collision entirely.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "portage" / "scripts"


def load_script(name: str):
    """Load a portage thin script module fresh by stem."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
