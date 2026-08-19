"""EPHEMERAL assumption probe — U4 (help-surface tests), plan
task/cross-vault-pipeline-listing-surface-in-lore, blocking Slice 1
(task/pipeline-command-skeleton-vault-walk-failure-posture-fencing-chokepoint).

Question: does registering a new TOP-LEVEL `pipeline` subcommand (one new
`lore/cli/pipeline.py` + one import + one `add_pipeline_subparser(sub)` call
in `dispatch.py`, per the plan's pinned registration axiom) break any
existing structural `--help` test, and does anything need to learn about
`pipeline` for `lore --help` to render it correctly?

Method: never touch the real `dispatch.py` / `argparse_util.py`. Instead
copy the whole `plugins/lore` tree into a tmp dir, inject a stub
`add_pipeline_subparser` there exactly the way the plan's axiom describes,
and run the REAL existing structural tests' own logic against both the
stubbed copy (in-process, via the real `_leaf_parsers` + the real
`format_help()` output) and the real subprocess CLI shim copied alongside it
(reproducing `test_cli_dispatch_split.py`'s own subprocess technique).

DELETE this file once U4 is resolved (see plan Known Unknowns / Slice 1).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"

# The exact string `test_cli_dispatch_split.py::test_top_level_help_lists_every_command_group`
# asserts against today, before any `pipeline` registration.
_CURRENT_CHOICES_STRING = (
    "{init,status,sync,flush,areas,reindex,search,record,task,vault,session}"
)


def _make_stub_copy(tmp_path: Path) -> Path:
    """Copy the plugin tree and inject a stub top-level `pipeline` command the
    way the plan's registration axiom pins it: one new cli module, one
    import, one `add_pipeline_subparser(sub)` call in dispatch.py."""
    dst = tmp_path / "lore_stub"
    shutil.copytree(PLUGIN_ROOT, dst)

    pipeline_cli = dst / "lore" / "cli" / "pipeline.py"
    pipeline_cli.write_text(
        '"""Stub pipeline subcommand for the U4 probe."""\n'
        "from __future__ import annotations\n\n\n"
        "def add_pipeline_subparser(sub) -> None:\n"
        '    parser = sub.add_parser("pipeline", help="probe stub")\n'
        "    parser.set_defaults(func=lambda args: 0)\n"
    )

    dispatch_path = dst / "lore" / "cli" / "dispatch.py"
    text = dispatch_path.read_text()
    old_import = (
        "from . import areas, flush, init, record, search, session, sync, task, vault"
    )
    new_import = (
        "from . import areas, flush, init, pipeline, record, search, session, sync, task, vault"
    )
    assert old_import in text, "dispatch.py import line shape changed — probe assumption stale"
    text = text.replace(old_import, new_import)

    old_call = "    session.add_session_subparser(sub)\n\n    return parser"
    new_call = (
        "    session.add_session_subparser(sub)\n"
        "    pipeline.add_pipeline_subparser(sub)\n\n"
        "    return parser"
    )
    assert old_call in text, "dispatch.py build_parser() shape changed — probe assumption stale"
    text = text.replace(old_call, new_call)

    dispatch_path.write_text(text)
    return dst


def _run_cli(stub_root: Path, args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "LORE_EMAIL": "tester@example.com",
    }
    cli_path = stub_root / "cli" / "lore"
    return subprocess.run(
        [sys.executable, str(cli_path), *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture()
def stub_root(tmp_path):
    return _make_stub_copy(tmp_path / "src")


class TestU4HelpSurface:
    def test_pipeline_help_renders_at_top_level(self, stub_root, tmp_path):
        """`lore pipeline --help` resolves once registered — sanity check that
        the stub registration actually wires a working subcommand."""
        result = _run_cli(stub_root, ["pipeline", "--help"], tmp_path / "run1")
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("usage:")
        assert "usage: lore pipeline" in result.stdout

    def test_top_level_choices_string_gains_pipeline(self, stub_root, tmp_path):
        """`lore --help` renders `pipeline` in the top-level choices brace —
        proving no argparse_util / render-side change is needed for the new
        command to *appear* correctly."""
        result = _run_cli(stub_root, ["--help"], tmp_path / "run2")
        assert result.returncode == 0
        assert "pipeline" in result.stdout
        # The registration order in the plan's stub places `pipeline` after
        # `session`, at the tail of the existing order.
        assert (
            "{init,status,sync,flush,areas,reindex,search,record,task,vault,session,pipeline}"
            in result.stdout
        )

    def test_existing_hardcoded_choices_string_test_would_now_fail(self, stub_root, tmp_path):
        """Reproduces test_cli_dispatch_split.py::test_top_level_help_lists_every_command_group's
        own assertion against the stubbed parser. If this fails here, the real
        (unmodified) test breaks the moment `pipeline` is registered in
        dispatch.py, and the test's hardcoded string needs updating as part
        of Slice 1."""
        result = _run_cli(stub_root, ["--help"], tmp_path / "run3")
        assert result.returncode == 0
        assert _CURRENT_CHOICES_STRING not in result.stdout, (
            "expected the pre-pipeline choices string to be ABSENT once "
            "pipeline is registered — if this assertion fails, the existing "
            "structural test would NOT need updating, contradicting the "
            "probe's other findings"
        )

    def test_leaf_parsers_untouched_by_new_top_level_command(self, stub_root):
        """`_leaf_parsers` (argparse_util.py) is curated by name — only
        `search` (bare) and `record`/`session` (expanded) — and must neither
        raise nor pick up `pipeline` just because a new top-level command was
        registered. Proves argparse_util.py needs NO change for Slice 1."""
        sys.path.insert(0, str(stub_root))
        try:
            for mod in list(sys.modules):
                if mod == "lore" or mod.startswith("lore."):
                    del sys.modules[mod]
            from lore.argparse_util import _leaf_parsers
            from lore.cli.dispatch import build_parser

            parser = build_parser()
            leaves = _leaf_parsers(parser)
            assert "pipeline" not in leaves
            assert set(leaves) == {
                "search",
                "record create",
                "record delete",
                "record rename",
                "record show",
                "record update",
                "session candidate",
                "session referenced",
                "session show",
            }
        finally:
            sys.path.remove(str(stub_root))
            for mod in list(sys.modules):
                if mod == "lore" or mod.startswith("lore."):
                    del sys.modules[mod]
