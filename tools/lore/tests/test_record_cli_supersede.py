"""``lore record supersede PREDECESSOR --by SUCCESSOR`` through the real CLI.

Every test runs the verb against temp vaults and judges the exit code, the
guard tag on stderr, and the bytes left on disk.
"""

from __future__ import annotations

import json
import shlex
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from conftest import (
    _cli_env,
    make_vault as _make_vault,
    run_cli as _run,
    write_vault_config,
)

BODY = "# Title\n\nline one\nline two\n"
VOLATILE = ("updated-at", "updated-by")


def _create(vault, state, kind, status, title, body=BODY, extra=None):
    args = ["record", "create", "--kind", kind, "--title", title,
            "--keyword", "k", "--status", status, *(extra or [])]
    r = _run(args, vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def _paths(vault: Path, rid: str):
    kind, name = rid.split("/", 1)
    return vault / kind / f"{name}.md", vault / kind / f"{name}.json"


def _sidecar(vault, rid) -> dict:
    return json.loads(_paths(vault, rid)[1].read_text(encoding="utf-8"))


def _body(vault, rid) -> bytes:
    return _paths(vault, rid)[0].read_bytes()


def _snapshot(vault: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(vault)): p.read_bytes()
        for p in sorted(vault.rglob("*"))
        if p.is_file() and p.suffix in (".md", ".json")
    }


def _supersede(vault, state, pred, succ, extra=()):
    return _run(["record", "supersede", pred, "--by", succ, *extra],
                vault=vault, state_dir=state)


def _stable(sidecar: dict) -> dict:
    return {k: v for k, v in sidecar.items() if k not in VOLATILE}


def _outcome(result) -> str:
    return result.stdout.splitlines()[0].split(":")[0]


@pytest.fixture
def env(tmp_path):
    return _make_vault(tmp_path)


def test_adr_pair_writes_edge_and_flips_status_without_touching_bodies(env):
    vault, state = env
    old = _create(vault, state, "adr", "active", "Old")
    new = _create(vault, state, "adr", "active", "New")
    bodies = (_body(vault, old), _body(vault, new))

    r = _supersede(vault, state, old, new)

    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, new)["supersedes"] == [old]
    assert _sidecar(vault, old)["status"] == "superseded"
    assert (_body(vault, old), _body(vault, new)) == bodies
    assert old in r.stdout and new in r.stdout

    assert _run(["reindex"], vault=vault, state_dir=state).returncode == 0
    found = _run(["search", f'superseded-by:"{new}"'], vault=vault, state_dir=state)
    assert old in found.stdout


def test_decision_superseded_by_adr(env):
    vault, state = env
    old = _create(vault, state, "decision", "active", "Old decision")
    new = _create(vault, state, "adr", "active", "New adr")

    r = _supersede(vault, state, old, new)

    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, new)["supersedes"] == [old]
    assert _sidecar(vault, old)["status"] == "superseded"


def test_ready_spec_needs_a_frozen_successor(env):
    vault, state = env
    old = _create(vault, state, "spec", "ready", "Old spec")
    new = _create(vault, state, "spec", "draft", "New spec")
    before = _snapshot(vault)

    refused = _supersede(vault, state, old, new)

    assert refused.returncode != 0
    assert "[supersede-unfrozen-successor]" in refused.stderr
    assert _snapshot(vault) == before

    assert _run(["record", "update", new, "--status", "ready"],
                vault=vault, state_dir=state).returncode == 0
    landed = _supersede(vault, state, old, new)
    assert landed.returncode == 0, landed.stderr
    assert _sidecar(vault, old)["status"] == "superseded"
    assert _sidecar(vault, new)["supersedes"] == [old]


def test_open_task_takes_a_draft_spec_successor(env):
    vault, state = env
    task = _create(vault, state, "task", "open", "Fold into spec")
    spec = _create(vault, state, "spec", "draft", "The spec")

    r = _supersede(vault, state, task, spec)

    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, task)["status"] == "superseded"
    assert _sidecar(vault, spec)["supersedes"] == [task]


