"""Tests for the ``ranger queue derive`` CLI verb.

A thin argparse shell over ``ranger.sweep.queue.derive_queue`` — these tests
drive the real ``ranger`` CLI shim as a subprocess (matching lore's
``test_task_list_cli.py`` pattern), with a fake ``lore`` executable placed
first on ``PATH`` so the production default runner shells out to a stub
rather than a real vault.

Test contract:
- ``ranger queue derive --vault <name> --json`` prints the derived queue as
  a JSON array.
- The human-readable (non-``--json``) rendering is one line per task, naming
  its bucket.
- A ``lore`` CLI failure (nonzero exit) surfaces as ``ranger: <message>`` on
  stderr, nonzero exit — never a traceback.
- Both lore reads name the vault explicitly; the stub exits nonzero on a
  ``record show`` that omits ``--vault``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"
CLI_PATH = _PLUGIN_DIR / "cli" / "ranger"

_FAKE_LORE_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import sys

    argv = sys.argv[1:]

    if argv[:2] == ["task", "list"]:
        vault = argv[argv.index("--vault") + 1]
        if vault == "missing-vault":
            print(f"lore: vault {vault!r} is not configured", file=sys.stderr)
            sys.exit(1)
        print(json.dumps([
            {
                "name": "t1",
                "status": "open",
                "created-at": "2026-01-01T00:00:00Z",
                "updated-at": "2026-01-01T00:00:00Z",
                "parent": None,
                "depends-on": [],
                "children": [],
            },
        ]))
        sys.exit(0)

    if argv[:2] == ["record", "show"]:
        # A bare `record show` scans configured vaults in declaration order,
        # cwd-blind — so a task name present in two vaults would be read from
        # the wrong one. Every ranger read names its vault.
        if "--vault" not in argv:
            print(f"fake lore: record show without --vault: {argv!r}", file=sys.stderr)
            sys.exit(2)
        name = argv[2].split("/", 1)[1]
        print(json.dumps({
            "record_id": argv[2],
            "kind": "task",
            "name": name,
            "sidecar": {},
            "body": "# t\\n\\nSome prose.\\n",
        }))
        sys.exit(0)

    print(f"fake lore: unexpected argv {argv!r}", file=sys.stderr)
    sys.exit(2)
    """
)


def _fake_lore_path(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lore_path = bin_dir / "lore"
    lore_path.write_text(_FAKE_LORE_SCRIPT, encoding="utf-8")
    lore_path.chmod(lore_path.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run(args, *, tmp_path: Path):
    bin_dir = _fake_lore_path(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_queue_derive_json_prints_the_derived_queue(tmp_path):
    res = _run(["queue", "derive", "--vault", "myvault", "--json"], tmp_path=tmp_path)
    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert entries == [
        {
            "name": "t1",
            "status": "open",
            "created-at": "2026-01-01T00:00:00Z",
            "updated-at": "2026-01-01T00:00:00Z",
            "parent": None,
            "depends-on": [],
            "children": [],
            "bucket": "dispatchable",
            "answer_near_miss": False,
        },
    ]


def test_queue_derive_human_rendering_is_one_line_per_task(tmp_path):
    res = _run(["queue", "derive", "--vault", "myvault"], tmp_path=tmp_path)
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "t1" in lines[0]
    assert "bucket=dispatchable" in lines[0]


def test_queue_derive_surfaces_lore_failure_as_named_error(tmp_path):
    res = _run(["queue", "derive", "--vault", "missing-vault", "--json"], tmp_path=tmp_path)
    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "missing-vault" in res.stderr


@pytest.mark.parametrize(
    "bad_vault",
    ["prod`touch pwn`", "prod$(id)", "prod;id", "prod name", "prod'quote", "prod\nline"],
    ids=["backtick", "dollar-paren", "semicolon", "space", "quote", "newline"],
)
def test_queue_derive_refuses_shell_metacharacters_in_vault(tmp_path, bad_vault):
    """`--vault` is validated before `derive_queue` ever shells out to lore —
    the same shell-safe allowlist `sweep start` holds its elected vault to."""
    res = _run(["queue", "derive", "--vault", bad_vault, "--json"], tmp_path=tmp_path)

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")


def test_queue_derive_accepts_actionable_for_parity_with_sweep_derive(tmp_path):
    """The diagnostic verb carries the same flag as the sweep's own view.

    The filtering itself is covered against `ranger.sweep.queue.actionable`
    and through `sweep derive`, where a fixture with all four buckets makes
    the assertion meaningful; this fixture holds one dispatchable task, so
    what is pinned here is only that the two verbs stay flag-compatible.
    """
    res = _run(["queue", "derive", "--vault", "myvault", "--actionable", "--json"],
               tmp_path=tmp_path)

    assert res.returncode == 0, res.stderr
    assert [e["name"] for e in json.loads(res.stdout)] == ["t1"]
