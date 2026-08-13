"""`lore areas` on-demand area lister.

Test contract (TDD — written BEFORE cmd_areas exists):
  1. Vault with 2 areas → stdout contains both area names, one-liners, keywords.
  2. Empty vault (no area files) → stdout contains a "no areas" message, exit 0,
     no traceback.
  3. Unresolvable vault → stderr one-liner AND a degraded stdout line, exit 0.

The command must follow the never-fail contract that cmd_recall already uses.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_cli():
    """Return the areas command module (``lore.cli.areas``).

    ``cmd_areas`` moved out of the monolithic ``cli/lore`` into its own
    command-group module; conftest puts the ``lore`` package's plugin root on
    sys.path so it imports by its dotted name. A fresh ``reload`` keeps the
    per-test isolation the old in-process loader provided.
    """
    from lore.cli import areas
    return importlib.reload(areas)


# ---------------------------------------------------------------------------
# Vault fixture helpers
# ---------------------------------------------------------------------------


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "area").mkdir(parents=True)
    (vault / "sessions").mkdir(parents=True)
    return vault


def _write_area(
    vault: Path,
    name: str,
    keywords: list[str],
    summary: str | None = None,
) -> Path:
    p = vault / "area" / f"{name}.md"
    kw_str = "[" + ", ".join(keywords) + "]"
    summary_line = f"summary: {summary}\n" if summary else ""
    p.write_text(
        f"---\ntype: area\nname: {name}\nkeywords: {kw_str}\n{summary_line}---\n\n"
        f"## Overview\n\nThis is the {name} area.\n"
    )
    return p


# ---------------------------------------------------------------------------
# Runner that invokes cmd_areas in-process
# ---------------------------------------------------------------------------


def _config_env(vault_path: str):
    """Seed config.json (default vault = ``vault_path``) and return an os.environ
    patch fencing XDG_CONFIG_HOME/XDG_STATE_HOME at hermetic tmp dirs.

    Config-only resolution: cmd_areas resolves the vault via
    ``resolve_active_vault`` (no LORE_VAULT). ``vault_path`` may be a nonexistent
    absolute path — config honors it and cmd_areas degrades when missing.
    XDG_STATE_HOME is fenced so config validation never touches the real state
    dir (Axiom 6).
    """
    from conftest import write_default_config
    config_home = Path(vault_path).parent / "_xdg_config"
    state_home = Path(vault_path).parent / "_xdg_state"
    write_default_config(config_home, Path(vault_path))
    return mock.patch.dict(
        os.environ,
        {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
        clear=False,
    )


def _multi_config_env(tmp_path: Path, vaults, *, shared_names=()):
    """Seed config.json with multiple vaults and return an os.environ patch.

    ``vaults`` is an iterable of ``(name, scope, path)`` triples, mirroring
    ``write_vault_config``. ``shared_names`` marks the named entries
    ``shared: true`` after writing, the same pattern
    ``test_flush_scoping.py``'s ``_set_vault_shared`` uses. Fences
    XDG_CONFIG_HOME/XDG_STATE_HOME at tmp_path-scoped dirs so nothing touches
    the live install (Axiom 6).
    """
    from conftest import write_vault_config

    config_home = tmp_path / "_xdg_config"
    state_home = tmp_path / "_xdg_state"
    write_vault_config(config_home, vaults)
    if shared_names:
        cfg_path = config_home / "lore" / "config.json"
        cfg = json.loads(cfg_path.read_text())
        for entry in cfg["vaults"]:
            if entry["name"] in shared_names:
                entry["shared"] = True
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return mock.patch.dict(
        os.environ,
        {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
        clear=False,
    )


def _run_areas_with_env(env_ctx) -> tuple[str, str, int]:
    """Invoke cmd_areas in-process under an already-built env context manager."""
    cli = _load_cli()
    out = io.StringIO()
    err = io.StringIO()
    args = SimpleNamespace()

    with env_ctx:
        with mock.patch("sys.stdout", out):
            with mock.patch("sys.stderr", err):
                rc = cli.cmd_areas(args)

    return out.getvalue(), err.getvalue(), rc


def _run_areas(vault_path: str) -> tuple[str, str, int]:
    """Invoke cmd_areas against a single-default-vault config.

    Returns (stdout, stderr, returncode). The single-vault convenience wrapper
    over :func:`_run_areas_with_env`.
    """
    return _run_areas_with_env(_config_env(vault_path))


# ---------------------------------------------------------------------------
# Tests: vault with 2 areas
# ---------------------------------------------------------------------------


class TestAreasWithContent:
    def test_stdout_contains_first_area_name(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth", "jwt"], summary="OAuth and JWT flows")
        _write_area(vault, "billing", ["stripe"], summary="Payment processing")

        stdout, _, rc = _run_areas(str(vault))

        assert rc == 0
        assert "auth" in stdout

    def test_stdout_contains_second_area_name(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth", "jwt"], summary="OAuth and JWT flows")
        _write_area(vault, "billing", ["stripe"], summary="Payment processing")

        stdout, _, rc = _run_areas(str(vault))

        assert rc == 0
        assert "billing" in stdout

    def test_stdout_contains_first_area_one_liner(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth", "jwt"], summary="OAuth and JWT flows")
        _write_area(vault, "billing", ["stripe"], summary="Payment processing")

        stdout, _, _ = _run_areas(str(vault))

        assert "OAuth and JWT flows" in stdout

    def test_stdout_contains_second_area_one_liner(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth", "jwt"], summary="OAuth and JWT flows")
        _write_area(vault, "billing", ["stripe"], summary="Payment processing")

        stdout, _, _ = _run_areas(str(vault))

        assert "Payment processing" in stdout

    def test_stdout_contains_keywords(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth", "jwt"], summary="OAuth and JWT flows")

        stdout, _, _ = _run_areas(str(vault))

        assert "oauth" in stdout

    def test_exit_code_is_zero(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth flows")

        _, _, rc = _run_areas(str(vault))

        assert rc == 0

    def test_no_traceback_in_stdout(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth flows")

        stdout, _, _ = _run_areas(str(vault))

        assert "Traceback" not in stdout

    def test_stdout_contains_lore_search_instruction(self, tmp_path):
        """render_area_menu includes a 'lore search' instruction (the per-area
        lookup is `lore search 'area:<name>'`, not `lore recall`)."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth flows")

        stdout, _, _ = _run_areas(str(vault))

        assert "lore search" in stdout
        assert "lore recall" not in stdout


