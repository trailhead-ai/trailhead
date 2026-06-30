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
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_cli():
    """Load cli/lore in-process (no .py extension — SourceFileLoader)."""
    from importlib.machinery import SourceFileLoader

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in list(sys.modules):
        if cached in (
            "recall",
            "vault",
            "frontmatter",
            "sessions",
            "layers",
            "promote",
            "review",
        ):
            sys.modules.pop(cached, None)
    loader = SourceFileLoader("lore_cli_areas_test", str(CLI_PATH))
    spec = importlib.util.spec_from_loader("lore_cli_areas_test", loader)
    sys.modules.pop("lore_cli_areas_test", None)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


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


def _run_areas(vault_path: str) -> tuple[str, str, int]:
    """Invoke cmd_areas in-process; returns (stdout, stderr, returncode)."""
    cli = _load_cli()
    out = io.StringIO()
    err = io.StringIO()
    args = SimpleNamespace()

    with _config_env(vault_path):
        with mock.patch("sys.stdout", out):
            with mock.patch("sys.stderr", err):
                rc = cli.cmd_areas(args)

    return out.getvalue(), err.getvalue(), rc


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
                    with mock.patch.object(
                        cli.recall_mod,
                        "build_area_map",
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
                    with mock.patch.object(
                        cli.recall_mod,
                        "build_area_map",
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
                    with mock.patch.object(
                        cli.recall_mod,
                        "build_area_map",
                        side_effect=RuntimeError("parse failure"),
                    ):
                        cli.cmd_areas(args)

        stderr = err.getvalue()
        assert stderr.strip() != ""
        assert len(stderr.strip().splitlines()) == 1
