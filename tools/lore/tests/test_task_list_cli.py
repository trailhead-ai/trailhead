"""Tests for ``lore task list --vault <name> [--status ...] [--json]`` — the
flat task-listing verb ranger's sweep shells out to for a named vault's queue.

Covers the test contract:

  - A fixture vault → every task record with the seven fields
    (name/status/created-at/updated-at/parent/depends-on/children).
  - ``children`` is computed from ``parent`` back-edges (containment, not
    ``depends-on``).
  - A task with no ``parent`` emits it as ``null``, consistently (not
    sometimes-absent).
  - Ordering: oldest-first by ``created-at``, with a record-name tiebreak on
    equal timestamps.
  - ``--status open --status blocked`` (repeatable) narrows to those statuses.
  - A malformed sidecar is skipped silently (matching
    ``load_task_sidecars``'s posture) — the rest still list.
  - An unknown ``--vault`` name is refused: ``lore: <msg>`` on stderr, nonzero.
  - ``lore task graph`` routing is unaffected — ``cmd_task`` still dispatches
    both actions.

Tests run the CLI as a subprocess via ``CLI_PATH`` (conftest pattern), fencing
``XDG_STATE_HOME``/``XDG_CONFIG_HOME`` under ``tmp_path`` so the real vault,
state, and config are never touched (Axiom 6). Task sidecars are written
directly to ``<vault>/task/*.json`` — bypassing ``record create`` routing —
so each fixture pins exact ``created-at`` ordering and a controlled malformed
sidecar, matching ``test_record_guards.py``'s direct-sidecar-write pattern.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import CLI_PATH  # noqa: E402


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _run(args, *, state, config, extra=None):
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["LORE_EMAIL"] = "tester@example.com"
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir()
    config.mkdir()
    return state, config


def _write_config(config, vaults):
    """``vaults`` is a list of raw vault-entry dicts, written verbatim."""
    cfg_path = config / "lore" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"vaults": vaults}), encoding="utf-8")


def _write_task(vault: Path, name: str, sidecar: dict) -> None:
    task_dir = vault / "task"
    task_dir.mkdir(parents=True, exist_ok=True)
    body = {"kind": "task", **sidecar}
    (task_dir / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


_SEVEN_FIELDS = {
    "name", "status", "created-at", "updated-at", "parent", "depends-on", "children",
}


# ---------------------------------------------------------------------------
# Fixture vault → every task, seven fields, children from parent back-edges
# ---------------------------------------------------------------------------


def test_lists_every_task_with_the_seven_fields(tmp_path):
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])

    _write_task(vault, "root", {
        "status": "in-progress",
        "created-at": "2026-01-01T00:00:00Z",
        "updated-at": "2026-01-02T00:00:00Z",
    })
    _write_task(vault, "child", {
        "status": "ready",
        "created-at": "2026-01-02T00:00:00Z",
        "updated-at": "2026-01-02T00:00:00Z",
        "parent": "root",
    })

    res = _run(["task", "list", "--vault", "default", "--json"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    by_name = {e["name"]: e for e in entries}
    assert set(by_name) == {"root", "child"}
    assert set(by_name["root"].keys()) == _SEVEN_FIELDS

    assert by_name["root"]["status"] == "in-progress"
    assert by_name["root"]["created-at"] == "2026-01-01T00:00:00Z"
    assert by_name["root"]["updated-at"] == "2026-01-02T00:00:00Z"
    assert by_name["root"]["parent"] is None
    assert by_name["root"]["depends-on"] == []
    assert by_name["root"]["children"] == ["child"]

    assert by_name["child"]["parent"] == "root"
    assert by_name["child"]["children"] == []


def test_parent_is_null_not_absent_when_missing(tmp_path):
    """Every entry carries the ``parent`` key — never omitted, even when unset."""
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])
    _write_task(vault, "solo", {"status": "open", "created-at": "2026-01-01T00:00:00Z"})

    res = _run(["task", "list", "--vault", "default", "--json"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert entries[0]["parent"] is None
    assert "parent" in entries[0]


# ---------------------------------------------------------------------------
# Ordering: oldest-first by created-at, name tiebreak on equal timestamps
# ---------------------------------------------------------------------------


def test_ordering_is_oldest_first_by_created_at(tmp_path):
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])

    _write_task(vault, "newest", {"status": "open", "created-at": "2026-03-01T00:00:00Z"})
    _write_task(vault, "oldest", {"status": "open", "created-at": "2026-01-01T00:00:00Z"})
    _write_task(vault, "middle", {"status": "open", "created-at": "2026-02-01T00:00:00Z"})

    res = _run(["task", "list", "--vault", "default", "--json"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert [e["name"] for e in entries] == ["oldest", "middle", "newest"]


def test_ordering_tiebreaks_on_name_when_created_at_equal(tmp_path):
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])

    same_ts = "2026-01-01T00:00:00Z"
    _write_task(vault, "zeta", {"status": "open", "created-at": same_ts})
    _write_task(vault, "alpha", {"status": "open", "created-at": same_ts})

    res = _run(["task", "list", "--vault", "default", "--json"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert [e["name"] for e in entries] == ["alpha", "zeta"]


# ---------------------------------------------------------------------------
# --status filter (repeatable)
# ---------------------------------------------------------------------------


def test_status_filter_narrows_to_the_given_statuses(tmp_path):
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])

    _write_task(vault, "a", {"status": "open", "created-at": "2026-01-01T00:00:00Z"})
    _write_task(vault, "b", {"status": "blocked", "created-at": "2026-01-02T00:00:00Z"})
    _write_task(vault, "c", {"status": "done", "created-at": "2026-01-03T00:00:00Z"})

    res = _run(
        ["task", "list", "--vault", "default", "--status", "open", "--status", "blocked", "--json"],
        state=state, config=config,
    )
    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert {e["name"] for e in entries} == {"a", "b"}


# ---------------------------------------------------------------------------
# Malformed sidecar is skipped silently; the rest still list
# ---------------------------------------------------------------------------


def test_malformed_sidecar_is_skipped_silently(tmp_path):
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])

    _write_task(vault, "good", {"status": "open", "created-at": "2026-01-01T00:00:00Z"})
    task_dir = vault / "task"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "bad.json").write_text("{not json", encoding="utf-8")

    res = _run(["task", "list", "--vault", "default", "--json"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert [e["name"] for e in entries] == ["good"]


# ---------------------------------------------------------------------------
# Unknown --vault → "lore: <msg>" stderr, nonzero
# ---------------------------------------------------------------------------


def test_unknown_vault_is_rejected(tmp_path):
    state, config = _dirs(tmp_path)
    _write_config(config, [{"name": "default", "scope": "default"}])

    res = _run(["task", "list", "--vault", "nope", "--json"], state=state, config=config)
    assert res.returncode != 0
    assert res.stderr.startswith("lore: ")
    assert "nope" in res.stderr


def test_missing_config_is_rejected(tmp_path):
    """No ``config.json`` at all — the named vault cannot be resolved either way."""
    state, config = _dirs(tmp_path)
    res = _run(["task", "list", "--vault", "anything", "--json"], state=state, config=config)
    assert res.returncode != 0
    assert res.stderr.startswith("lore: ")


# ---------------------------------------------------------------------------
# ``lore task graph`` routing is unaffected
# ---------------------------------------------------------------------------


def test_task_graph_still_works(tmp_path):
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])
    _write_task(vault, "root", {"status": "open", "created-at": "2026-01-01T00:00:00Z"})

    res = _run(["task", "graph", "root"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    assert "root [open]" in res.stdout


def test_task_list_registered_in_task_help(tmp_path):
    state, config = _dirs(tmp_path)
    res = _run(["task", "--help"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    assert "list" in res.stdout


# ---------------------------------------------------------------------------
# Human-readable (non-JSON) rendering — one line per task
# ---------------------------------------------------------------------------


def test_human_rendering_without_json_is_one_line_per_task(tmp_path):
    state, config = _dirs(tmp_path)
    vault = tmp_path / "v-default"
    vault.mkdir()
    _write_config(config, [{"name": "default", "scope": "default", "path": str(vault)}])
    _write_task(vault, "solo", {"status": "open", "created-at": "2026-01-01T00:00:00Z"})

    res = _run(["task", "list", "--vault", "default"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "solo" in lines[0]
    assert "status=open" in lines[0]