def test_rerunning_a_completed_pair_changes_nothing(env):
    vault, state = env
    old = _create(vault, state, "adr", "active", "Old")
    new = _create(vault, state, "adr", "active", "New")
    assert _supersede(vault, state, old, new).returncode == 0
    after_first = _snapshot(vault)

    again = _supersede(vault, state, old, new)

    assert again.returncode == 0, again.stderr
    assert _snapshot(vault) == after_first


def test_a_torn_state_is_completed_by_the_verb(env):
    vault, state = env
    old = _create(vault, state, "adr", "active", "Old")
    new = _create(vault, state, "adr", "active", "New")
    assert _run(["record", "update", new, "--supersedes", old],
                vault=vault, state_dir=state).returncode == 0
    assert _sidecar(vault, old)["status"] == "active"
    edge_bytes = _paths(vault, new)[1].read_bytes()

    r = _supersede(vault, state, old, new)

    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, old)["status"] == "superseded"
    assert _sidecar(vault, new)["supersedes"] == [old]
    assert _paths(vault, new)[1].read_bytes() == edge_bytes


def test_a_second_successor_on_a_superseded_task_adds_its_edge(env):
    vault, state = env
    task = _create(vault, state, "task", "in-progress", "Big task")
    first = _create(vault, state, "task", "open", "First")
    second = _create(vault, state, "task", "open", "Second")
    assert _supersede(vault, state, task, first).returncode == 0

    r = _supersede(vault, state, task, second)

    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, first)["supersedes"] == [task]
    assert _sidecar(vault, second)["supersedes"] == [task]
    assert _sidecar(vault, task)["status"] == "superseded"


def test_split_names_each_child(env):
    vault, state = env
    task = _create(vault, state, "task", "in-progress", "Split me")
    kids = [_create(vault, state, "task", "open", f"Child {i}") for i in range(2)]

    results = [_supersede(vault, state, task, kid) for kid in kids]

    assert [r.returncode for r in results] == [0, 0], [r.stderr for r in results]
    assert all(_sidecar(vault, kid)["supersedes"] == [task] for kid in kids)


def test_outcome_word_follows_the_pair_state(env):
    vault, state = env
    task = _create(vault, state, "task", "open", "Parent")
    first = _create(vault, state, "task", "open", "First")
    second = _create(vault, state, "task", "open", "Second")

    fresh = _supersede(vault, state, task, first)
    added = _supersede(vault, state, task, second)
    repeat = _supersede(vault, state, task, second)

    assert [_outcome(fresh), _outcome(added), _outcome(repeat)] == [
        "superseded", "edge added", "already recorded",
    ]
    assert all(task in r.stdout and s in r.stdout
               for r, s in ((fresh, first), (added, second), (repeat, second)))
    assert all("reindex" in r.stdout for r in (fresh, added, repeat))


def test_a_failed_flip_is_loud_and_the_printed_command_completes_it(env, monkeypatch):
    from lore.record import store

    vault, state = env
    old = _create(vault, state, "adr", "active", "Old")
    new = _create(vault, state, "adr", "active", "New")
    real = store.validate_and_write
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "validate_and_write", flaky)
    r = _supersede(vault, state, old, new)
    monkeypatch.setattr(store, "validate_and_write", real)

    assert r.returncode != 0
    assert _sidecar(vault, new)["supersedes"] == [old]
    assert _sidecar(vault, old)["status"] == "active"
    rerun = r.stderr.strip().splitlines()[-1]
    assert rerun == f"lore record supersede {old} --by {new}"

    fixed = _run(shlex.split(rerun)[1:], vault=vault, state_dir=state)
    assert fixed.returncode == 0, fixed.stderr
    assert _sidecar(vault, old)["status"] == "superseded"


