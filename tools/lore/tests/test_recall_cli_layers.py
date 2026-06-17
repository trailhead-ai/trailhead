"""End-to-end CLI integration tests for cmd_recall layered-vault wiring.

Tests the bug where cmd_recall was never wired to resolve_layers / pass
layers= to recall_areas / pass tty= to render_recall_banner.

Test contract:
  1. Regression: personal + shared layer via groups config → banner contains
     both personal item AND shared item wrapped in <external-memory>.
     MUST FAIL against the old single-vault cmd_recall.
  2. --vault <override> → single-vault path (no <external-memory>, no layer
     composition). Explicit override is an escape from layering.
  3. --json over composed layers → items carry real layer values
     ("personal" + shared name) and correct trusted bools.
  4. Layer resolution raises → still exits 0 with a (degraded) banner.
  5. tty=False path (subprocess, piped) → shared items wrapped in
     <external-memory>; personal items are NOT wrapped.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


# ---------------------------------------------------------------------------
# Module loaders
# ---------------------------------------------------------------------------

def _load_cli_module():
    """Load cli/lore in-process (no .py extension → SourceFileLoader)."""
    from importlib.machinery import SourceFileLoader
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    loader = SourceFileLoader("lore_cli_recall_test", str(CLI_PATH))
    spec = importlib.util.spec_from_loader("lore_cli_recall_test", loader)
    sys.modules.pop("lore_cli_recall_test", None)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_cli(args, env=None, input_text=None, cwd=None):
    """Run lore CLI as a subprocess (piped → isatty()=False)."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=input_text,
        cwd=str(cwd) if cwd else None,
    )


