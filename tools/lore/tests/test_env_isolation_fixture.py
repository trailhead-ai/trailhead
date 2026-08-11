"""Coverage for the autouse ambient-env isolation fixture in conftest.py.

The suite's autouse fixture pins HOME/XDG_STATE_HOME/XDG_CONFIG_HOME to
tmp_path-scoped directories for every test, so an in-process call that reads
these vars ambiently (``env=None`` default) can never resolve the real
``~/.local/state/lore`` or ``~/.config/lore`` — even when the test itself adds
no per-test env override.
"""

from pathlib import Path

from conftest import load_script


def test_ambient_state_dir_resolves_under_tmp_path(tmp_path):
    """No per-test env override: ambient XDG_STATE_HOME must still be pinned.

    Exercises the same ambient-resolution path the fixture exists to fence —
    ``lore.locking.lock_root_for_vault(vault, env=None)`` — and asserts the
    resolved path falls under this test's own tmp_path, never under the real
    ``~/.local/state/lore``.
    """
    locking = load_script("lore.locking")
    vault = tmp_path / "vault"
    vault.mkdir()

    resolved = locking.lock_root_for_vault(vault, env=None)

    real_state_home = Path.home() / ".local" / "state" / "lore"
    assert real_state_home not in resolved.parents
    assert resolved != real_state_home
    assert str(tmp_path) in str(resolved)


def test_ambient_config_dir_resolves_under_tmp_path(tmp_path):
    """No per-test env override: ambient XDG_CONFIG_HOME must still be pinned."""
    import trailhead.paths as trailhead_paths

    resolved = trailhead_paths.config_dir("lore", env=None)

    real_config_home = Path.home() / ".config" / "lore"
    assert real_config_home not in resolved.parents
    assert resolved != real_config_home
    assert str(tmp_path) in str(resolved)