# ---------------------------------------------------------------------------
# Tests: empty vault (no area files)
# ---------------------------------------------------------------------------


class TestAreasEmpty:
    def test_exit_code_is_zero(self, tmp_path):
        vault = _make_vault(tmp_path)

        _, _, rc = _run_areas(str(vault))

        assert rc == 0

    def test_stdout_contains_no_areas_message(self, tmp_path):
        vault = _make_vault(tmp_path)

        stdout, _, _ = _run_areas(str(vault))

        assert "no areas" in stdout.lower()

    def test_stderr_is_silent_for_a_healthy_vault_with_zero_areas(self, tmp_path):
        """A resolvable, healthy vault that simply has no area/ files defined
        yet must not emit the degradation stderr signal — that signal is
        reserved for actual resolution failure, not a legitimately empty
        vault (a freshly-initialized vault must not read as an error)."""
        vault = _make_vault(tmp_path)

        _, stderr, _ = _run_areas(str(vault))

        assert stderr == ""

    def test_no_traceback(self, tmp_path):
        vault = _make_vault(tmp_path)

        stdout, stderr, _ = _run_areas(str(vault))

        assert "Traceback" not in stdout
        assert "Traceback" not in stderr

    def test_absent_areas_dir_also_safe(self, tmp_path):
        """build_area_map tolerates absent areas/ — no crash, no areas message."""
        vault = _make_vault(tmp_path)
        (vault / "area").rmdir()

        stdout, _, rc = _run_areas(str(vault))

        assert rc == 0
        assert "no areas" in stdout.lower()


# ---------------------------------------------------------------------------
# Tests: unresolvable vault (degradation contract)
# ---------------------------------------------------------------------------