def test_the_documented_undo_restores_both_records(env):
    vault, state = env
    old = _create(vault, state, "adr", "active", "Old")
    new = _create(vault, state, "adr", "active", "New")
    before = {rid: _stable(_sidecar(vault, rid)) for rid in (old, new)}
    assert _supersede(vault, state, old, new).returncode == 0

    assert _run(["record", "update", old, "--status", "active"],
                vault=vault, state_dir=state).returncode == 0
    assert _run(["record", "update", new, "--unset-supersedes", old],
                vault=vault, state_dir=state).returncode == 0

    after = {rid: _stable(_sidecar(vault, rid)) for rid in (old, new)}
    assert {k: {f: v for f, v in s.items() if f != "change-history"} for k, s in after.items()} == \
        {k: {f: v for f, v in s.items() if f != "change-history"} for k, s in before.items()}


def test_superseding_a_depended_on_task_still_surfaces_the_dependents_notice(env):
    vault, state = env
    base = _create(vault, state, "task", "open", "Base")
    _create(vault, state, "task", "open", "Dependent", extra=["--depends-on", base.split("/", 1)[1]])
    heir = _create(vault, state, "task", "open", "Heir")

    r = _supersede(vault, state, base, heir)

    assert r.returncode == 0, r.stderr
    assert "[dependents]" in r.stderr


def test_interleaved_calls_on_one_successor_keep_both_edges(env, monkeypatch):
    from lore import locking
    from lore.cli import dispatch

    vault, state = env
    succ = _create(vault, state, "adr", "active", "Heir")
    preds = [_create(vault, state, "adr", "active", f"Old {i}") for i in (1, 2)]
    for key, value in _cli_env(vault, state, None).items():
        monkeypatch.setenv(key, value)

    real = locking.vault_write_locks
    held = threading.Event()
    requested = {"t1": threading.Event(), "t2": threading.Event()}

    @contextmanager
    def watched(*roots):
        me = threading.current_thread().name
        requested[me].set()
        with real(*roots):
            if me == "t1":
                held.set()
                assert requested["t2"].wait(10)
                time.sleep(0.3)
            yield

    monkeypatch.setattr(locking, "vault_write_locks", watched)
    codes = {}

    def call(name, pred):
        codes[name] = dispatch.main(["record", "supersede", pred, "--by", succ])

    t1 = threading.Thread(target=call, args=("t1", preds[0]), name="t1")
    t1.start()
    assert held.wait(10)
    t2 = threading.Thread(target=call, args=("t2", preds[1]), name="t2")
    t2.start()
    t1.join(30)
    t2.join(30)

    assert codes == {"t1": 0, "t2": 0}
    assert sorted(_sidecar(vault, succ)["supersedes"]) == sorted(preds)


def test_cross_vault_pair_lands_and_reindexes(tmp_path):
    default, state = _make_vault(tmp_path)
    va, vb = tmp_path / "vault_a", tmp_path / "vault_b"
    va.mkdir()
    vb.mkdir()
    cfg = tmp_path / "config"
    write_vault_config(cfg, [("default", "default", default), ("alpha", "team", va),
                             ("beta", "team", vb)])
    extra = {"XDG_CONFIG_HOME": str(cfg)}

    def cli(args):
        return _run(args, vault=default, state_dir=state, env_extra=extra)

    old = cli(["record", "create", "--kind", "adr", "--title", "Old", "--keyword", "k",
               "--status", "active", "--team", "alpha"]).stdout.strip().splitlines()[-1]
    new = cli(["record", "create", "--kind", "adr", "--title", "New", "--keyword", "k",
               "--status", "active", "--team", "beta"]).stdout.strip().splitlines()[-1]

    r = cli(["record", "supersede", old, "--by", new])

    assert r.returncode == 0, r.stderr
    assert _sidecar(vb, new)["supersedes"] == [old]
    assert _sidecar(va, old)["status"] == "superseded"
    cli(["reindex"])
    assert old in cli(["search", f'superseded-by:"{new}"']).stdout


