"""Tests for _resolve_group_scopes — the group-default scope routing helper in cli/lore.

Exercises the full degradation contract: returns {} on every failure path (camp
absent, bootstrap unavailable, no group match, malformed config, groups_dir is
None). Emits a stderr warning only for overlap and malformed config; a clean
no-group match is silent.

The function normalises vault names (/ → _) before returning, so the returned
value always matches the elected vault for slashed names.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

# Camp scripts must be on sys.path so the lazy camp imports inside
# _resolve_group_scopes can succeed when tests exercise the happy path.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CAMP_SCRIPTS = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
if str(_CAMP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CAMP_SCRIPTS))

from conftest import SCRIPTS_DIR  # noqa: E402

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_CLI_PATH = Path(__file__).parent.parent / "plugins" / "lore" / "cli" / "lore"


def _load_cli():
    """Load cli/lore as a module to access its private helpers.

    cli/lore has no .py suffix, so the loader is named explicitly —
    spec_from_file_location cannot infer one from the extensionless path.
    """
    loader = SourceFileLoader("lore_cli", str(_CLI_PATH))
    spec = importlib.util.spec_from_loader("lore_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# Load once; the lazy imports inside _resolve_group_scopes do not depend on
# module-load-time state, so a single load is sufficient for all tests.
_CLI = _load_cli()
_resolve_group_scopes = _CLI._resolve_group_scopes


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _write_group_config(
    groups_dir: Path,
    *,
    group_name: str,
    member_repo: Path,
    lore_scopes: list[dict] | None = None,
    filename: str | None = None,
) -> Path:
    """Write a minimal group TOML and return its path."""
    if filename is None:
        filename = f"{group_name}.toml"
    lines = [
        f'[group]\nname = "{group_name}"\n',
        f'\n[[members]]\nname = "repo"\nrepo_root = "{member_repo}"\n',
    ]
    if lore_scopes:
        for ls in lore_scopes:
            lines.append(
                f'\n[[lore_scopes]]\nscope = "{ls["scope"]}"\nname = "{ls["name"]}"\n'
            )
    (groups_dir / filename).write_text("".join(lines), encoding="utf-8")
    return groups_dir / filename


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_cwd_in_group_with_binding_returns_scope_map(self, tmp_path: Path) -> None:
        """cwd inside a group that declares [[lore_scopes]] returns {scope: name}."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        camp_state_dir = tmp_path / "camp_state"

        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_repo=tmp_path / "repos" / "member-alpha",
            lore_scopes=[{"scope": "product", "name": "trailhead"}],
        )

        cwd = camp_state_dir / "mygroup" / "worktrees" / "feat-123" / "member-alpha"
        cwd.mkdir(parents=True)

        result = _resolve_group_scopes(
            cwd=cwd, groups_dir=groups_dir, camp_state_dir=camp_state_dir
        )
        assert result == {"product": "trailhead"}

    def test_cwd_in_group_with_no_binding_returns_empty(self, tmp_path: Path) -> None:
        """A group with no [[lore_scopes]] section → {}."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        camp_state_dir = tmp_path / "camp_state"

        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_repo=tmp_path / "repos" / "member-alpha",
        )

        cwd = camp_state_dir / "mygroup" / "worktrees" / "feat-123" / "member-alpha"
        cwd.mkdir(parents=True)

        result = _resolve_group_scopes(
            cwd=cwd, groups_dir=groups_dir, camp_state_dir=camp_state_dir
        )
        assert result == {}


# ---------------------------------------------------------------------------
# Degradation matrix
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_groups_dir_none_returns_empty(self, tmp_path: Path) -> None:
        """groups_dir=None (bootstrap-failure path) returns {} without crash."""
        result = _resolve_group_scopes(cwd=tmp_path, groups_dir=None)
        assert result == {}

    def test_cwd_not_in_any_group_returns_empty_and_silent(
        self, tmp_path: Path, capsys
    ) -> None:
        """cwd outside all configured groups degrades silently to {}."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        camp_state_dir = tmp_path / "camp_state"

        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_repo=tmp_path / "repos" / "member-alpha",
            lore_scopes=[{"scope": "product", "name": "trailhead"}],
        )

        # cwd is neither under camp_state_dir nor a registered repo_root
        cwd = tmp_path / "some-unrelated-dir"
        cwd.mkdir()

        result = _resolve_group_scopes(
            cwd=cwd, groups_dir=groups_dir, camp_state_dir=camp_state_dir
        )
        assert result == {}
        captured = capsys.readouterr()
        assert not captured.err  # no warning on a clean no-match

    def test_overlapping_groups_returns_empty_with_warning(
        self, tmp_path: Path, capsys
    ) -> None:
        """Two groups sharing the same repo_root (overlap) → {} + stderr warning."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        camp_state_dir = tmp_path / "camp_state"
        shared_repo = tmp_path / "shared-repo"

        # Both groups declare the same repo_root — overlap at canonical-repo resolution
        _write_group_config(
            groups_dir,
            group_name="group1",
            member_repo=shared_repo,
            lore_scopes=[{"scope": "product", "name": "trailhead"}],
            filename="group1.toml",
        )
        _write_group_config(
            groups_dir,
            group_name="group2",
            member_repo=shared_repo,
            filename="group2.toml",
        )

        # cwd is the shared repo itself; canonical-member-repo walk finds two groups
        result = _resolve_group_scopes(
            cwd=shared_repo, groups_dir=groups_dir, camp_state_dir=camp_state_dir
        )
        assert result == {}
        captured = capsys.readouterr()
        assert captured.err  # overlap warning must appear

    def test_camp_import_unavailable_returns_empty(self, tmp_path: Path) -> None:
        """Blocking the camp import degrades to {} without crash."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()

        with mock.patch.dict(
            sys.modules,
            {
                "camp": None,
                "camp.scripts": None,
                "camp.scripts.group_config": None,
                "camp.scripts.group_resolve": None,
            },
        ):
            result = _resolve_group_scopes(cwd=tmp_path, groups_dir=groups_dir)
        assert result == {}

    def test_bootstrap_guard_prevents_module_not_found_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The ensure_trailhead_importable() guard prevents ModuleNotFoundError
        from escaping when trailhead is not yet on sys.path at call time.

        resolve_from_cwd imports trailhead.paths lazily when camp_state_dir is None.
        Without the guard calling ensure_trailhead_importable() first, that import
        raises ModuleNotFoundError which is not caught by the GroupResolutionError
        handler. The guard prevents this by ensuring trailhead is importable before
        any camp code runs.

        This test fails if the guard block is deleted.
        """
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        _write_group_config(
            groups_dir,
            group_name="testgroup",
            member_repo=tmp_path / "repos" / "member",
        )

        # Remove trailhead from sys.modules so re-import is attempted
        for key in list(sys.modules.keys()):
            if key == "trailhead" or key.startswith("trailhead."):
                monkeypatch.delitem(sys.modules, key, raising=False)

        # Remove trailhead root from sys.path so the import would fail without
        # ensure_trailhead_importable() re-adding it via its file-system walk
        trailhead_root = next(
            (p for p in sys.path if (Path(p) / "trailhead" / "paths.py").exists()),
            None,
        )
        if trailhead_root:
            monkeypatch.setattr(sys, "path", [p for p in sys.path if p != trailhead_root])

        # camp_state_dir=None triggers the lazy trailhead.paths import inside
        # resolve_from_cwd; the guard must repair sys.path first so it succeeds
        result = _resolve_group_scopes(
            cwd=tmp_path / "elsewhere", groups_dir=groups_dir
        )
        # No ModuleNotFoundError escaped; function degraded gracefully
        assert result == {}

    def test_malformed_group_config_returns_empty_with_warning(
        self, tmp_path: Path, capsys
    ) -> None:
        """A TOML with a malformed [[lore_scopes]] entry → {} + stderr warning."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()

        # scope "invalid_scope" is not in {repo, product, suite, team}
        (groups_dir / "bad.toml").write_text(
            '[group]\nname = "badgroup"\n\n'
            '[[members]]\nname = "repo"\nrepo_root = "/nonexistent"\n\n'
            '[[lore_scopes]]\nscope = "invalid_scope"\nname = "trailhead"\n',
            encoding="utf-8",
        )

        camp_state_dir = tmp_path / "camp_state"
        result = _resolve_group_scopes(
            cwd=tmp_path / "cwd",
            groups_dir=groups_dir,
            camp_state_dir=camp_state_dir,
        )
        assert result == {}
        captured = capsys.readouterr()
        assert captured.err  # warning must appear for malformed config

    def test_unreadable_group_config_degrades_with_warning(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """load_all_groups raising a non-GroupConfigError (e.g. OSError from an
        unreadable TOML, or UnicodeDecodeError from non-UTF-8 bytes — neither of
        which load_group wraps) degrades to {} with a warning rather than letting
        the exception crash the caller."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()

        camp_plugins = (
            Path(__file__).resolve().parents[3] / "tools" / "camp" / "plugins"
        )
        if str(camp_plugins) not in sys.path:
            sys.path.insert(0, str(camp_plugins))
        import camp.scripts.group_config as camp_gc

        def _raise_oserror(_groups_dir):
            raise OSError("simulated unreadable group config")

        monkeypatch.setattr(camp_gc, "load_all_groups", _raise_oserror)

        result = _resolve_group_scopes(cwd=tmp_path / "cwd", groups_dir=groups_dir)
        assert result == {}
        captured = capsys.readouterr()
        assert captured.err  # degradation warning must appear


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------


class TestNameVerbatim:
    def test_slashed_vault_name_returned_verbatim(self, tmp_path: Path) -> None:
        """A slashed name is returned exactly as written — matching how an explicit
        --repo flag stores its value — so flag-origin and group-default-origin
        sidecar fields agree. Vault election normalizes (/ → _) at lookup."""
        groups_dir = tmp_path / "groups"
        groups_dir.mkdir()
        camp_state_dir = tmp_path / "camp_state"

        _write_group_config(
            groups_dir,
            group_name="mygroup",
            member_repo=tmp_path / "repos" / "member-alpha",
            lore_scopes=[{"scope": "repo", "name": "org/repo-name"}],
        )

        cwd = camp_state_dir / "mygroup" / "worktrees" / "feat-123" / "member-alpha"
        cwd.mkdir(parents=True)

        result = _resolve_group_scopes(
            cwd=cwd, groups_dir=groups_dir, camp_state_dir=camp_state_dir
        )
        assert result == {"repo": "org/repo-name"}