class TestAreasUnresolvableVault:
    def test_exit_code_is_zero(self, tmp_path):
        """Vault resolution failure must still exit 0."""
        bad_vault = str(tmp_path / "nonexistent" / "vault")

        _, _, rc = _run_areas(bad_vault)

        assert rc == 0

    def test_stderr_contains_one_liner_signal(self, tmp_path):
        """A one-line diagnostic must appear on stderr."""
        bad_vault = str(tmp_path / "nonexistent" / "vault")

        _, stderr, _ = _run_areas(bad_vault)

        assert stderr.strip() != ""
        # Must be a single compact line (no multi-line traceback)
        assert len(stderr.strip().splitlines()) == 1

    def test_stdout_contains_degraded_line(self, tmp_path):
        """Even on vault failure, a human-readable line must appear on stdout."""
        bad_vault = str(tmp_path / "nonexistent" / "vault")

        stdout, _, _ = _run_areas(bad_vault)

        assert stdout.strip() != ""

    def test_stdout_degraded_line_is_no_areas(self, tmp_path):
        """Degraded stdout line must convey 'no areas' so caller gets something useful."""
        bad_vault = str(tmp_path / "nonexistent" / "vault")

        stdout, _, _ = _run_areas(bad_vault)

        assert "no areas" in stdout.lower()

    def test_no_traceback_in_stdout(self, tmp_path):
        bad_vault = str(tmp_path / "nonexistent" / "vault")

        stdout, _, _ = _run_areas(bad_vault)

        assert "Traceback" not in stdout


# ---------------------------------------------------------------------------
# Tests: vault path exists but build_area_map raises
# ---------------------------------------------------------------------------


class TestAreasBuildAreaMapRaises:
    """Cover the second never-fail branch in cmd_areas.

    Vault exists on disk but build_area_map raises (e.g. unexpected I/O or
    parse error). The command must still exit 0, emit a degraded 'no areas'
    stdout line, and emit a single-line stderr signal.
    """

    def test_exit_code_is_zero(self, tmp_path):
        """cmd_areas exits 0 even when build_area_map raises."""
        vault = _make_vault(tmp_path)
        cli = _load_cli()
        from types import SimpleNamespace
        from unittest import mock
        import io

        out = io.StringIO()
        err = io.StringIO()
        args = SimpleNamespace()

        with _config_env(str(vault)):
            with mock.patch("sys.stdout", out):
                with mock.patch("sys.stderr", err):
                    with mock.patch(
                        "lore.search.area_map.build_area_map",
                        side_effect=RuntimeError("parse failure"),
                    ):
                        rc = cli.cmd_areas(args)

        assert rc == 0

    def test_stdout_contains_no_areas_line(self, tmp_path):
        """Degraded stdout line conveys 'no areas' when build_area_map raises."""
        vault = _make_vault(tmp_path)
        cli = _load_cli()
        from types import SimpleNamespace
        from unittest import mock
        import io

        out = io.StringIO()
        err = io.StringIO()
        args = SimpleNamespace()

        with _config_env(str(vault)):
            with mock.patch("sys.stdout", out):
                with mock.patch("sys.stderr", err):
                    with mock.patch(
                        "lore.search.area_map.build_area_map",
                        side_effect=RuntimeError("parse failure"),
                    ):
                        cli.cmd_areas(args)

        assert "no areas" in out.getvalue().lower()

    def test_stderr_contains_one_line_signal(self, tmp_path):
        """A single-line stderr diagnostic is emitted when build_area_map raises."""
        vault = _make_vault(tmp_path)
        cli = _load_cli()
        from types import SimpleNamespace
        from unittest import mock
        import io

        out = io.StringIO()
        err = io.StringIO()
        args = SimpleNamespace()

        with _config_env(str(vault)):
            with mock.patch("sys.stdout", out):
                with mock.patch("sys.stderr", err):
                    with mock.patch(
                        "lore.search.area_map.build_area_map",
                        side_effect=RuntimeError("parse failure"),
                    ):
                        cli.cmd_areas(args)

        stderr = err.getvalue()
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1


# ---------------------------------------------------------------------------
# Tests: multi-vault enumeration (the regression this task fixes)
# ---------------------------------------------------------------------------


