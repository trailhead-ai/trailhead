"""Tests for shared-layer discovery from the group's camp config.

Test contract (all must RED before implementation, GREEN after):

1. synthetic group config with one existing [[shared_vaults]] entry →
   resolve_layers() returns [personal, shared], shared trusted=False, in declared order.
2. no group config dir → [personal], no raise.
3. group with no [[shared_vaults]] → [personal].
4. cwd in no group → [personal] (graceful single-personal fallback).
5. camp absent (simulated ImportError) → [personal], no raise.
6. malformed-but-valid-TOML group config → [personal], no raise.
7. [[shared_vaults]] root that doesn't exist → omitted + named stderr line;
   remaining layers resolve.
8. shared name of '../evil' → named error, that layer dropped, the rest survive.
9. shared root that resolves to same path as personal root → dropped + stderr.
10. relative root in the TOML resolves relative to TOML file location, not cwd.
11. two groups claiming the cwd → [personal] + stderr overlap warning (recall degrade).
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from unittest import mock


from conftest import SCRIPTS_DIR, load_script

# Ensure scripts dir is on path for direct imports
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# The camp plugin root must be on sys.path for the lazy import to succeed.
_CAMP_PLUGIN_ROOT = str(Path(__file__).resolve().parents[4] / "camp" / "plugins")
if _CAMP_PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _CAMP_PLUGIN_ROOT)


# ---------------------------------------------------------------------------
# Helper: build a synthetic group TOML config file in tmp_path
# ---------------------------------------------------------------------------


def _write_group_config(
    groups_dir: Path,
    *,
    group_name: str = "testgroup",
    member_root: str | None = None,
    shared_vaults: list[dict] | None = None,
    filename: str | None = None,
) -> Path:
    """Write a minimal group config TOML and return its path."""
    if member_root is None:
        member_root = str(groups_dir.parent / "repo")
    if filename is None:
        filename = f"{group_name}.toml"

    lines = [
        f'[group]\nname = "{group_name}"\n',
        f'\n[[members]]\nname = "repo"\nrepo_root = "{member_root}"\n',
    ]
    if shared_vaults is not None:
        for sv in shared_vaults:
            sv_name = sv.get("name", "team-vault")
            sv_root = sv.get("root", "/tmp/shared-vault")
            lines.append(f'\n[[shared_vaults]]\nname = "{sv_name}"\nroot = "{sv_root}"\n')

    toml_path = groups_dir / filename
    toml_path.write_text("".join(lines))
    return toml_path


def _layers():
    return load_script("layers")


# ---------------------------------------------------------------------------
# 1. Happy path: group with one existing shared_vaults entry
# ---------------------------------------------------------------------------


class TestSharedLayerDiscovery:
    def test_one_shared_vault_returns_personal_and_shared(self, tmp_path: Path) -> None:
        """A group config with one existing [[shared_vaults]] → [personal, shared]."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        shared_root = tmp_path / "team-vault"
        shared_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[{"name": "team-vault", "root": str(shared_root)}],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 2
        assert layers[0].name == "personal"
        assert layers[0].kind == "personal"
        assert layers[0].trusted is True
        assert layers[1].name == "team-vault"
        assert layers[1].kind == "shared"
        assert layers[1].trusted is False
        assert layers[1].root.resolve() == shared_root.resolve()

    def test_two_shared_vaults_both_appended_in_order(self, tmp_path: Path) -> None:
        """Multiple [[shared_vaults]] entries appear in declared order."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        sv1 = tmp_path / "vault-alpha"
        sv1.mkdir()
        sv2 = tmp_path / "vault-beta"
        sv2.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[
                {"name": "vault-alpha", "root": str(sv1)},
                {"name": "vault-beta", "root": str(sv2)},
            ],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 3
        assert layers[0].name == "personal"
        assert layers[1].name == "vault-alpha"
        assert layers[2].name == "vault-beta"


# ---------------------------------------------------------------------------
# 2. Graceful fallback: no group config dir
# ---------------------------------------------------------------------------


class TestGracefulFallback:
    def test_no_groups_dir_returns_personal_only(self, tmp_path: Path) -> None:
        """No group config dir → [personal], no raise."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        groups_dir = tmp_path / "nonexistent-groups"
        # not created

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=tmp_path, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"

    def test_group_with_no_shared_vaults_returns_personal_only(self, tmp_path: Path) -> None:
        """Group config with no [[shared_vaults]] → [personal]."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[],  # no shared vaults
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"

    def test_cwd_in_no_group_returns_personal_only(self, tmp_path: Path) -> None:
        """cwd not in any group → [personal] (graceful single-personal fallback)."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "registered-repo"
        repo_root.mkdir()
        unrelated_cwd = tmp_path / "some-other-dir"
        unrelated_cwd.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        shared_root = tmp_path / "team-vault"
        shared_root.mkdir()
        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[{"name": "team-vault", "root": str(shared_root)}],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            # cwd is unrelated_cwd — not a member of any group
            layers = m.resolve_layers(cwd=unrelated_cwd, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"


# ---------------------------------------------------------------------------
# 5. camp absent → [personal], no raise
# ---------------------------------------------------------------------------


class TestCampAbsent:
    def test_camp_import_error_returns_personal_only(self, tmp_path: Path) -> None:
        """camp ImportError → [personal], no raise."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()

        # Simulate camp being absent by blocking the import
        with mock.patch.dict(
            sys.modules,
            {
                "camp": None,
                "camp.scripts": None,
                "camp.scripts.group_config": None,
                "camp.scripts.group_resolve": None,
            },
        ):
            with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
                layers = m.resolve_layers(cwd=tmp_path, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"


# ---------------------------------------------------------------------------
# 6. Malformed group config → [personal], no raise
# ---------------------------------------------------------------------------


class TestMalformedConfig:
    def test_malformed_group_config_returns_personal_only(self, tmp_path: Path, capsys) -> None:
        """Malformed group config (missing group.name) → [personal], no raise."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        # Write a structurally valid TOML but missing required group.name
        bad_config = groups_dir / "bad.toml"
        bad_config.write_text(
            '[group]\n# name is missing\n\n[[members]]\nname = "r"\nrepo_root = "/tmp/r"\n'
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"
        # A named stderr line should appear
        captured = capsys.readouterr()
        assert captured.err  # some warning was printed


# ---------------------------------------------------------------------------
# 7. Missing shared vault root → omitted + named stderr line
# ---------------------------------------------------------------------------


class TestMissingSharedRoot:
    def test_missing_shared_root_omitted_with_stderr(self, tmp_path: Path, capsys) -> None:
        """A [[shared_vaults]] root that doesn't exist → omitted + named stderr."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        nonexistent = tmp_path / "nonexistent-vault"
        # not created

        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[{"name": "missing-vault", "root": str(nonexistent)}],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"
        captured = capsys.readouterr()
        assert "missing-vault" in captured.err
        assert str(nonexistent) in captured.err or "not found" in captured.err

    def test_good_vault_survives_bad_vault_skipped(self, tmp_path: Path, capsys) -> None:
        """One missing vault is skipped; remaining valid vaults still resolve."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        good_vault = tmp_path / "good-vault"
        good_vault.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        nonexistent = tmp_path / "missing-vault"

        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[
                {"name": "missing-vault", "root": str(nonexistent)},
                {"name": "good-vault", "root": str(good_vault)},
            ],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 2
        names = [layer.name for layer in layers]
        assert "personal" in names
        assert "good-vault" in names
        assert "missing-vault" not in names


# ---------------------------------------------------------------------------
# 8. Bad layer name (../evil) → dropped + named stderr
# ---------------------------------------------------------------------------


class TestConfinementViolations:
    def test_bad_shared_name_dropped_with_stderr(self, tmp_path: Path, capsys) -> None:
        """A [[shared_vaults]] name of '../evil' → that layer dropped + named stderr."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        evil_vault = tmp_path / "evil-vault"
        evil_vault.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[{"name": "../evil", "root": str(evil_vault)}],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"
        captured = capsys.readouterr()
        assert captured.err  # named error

    def test_bad_name_dropped_but_good_name_survives(self, tmp_path: Path, capsys) -> None:
        """Bad name dropped; good-name vault in same config survives."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        good_vault = tmp_path / "good-vault"
        good_vault.mkdir()
        evil_vault = tmp_path / "evil-vault"
        evil_vault.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[
                {"name": "../evil", "root": str(evil_vault)},
                {"name": "good-vault", "root": str(good_vault)},
            ],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 2
        names = [layer.name for layer in layers]
        assert "good-vault" in names
        assert "personal" in names


# ---------------------------------------------------------------------------
# 9. Shared root resolves to same path as personal root → dropped + stderr
# ---------------------------------------------------------------------------


class TestPromoteToSelfCollision:
    def test_shared_root_same_as_personal_root_dropped(self, tmp_path: Path, capsys) -> None:
        """Shared root == personal root → dropped + stderr collision warning."""
        m = _layers()

        personal_root = tmp_path / "my-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        # shared vault points at the SAME directory as personal vault
        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_root=str(repo_root),
            shared_vaults=[{"name": "my-vault", "root": str(personal_root)}],
        )

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        assert len(layers) == 1
        assert layers[0].kind == "personal"
        captured = capsys.readouterr()
        assert captured.err  # collision warning


# ---------------------------------------------------------------------------
# 10. Relative root resolves relative to TOML file location
# ---------------------------------------------------------------------------


class TestRelativeRootResolution:
    def test_relative_root_resolves_from_toml_location(self, tmp_path: Path) -> None:
        """A relative root in [[shared_vaults]] resolves relative to the TOML file, not cwd."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Create shared vault relative to groups_dir
        shared_root = tmp_path / "shared-vault"
        shared_root.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()

        # Write relative root — relative to groups_dir
        toml_content = textwrap.dedent(f"""\
            [group]
            name = "mygroup"

            [[members]]
            name = "repo"
            repo_root = "{repo_root}"

            [[shared_vaults]]
            name = "shared-vault"
            root = "../shared-vault"
        """)
        (groups_dir / "mygroup.toml").write_text(toml_content)

        # cwd is somewhere completely different from groups_dir
        unrelated_cwd = repo_root

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=unrelated_cwd, groups_dir=groups_dir)

        # The relative "../shared-vault" from groups_dir should land at tmp_path/shared-vault
        assert len(layers) == 2
        assert layers[1].name == "shared-vault"
        assert layers[1].root.resolve() == shared_root.resolve()


# ---------------------------------------------------------------------------
# 11. Two groups claiming the cwd → [personal] + stderr overlap warning
# ---------------------------------------------------------------------------


class TestOverlapDegradation:
    def test_two_groups_claim_cwd_degrades_to_personal(self, tmp_path: Path, capsys) -> None:
        """Two groups claiming cwd → [personal] + stderr warning (recall degrade)."""
        m = _layers()

        personal_root = tmp_path / "personal-vault"
        personal_root.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        sv1 = tmp_path / "vault-1"
        sv1.mkdir()
        sv2 = tmp_path / "vault-2"
        sv2.mkdir()

        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()

        # Two groups both claim the same repo_root
        toml1 = textwrap.dedent(f"""\
            [group]
            name = "group-alpha"

            [[members]]
            name = "repo"
            repo_root = "{repo_root}"

            [[shared_vaults]]
            name = "vault-1"
            root = "{sv1}"
        """)
        toml2 = textwrap.dedent(f"""\
            [group]
            name = "group-beta"

            [[members]]
            name = "repo"
            repo_root = "{repo_root}"

            [[shared_vaults]]
            name = "vault-2"
            root = "{sv2}"
        """)
        (groups_dir / "alpha.toml").write_text(toml1)
        (groups_dir / "beta.toml").write_text(toml2)

        with mock.patch.dict(os.environ, {"LORE_VAULT": str(personal_root)}, clear=False):
            layers = m.resolve_layers(cwd=repo_root, groups_dir=groups_dir)

        # Must degrade to personal-only (never raise)
        assert len(layers) == 1
        assert layers[0].kind == "personal"
        # Must emit a stderr warning
        captured = capsys.readouterr()
        assert captured.err