def test_update_only_flags_are_rejected_and_nothing_is_written(env):
    vault, state = env
    old = _create(vault, state, "adr", "active", "Old")
    new = _create(vault, state, "adr", "active", "New")
    before = _snapshot(vault)

    flagged = _run(["record", "supersede", old, "--by", new, "--status", "active"],
                   vault=vault, state_dir=state)
    with_body = _run(["record", "supersede", old, "--by", new],
                     vault=vault, state_dir=state, stdin_text="# replaced\n")

    assert flagged.returncode != 0
    assert "unrecognized arguments" in flagged.stderr
    assert with_body.returncode == 0, with_body.stderr
    after = _snapshot(vault)
    assert {k: v for k, v in after.items() if k.endswith(".md")} == \
        {k: v for k, v in before.items() if k.endswith(".md")}


# --- refusals: each names its guard and leaves both records untouched ---------


def _refused(env, pred, succ, tag, extra=()):
    vault, state = env
    before = _snapshot(vault)
    r = _supersede(vault, state, pred, succ, extra)
    assert r.returncode != 0
    assert f"[{tag}]" in r.stderr
    assert _snapshot(vault) == before


def test_refuses_a_record_superseding_itself(env):
    vault, state = env
    task = _create(vault, state, "task", "open", "Solo")
    _refused(env, task, task, "supersede-self")


def test_refuses_a_predecessor_kind_without_a_superseded_status(env):
    vault, state = env
    lesson = _create(vault, state, "lesson", "active", "A lesson")
    heir = _create(vault, state, "lesson", "active", "Other lesson")
    _refused(env, lesson, heir, "supersede-no-superseded-status")


def test_refuses_a_dropped_predecessor(env):
    vault, state = env
    task = _create(vault, state, "task", "dropped", "Gone")
    heir = _create(vault, state, "task", "open", "Heir")
    _refused(env, task, heir, "supersede-dropped-predecessor")


@pytest.mark.parametrize("status", ["superseded", "dropped"])
def test_refuses_a_dead_successor(env, status):
    vault, state = env
    task = _create(vault, state, "task", "open", "Old")
    heir = _create(vault, state, "task", "open" if status == "superseded" else status, "Dead heir")
    if status == "superseded":
        heir_successor = _create(vault, state, "task", "open", "Dead heir successor")
        assert _supersede(vault, state, heir, heir_successor).returncode == 0
    _refused(env, task, heir, "supersede-dead-successor")


def test_refuses_a_direct_cycle(env):
    vault, state = env
    a = _create(vault, state, "task", "open", "A")
    b = _create(vault, state, "task", "open", "B")
    assert _run(["record", "update", a, "--supersedes", b],
                vault=vault, state_dir=state).returncode == 0
    _refused(env, a, b, "supersede-cycle")


def test_refuses_a_three_record_cycle(env):
    vault, state = env
    a = _create(vault, state, "task", "open", "A")
    b = _create(vault, state, "task", "open", "B")
    c = _create(vault, state, "task", "open", "C")
    for holder, held in ((a, b), (b, c)):
        assert _run(["record", "update", holder, "--supersedes", held],
                    vault=vault, state_dir=state).returncode == 0
    _refused(env, a, c, "supersede-cycle")


@pytest.mark.parametrize("pred_kind,pred_status", [
    ("adr", "active"), ("spec", "ready"), ("decision", "active"),
])
def test_refuses_a_frozen_predecessor_with_an_unfrozen_successor(env, pred_kind, pred_status):
    vault, state = env
    pred = _create(vault, state, pred_kind, pred_status, "Frozen")
    heir = _create(vault, state, "adr", "draft", "Draft heir")
    _refused(env, pred, heir, "supersede-unfrozen-successor")


@pytest.mark.parametrize("missing_side", ["predecessor", "successor"])
def test_refuses_a_missing_record_and_leaves_the_other_untouched(env, missing_side):
    vault, state = env
    real = _create(vault, state, "task", "open", "Real")
    ghost = "task/never-written"
    pred, succ = (ghost, real) if missing_side == "predecessor" else (real, ghost)
    _refused(env, pred, succ, "supersede-missing")