class TestAreasMultiVault:
    def test_area_in_default_vault_appears(self, tmp_path):
        default_vault = tmp_path / "v-default"
        other_vault = tmp_path / "v-team"
        (default_vault / "area").mkdir(parents=True)
        (other_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "auth", ["oauth"], summary="Auth in default.")
        _write_area(other_vault, "billing", ["stripe"], summary="Billing in team.")

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", default_vault), ("teamvault", "team", other_vault)],
        )
        stdout, _, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert "auth" in stdout

    def test_area_in_scoped_vault_appears(self, tmp_path):
        """The reported regression: an area living only in a scoped (non-default)
        vault must still show up in `lore areas`."""
        default_vault = tmp_path / "v-default"
        other_vault = tmp_path / "v-team"
        (default_vault / "area").mkdir(parents=True)
        (other_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "auth", ["oauth"], summary="Auth in default.")
        _write_area(other_vault, "billing", ["stripe"], summary="Billing in team.")

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", default_vault), ("teamvault", "team", other_vault)],
        )
        stdout, _, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert "billing" in stdout

    def test_same_named_area_in_two_vaults_appears_once(self, tmp_path):
        default_vault = tmp_path / "v-default"
        other_vault = tmp_path / "v-team"
        (default_vault / "area").mkdir(parents=True)
        (other_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "auth", ["oauth"], summary="Default one-liner.")
        _write_area(other_vault, "auth", ["saml"], summary="Team one-liner.")

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", default_vault), ("teamvault", "team", other_vault)],
        )
        stdout, _, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert stdout.count("  auth  ") == 1
        assert "Default one-liner." in stdout
        assert "Team one-liner." not in stdout

    def test_shared_vault_areas_excluded(self, tmp_path):
        default_vault = tmp_path / "v-default"
        shared_vault = tmp_path / "v-shared"
        (default_vault / "area").mkdir(parents=True)
        (shared_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(
            shared_vault, "untrusted", ["x"], summary="Shared one-liner leak check."
        )

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", default_vault), ("sharedvault", "team", shared_vault)],
            shared_names=("sharedvault",),
        )
        stdout, _, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert "auth" in stdout
        assert "untrusted" not in stdout
        assert "Shared one-liner leak check." not in stdout

    def test_shared_vault_content_excluded_despite_case_only_path_alias(
        self, tmp_path
    ):
        """Regression: a ``shared: true`` vault's content must stay out of the
        menu even when a second, non-shared config entry names the exact same
        physical directory under different-case path string.

        Reproduces the exact two-entry shape from the report — a
        ``shared: true`` ``sharedteam`` vault plus a ``sharedteam-alias``
        entry whose path is ``sharedteam``'s with its case flipped. On this
        sandbox's case-insensitive (APFS) filesystem the two entries are
        genuinely the same physical directory (confirmed below), so a
        resolved-path-*string*-keyed exclusion set — built from
        ``str(Path(v.path).resolve())`` for the shared entry only — never
        contains the alias's differently-cased string and the alias's own
        root gets scanned directly, handing the shared vault's area straight
        to the merged menu one hop removed. A purely name-keyed exclusion
        (checking only whether the *scanned* entry's own name is
        ``shared: true``) does not fully close this either: the alias entry
        is legitimately not marked shared, so its root would still be
        scanned and would still contain the shared vault's files. Closing it
        requires recognizing that the alias's root is, physically, the same
        directory as an entry that IS marked shared — the fix does this via
        ``os.stat`` device+inode identity rather than any path string.
        """
        vaults_dir = tmp_path / "vaults"
        shared_dir = vaults_dir / "SharedTeam"
        shared_dir.mkdir(parents=True)
        (shared_dir / "area").mkdir()
        _write_area(
            shared_dir, "untrusted", ["x"], summary="Shared one-liner leak check."
        )

        default_vault = tmp_path / "v-default"
        (default_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "core", ["x"], summary="Default area.")

        alias_path = vaults_dir / "sharedteam"  # case-flipped alias of shared_dir
        assert (alias_path / "area" / "untrusted.md").is_file(), (
            "sandbox filesystem is case-sensitive — this reproduction needs "
            "a case-insensitive volume (e.g. macOS APFS)"
        )

        env_ctx = _multi_config_env(
            tmp_path,
            [
                ("default", "default", default_vault),
                ("sharedteam", "team", shared_dir),
                ("sharedteam-alias", "team", alias_path),
            ],
            shared_names=("sharedteam",),
        )
        stdout, _, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert "untrusted" not in stdout
        assert "Shared one-liner leak check." not in stdout
        assert "core" in stdout

    def test_one_absent_root_still_renders_the_rest(self, tmp_path):
        default_vault = tmp_path / "v-default"
        absent_vault = tmp_path / "v-does-not-exist"
        (default_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "auth", ["oauth"], summary="Auth area.")

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", default_vault), ("teamvault", "team", absent_vault)],
        )
        stdout, _, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert "auth" in stdout

    def test_every_root_absent_emits_stderr_signal_and_no_areas_line(self, tmp_path):
        absent_default = tmp_path / "v-default-absent"
        absent_other = tmp_path / "v-team-absent"

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", absent_default), ("teamvault", "team", absent_other)],
        )
        stdout, stderr, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert "no areas" in stdout.lower()
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1

    def test_one_vault_build_area_map_raising_still_renders_the_rest(self, tmp_path):
        default_vault = tmp_path / "v-default"
        other_vault = tmp_path / "v-team"
        (default_vault / "area").mkdir(parents=True)
        (other_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(other_vault, "billing", ["stripe"], summary="Billing area.")

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", default_vault), ("teamvault", "team", other_vault)],
        )
        cli = _load_cli()
        out = io.StringIO()
        err = io.StringIO()
        args = SimpleNamespace()

        from lore.search import area_map as area_map_mod
        original = area_map_mod.build_area_map

        def _raise_for_default(vault, *a, **kw):
            if Path(vault) == default_vault:
                raise RuntimeError("boom")
            return original(vault, *a, **kw)

        with env_ctx:
            with mock.patch("sys.stdout", out):
                with mock.patch("sys.stderr", err):
                    with mock.patch(
                        "lore.search.area_map.build_area_map",
                        side_effect=_raise_for_default,
                    ):
                        rc = cli.cmd_areas(args)

        assert rc == 0
        assert "billing" in out.getvalue()

    def test_one_root_exists_check_raising_still_renders_the_rest(self, tmp_path):
        """A single root whose ``exists()`` check itself raises (symlink loop,
        permission error on a parent directory) must degrade like any other
        single-root failure — the surviving roots still render — rather than
        propagating past `_dedupe_and_exclude_shared_roots` into `cmd_areas`'s
        outer total guard and blanking the whole menu (contradicting the
        docstring's claim that a single failing root leaves survivors
        rendering)."""
        default_vault = tmp_path / "v-default"
        other_vault = tmp_path / "v-team"
        (default_vault / "area").mkdir(parents=True)
        (other_vault / "area").mkdir(parents=True)
        _write_area(default_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(other_vault, "billing", ["stripe"], summary="Billing area.")

        env_ctx = _multi_config_env(
            tmp_path,
            [("default", "default", default_vault), ("teamvault", "team", other_vault)],
        )
        cli = _load_cli()
        out = io.StringIO()
        err = io.StringIO()
        args = SimpleNamespace()

        original_exists = Path.exists

        def _raising_exists(self, *a, **kw):
            if self == default_vault:
                raise OSError("simulated symlink loop")
            return original_exists(self, *a, **kw)

        with env_ctx:
            with mock.patch("sys.stdout", out):
                with mock.patch("sys.stderr", err):
                    with mock.patch.object(Path, "exists", _raising_exists):
                        rc = cli.cmd_areas(args)

        assert rc == 0
        assert "billing" in out.getvalue()

    def test_malformed_config_falls_back_to_floor_vault_with_stderr_signal(self, tmp_path):
        config_home = tmp_path / "_xdg_config"
        state_home = tmp_path / "_xdg_state"
        lore_cfg = config_home / "lore"
        lore_cfg.mkdir(parents=True)
        (lore_cfg / "config.json").write_text("{not valid json", encoding="utf-8")

        env_ctx = mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
            clear=False,
        )
        stdout, stderr, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1
        assert "Traceback" not in stdout
        assert "Traceback" not in stderr

    def test_config_json_top_level_list_falls_back_to_floor_vault_with_stderr_signal(
        self, tmp_path
    ):
        """A well-formed-JSON-but-wrong-shape config (top-level list instead of
        an object) must degrade like any other malformed config, not raise
        AttributeError out of ``validate_config``'s ``data.get("vaults", [])``."""
        config_home = tmp_path / "_xdg_config"
        state_home = tmp_path / "_xdg_state"
        lore_cfg = config_home / "lore"
        lore_cfg.mkdir(parents=True)
        (lore_cfg / "config.json").write_text(json.dumps([]), encoding="utf-8")

        env_ctx = mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
            clear=False,
        )
        stdout, stderr, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1
        assert "Traceback" not in stdout
        assert "Traceback" not in stderr

    def test_config_json_vaults_entries_not_objects_falls_back_to_floor_vault(
        self, tmp_path
    ):
        """A ``"vaults"`` array of non-dict entries (e.g. bare strings) must
        degrade too, not raise AttributeError out of ``entry.get("name", "")``."""
        config_home = tmp_path / "_xdg_config"
        state_home = tmp_path / "_xdg_state"
        lore_cfg = config_home / "lore"
        lore_cfg.mkdir(parents=True)
        (lore_cfg / "config.json").write_text(
            json.dumps({"vaults": ["default"]}), encoding="utf-8"
        )

        env_ctx = mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
            clear=False,
        )
        stdout, stderr, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1
        assert "Traceback" not in stdout
        assert "Traceback" not in stderr

    def test_no_config_json_at_all_is_vanilla_with_silent_stderr(self, tmp_path):
        """The true vanilla path: no ``config.json`` on disk at all (every other
        case in this suite writes one, including the malformed ones). A healthy
        empty floor vault (``state/lore/vaults/default``, pre-created the way
        ``lore init``/first use would leave it) must produce the plain "no
        areas" stdout line and NO stderr signal — byte-identical to the
        pre-multi-vault base behavior."""
        config_home = tmp_path / "_xdg_config"
        state_home = tmp_path / "_xdg_state"
        floor_vault = state_home / "lore" / "vaults" / "default"
        (floor_vault / "area").mkdir(parents=True)
        assert not (config_home / "lore" / "config.json").exists()

        env_ctx = mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
            clear=False,
        )
        stdout, stderr, rc = _run_areas_with_env(env_ctx)

        assert rc == 0
        assert "no areas" in stdout.lower()
        assert stderr == ""