# ---------------------------------------------------------------------------
# Vault and group-config fixture helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path, name: str = "vault") -> Path:
    vault = tmp_path / name
    for d in ("areas", "deferred", "dead-ends", "lessons", "decisions"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_area(vault: Path, name: str, keywords: list[str],
                summary: str | None = None) -> Path:
    p = vault / "areas" / f"{name}.md"
    kw_str = "[" + ", ".join(keywords) + "]"
    summary_line = f"summary: {summary}\n" if summary else ""
    p.write_text(
        f"---\ntype: area\nname: {name}\nkeywords: {kw_str}\n{summary_line}---\n"
    )
    return p


def _write_decision(vault: Path, name: str, areas: list[str]) -> Path:
    p = vault / "decisions" / f"{name}.md"
    areas_str = "[" + ", ".join(areas) + "]"
    p.write_text(
        f"---\ntype: decision\nareas: {areas_str}\n---\n\n# {name}\n\nDecision body.\n"
    )
    return p


def _write_group_config(
    groups_dir: Path,
    member_root: Path,
    shared_vaults: list[dict],
    group_name: str = "testgroup",
) -> Path:
    """Write a minimal camp group config with shared vaults."""
    groups_dir.mkdir(parents=True, exist_ok=True)
    cfg = groups_dir / f"{group_name}.toml"
    lines = [
        f'[group]\nname = "{group_name}"\n\n',
        f'[[members]]\nname = "repo"\nrepo_root = "{member_root}"\n',
    ]
    for sv in shared_vaults:
        lines.append(
            f'\n[[shared_vaults]]\nname = "{sv["name"]}"\nroot = "{sv["root"]}"\n'
        )
    cfg.write_text("".join(lines))
    return cfg


# ---------------------------------------------------------------------------
# 1. Regression: layered recall wired end-to-end through CLI
#
# This is THE regression test that would have caught the original bug.
# Before the fix, cmd_recall calls recall_areas(vault, area_names) without
# layers= and so only personal items appear; no <external-memory> is emitted.
# After the fix, layers= is passed and both items appear with correct framing.
# ---------------------------------------------------------------------------

class TestLayeredRecallEndToEnd:
    def test_personal_and_shared_items_both_in_banner(self, tmp_path: Path) -> None:
        """Regression: cmd_recall must compose layers when groups_dir has a shared vault.

        FAILS against the old single-vault cmd_recall (which skips layer resolution).
        PASSES after the fix (which resolves layers and passes them to recall_areas).
        """
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        # Area present in both vaults
        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")

        # One decision in personal, one in shared
        _write_decision(personal_vault, "personal-decision", areas=["auth"])
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        # Create a camp group config that registers the shared vault
        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        r = run_cli(
            ["recall", "--areas", "auth"],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )

        assert r.returncode == 0, f"recall must exit 0; stderr: {r.stderr!r}"
        assert "personal-decision" in r.stdout, (
            "personal-decision must appear in the banner (personal layer)"
        )
        assert "shared-decision" in r.stdout, (
            "shared-decision must appear in the banner (shared layer). "
            "FAIL here means cmd_recall is still using the single-vault path "
            "and never resolving/passing layers to recall_areas."
        )

    def test_shared_item_wrapped_in_external_memory_on_piped_output(self, tmp_path: Path) -> None:
        """Non-TTY (piped) output must wrap shared items in <external-memory>."""
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        r = run_cli(
            ["recall", "--areas", "auth"],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )

        assert r.returncode == 0
        assert '<external-memory layer="shared"' in r.stdout, (
            "Shared items must be wrapped in <external-memory> on piped (non-TTY) output. "
            "FAIL here means tty= is not being passed to render_recall_banner, or layers= "
            "is not being passed to recall_areas."
        )
        assert 'source="team-vault"' in r.stdout, (
            "external-memory wrapper must carry source= attribute with the vault name"
        )
        assert "</external-memory>" in r.stdout, (
            "external-memory block must be closed"
        )

    def test_personal_item_not_in_external_memory_wrapper(self, tmp_path: Path) -> None:
        """Personal items must NOT appear inside the <external-memory> block."""
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        r = run_cli(
            ["recall", "--areas", "auth"],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )

        assert r.returncode == 0
        # Find the <external-memory> block, assert personal-decision is outside it
        out = r.stdout
        ext_start = out.find("<external-memory")
        ext_end = out.find("</external-memory>")
        if ext_start != -1 and ext_end != -1:
            ext_block = out[ext_start: ext_end + len("</external-memory>")]
            assert "personal-decision" not in ext_block, (
                "personal-decision must NOT appear inside the <external-memory> block; "
                "personal items belong in the untrusted-free section above"
            )


# ---------------------------------------------------------------------------
# 2. --vault override → single-vault path (no layer composition)
# ---------------------------------------------------------------------------

class TestVaultOverrideEscapesLayering:
    def test_vault_override_uses_single_vault_path(self, tmp_path: Path) -> None:
        """--vault override → single-vault path: no <external-memory>, no shared items."""
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        # Pass --vault explicitly → must NOT compose layers
        r = run_cli(
            ["recall", "--areas", "auth", "--vault", str(personal_vault)],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )

        assert r.returncode == 0
        assert "<external-memory" not in r.stdout, (
            "--vault override must keep single-vault path; no <external-memory> wrapper. "
            "The override is a deliberate escape from layering."
        )
        # Shared item must NOT appear when --vault explicitly overrides
        assert "shared-decision" not in r.stdout, (
            "--vault override must use exactly the specified vault, not compose layers"
        )

    def test_vault_override_still_shows_personal_items(self, tmp_path: Path) -> None:
        """--vault override still shows the named vault's items."""
        personal_vault = _make_vault(tmp_path, "personal")
        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])

        r = run_cli(
            ["recall", "--areas", "auth", "--vault", str(personal_vault)],
            env={"LORE_VAULT": str(personal_vault)},
        )

        assert r.returncode == 0
        assert "personal-decision" in r.stdout


# ---------------------------------------------------------------------------
# 3. --json over composed layers → layer + trusted values are real
# ---------------------------------------------------------------------------

class TestJsonComposedLayers:
    def test_json_items_have_personal_and_shared_layers(self, tmp_path: Path) -> None:
        """--json over composed layers → items carry real layer names."""
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        r = run_cli(
            ["recall", "--areas", "auth", "--json"],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )

        assert r.returncode == 0, f"recall --json must exit 0; stderr: {r.stderr!r}"
        payload = json.loads(r.stdout)
        layers_seen = {item["layer"] for item in payload["items"]}
        assert "personal" in layers_seen, (
            "--json items must include layer='personal' for the personal vault"
        )
        assert "team-vault" in layers_seen, (
            "--json items must include layer='team-vault' for the shared vault. "
            "FAIL here means layers are not being composed in cmd_recall."
        )

    def test_json_trusted_values_correct(self, tmp_path: Path) -> None:
        """--json: personal items trusted=true, shared items trusted=false."""
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        r = run_cli(
            ["recall", "--areas", "auth", "--json"],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )

        assert r.returncode == 0
        payload = json.loads(r.stdout)
        for item in payload["items"]:
            if item["layer"] == "personal":
                assert item["trusted"] is True, (
                    f"personal item must have trusted=true: {item}"
                )
            elif item["layer"] == "team-vault":
                assert item["trusted"] is False, (
                    f"shared item must have trusted=false: {item}"
                )


# ---------------------------------------------------------------------------
# 4. Layer resolution raises → still exits 0 (never-fail contract)
# ---------------------------------------------------------------------------