# ---------------------------------------------------------------------------
# Tests: vault resolution raises something outside the enumerated config-
# parse exceptions (e.g. trailhead.paths.PathResolutionError) — cmd_areas
# must still never let a traceback escape (total guard at the command
# boundary, not another widened `except` tuple downstream).
# ---------------------------------------------------------------------------


class TestAreasTotalGuardAgainstUnenumeratedExceptions:
    def _run(self, env_patch) -> tuple[str, str, int]:
        cli = _load_cli()
        out = io.StringIO()
        err = io.StringIO()
        args = SimpleNamespace()
        with env_patch:
            with mock.patch("sys.stdout", out):
                with mock.patch("sys.stderr", err):
                    rc = cli.cmd_areas(args)
        return out.getvalue(), err.getvalue(), rc

    def test_relative_xdg_config_home_does_not_raise(self, tmp_path, monkeypatch):
        """A relative XDG_CONFIG_HOME makes trailhead.paths.config_dir raise
        PathResolutionError — a type `_resolve_all_vaults_and_shared`'s except
        tuple does not (and must not) enumerate. cmd_areas must still degrade
        cleanly rather than let it escape as a traceback."""
        monkeypatch.chdir(tmp_path)
        env_patch = mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": "relconf", "XDG_STATE_HOME": str(tmp_path / "_xdg_state")},
            clear=False,
        )
        stdout, stderr, rc = self._run(env_patch)

        assert rc == 0
        assert "Traceback" not in stdout
        assert "Traceback" not in stderr
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1
        assert "no areas" in stdout.lower()

    def test_relative_lore_config_dir_does_not_raise(self, tmp_path, monkeypatch):
        """Same failure class via LORE_CONFIG_DIR set to a relative path."""
        monkeypatch.chdir(tmp_path)
        env_patch = mock.patch.dict(
            os.environ,
            {"LORE_CONFIG_DIR": "relative/path", "XDG_STATE_HOME": str(tmp_path / "_xdg_state")},
            clear=False,
        )
        stdout, stderr, rc = self._run(env_patch)

        assert rc == 0
        assert "Traceback" not in stdout
        assert "Traceback" not in stderr
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1
        assert "no areas" in stdout.lower()

    def test_unset_home_does_not_raise(self, tmp_path, monkeypatch):
        """Same failure class via an unset HOME — trailhead.paths.home_dir()
        raises PathResolutionError when HOME is absent."""
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        stdout, stderr, rc = self._run(mock.patch.dict(os.environ, {}, clear=False))

        assert rc == 0
        assert "Traceback" not in stdout
        assert "Traceback" not in stderr
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1
        assert "no areas" in stdout.lower()