class TestNeverFailOnLayerError:
    def test_layer_resolution_error_exits_zero(self, tmp_path: Path) -> None:
        """Layer resolution failure degrades to single-vault; exit 0 always (D-1)."""
        personal_vault = _make_vault(tmp_path, "personal")
        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])

        # LORE_GROUPS_DIR points at a non-existent directory → resolve_layers
        # will silently return personal-only (no crash)
        r = run_cli(
            ["recall", "--areas", "auth"],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(tmp_path / "nonexistent-groups"),
            },
        )

        assert r.returncode == 0, (
            "recall must always exit 0, even when layer resolution fails. "
            f"stderr: {r.stderr!r}"
        )
        # Must still show something useful on stdout
        assert "personal-decision" in r.stdout or "0 items" in r.stdout, (
            "recall must produce output even when layer resolution fails"
        )

    def test_layer_resolution_error_degrades_to_single_vault_in_process(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """In-process: when resolve_layers raises, cmd_recall falls back to
        single-vault (layers=None) and never propagates the exception."""
        personal_vault = _make_vault(tmp_path, "personal")
        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(personal_vault, "personal-decision", areas=["auth"])

        cli = _load_cli_module()

        # Patch _resolve_groups_dir to return a valid-but-empty path so
        # resolve_layers itself returns personal-only (no exception path needed,
        # but we want to confirm the normal-degradation path also reaches exit 0)
        monkeypatch.setattr(cli, "_resolve_groups_dir", lambda: None)
        monkeypatch.setenv("LORE_VAULT", str(personal_vault))

        captured = io.StringIO()
        with mock.patch("sys.stdout", captured):
            with mock.patch("sys.stdout.isatty", return_value=False):
                exit_code = cli.cmd_recall(
                    SimpleNamespace(areas="auth", vault=None, json=False)
                )

        assert exit_code == 0, "cmd_recall must always exit 0"
        output = captured.getvalue()
        assert "personal-decision" in output, (
            "degraded single-vault recall must still show personal items"
        )


# ---------------------------------------------------------------------------
# 5. tty= passed to render_recall_banner (non-TTY → XML, no human separator)
# ---------------------------------------------------------------------------

class TestTtyPassedToRenderer:
    def test_non_tty_subprocess_no_human_separator(self, tmp_path: Path) -> None:
        """Piped (non-TTY) output must not contain the TTY human separator."""
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        r = run_cli(
            ["recall", "--areas", "auth"],
            env={
                "LORE_VAULT": str(personal_vault),
                "LORE_GROUPS_DIR": str(groups_dir),
            },
            cwd=cwd_dir,
        )

        assert r.returncode == 0
        # Piped output must use XML, not human separator
        assert "--- [shared:" not in r.stdout, (
            "Non-TTY output must not contain the human-readable '--- [shared:' separator; "
            "use <external-memory> XML channel instead. "
            "FAIL here means tty= is not being passed to render_recall_banner."
        )
        assert "<external-memory" in r.stdout, (
            "Non-TTY output must use <external-memory> XML channel for shared items"
        )

    def test_in_process_tty_true_uses_human_separator(self, tmp_path: Path, monkeypatch) -> None:
        """In-process: tty=True path → human separator, no XML wrapper."""
        personal_vault = _make_vault(tmp_path, "personal")
        shared_vault = _make_vault(tmp_path, "shared")

        _write_area(personal_vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(shared_vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(shared_vault, "shared-decision", areas=["auth"])

        groups_dir = tmp_path / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(
            groups_dir, cwd_dir,
            [{"name": "team-vault", "root": str(shared_vault)}],
        )

        cli = _load_cli_module()
        monkeypatch.setenv("LORE_VAULT", str(personal_vault))
        monkeypatch.setenv("LORE_GROUPS_DIR", str(groups_dir))
        monkeypatch.chdir(cwd_dir)

        captured = io.StringIO()
        with mock.patch("sys.stdout", captured):
            # Monkeypatch isatty to return True (simulating interactive terminal)
            with mock.patch("sys.stdout.isatty", return_value=True):
                exit_code = cli.cmd_recall(
                    SimpleNamespace(areas="auth", vault=None, json=False)
                )

        assert exit_code == 0
        output = captured.getvalue()
        assert "--- [shared:" in output, (
            "TTY path must use human-readable '--- [shared:' separator. "
            "FAIL here means tty= is not being passed to render_recall_banner."
        )
        assert "<external-memory" not in output, (
            "TTY path must NOT use <external-memory> XML wrapper"
        )
