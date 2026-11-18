"""``lore distill-queue`` end to end: ``run_cli`` in this process against fixture vaults.

Every vault, config and index lives under ``tmp_path``. Failures are caused on the
real disk (chmod). One test runs the real process.
"""

import importlib
import json
import os
import subprocess
import sys

import pytest

from conftest import CLI_PATH, run_cli, write_vault_config

HEADER = "lore distill-queue — "


def _put(root, kind, name, status="planned", updated="2026-03-01T00:00:00Z", **extra):
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    side = {"title": name, "status": status, "created-at": "2026-01-01T00:00:00Z",
            "updated-at": updated, **extra}
    (d / f"{name}.json").write_text(json.dumps(side))
    (d / f"{name}.md").write_text("text\n")


def _link(root, spec_name, task_name=None):
    _put(root, "task", task_name or f"t-{spec_name}", status="open", related={"spec": [spec_name]})


class World:
    def __init__(self, tmp_path, names=("default", "trailhead")):
        self.tmp_path = tmp_path
        self.state = tmp_path / "state"
        self.cfg = tmp_path / "config"
        (self.state / "lore" / "vaults").mkdir(parents=True)
        self.roots = {}
        triples = []
        for n in names:
            root = tmp_path / "vaults" / f"v{len(self.roots)}"
            root.mkdir(parents=True)
            self.roots[n] = root
            triples.append((n, "default" if n == "default" else "team", root))
        write_vault_config(self.cfg, triples)

    def env(self):
        return {"XDG_CONFIG_HOME": str(self.cfg)}

    def run(self, *args):
        return run_cli(["distill-queue", *args], vault=self.roots["default"], state_dir=self.state,
                       env_extra=self.env())


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _snapshot(*roots):
    snap = {}
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            for entry in dirs + files:
                p = os.path.join(dirpath, entry)
                if "_xdg_config" in p:
                    continue
                st = os.lstat(p)
                data = open(p, "rb").read() if os.path.isfile(p) and not os.path.islink(p) else None
                snap[p] = (data, st.st_mtime_ns)
    return snap


def test_prints_waiting_oldest_first_then_deferred_in_the_design_shape_and_exits_zero(world):
    t, d = world.roots["trailhead"], world.roots["default"]
    _put(t, "spec", "streaming-export", "ready", updated="2026-09-02T00:00:00Z",
         labels={"craft/slice-loop": "complete"})
    _put(t, "spec", "notes-are-records", "planned", updated="2026-10-11T00:00:00Z")
    _put(d, "spec", "audio-on-demand", "ready", updated="2026-11-01T00:00:00Z",
         labels={"craft/slice-loop": "complete"}, annotations={"distilled": "adr"})
    _put(d, "spec", "sketch-the-sync-design", "complete", updated="2026-06-30T00:00:00Z")
    for s, root in (("streaming-export", t), ("notes-are-records", t), ("audio-on-demand", d)):
        _link(root, s)
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        "lore distill-queue — 3 waiting, 1 deferred",
        "",
        "waiting:",
        "  trailhead/spec/streaming-export  2026-09-02  ready  slice loop complete, not yet distilled",
        "  trailhead/spec/notes-are-records  2026-10-11  planned  planned, not yet distilled",
        "  default/spec/audio-on-demand  2026-11-01  ready  slice loop complete, previously distilled",
        "",
        "deferred, no linked task (distill each by target):",
        "  default/spec/sketch-the-sync-design  2026-06-30  complete  complete, never distilled",
    ]


def test_a_waiting_spec_held_by_a_draft_adr_edge_says_why_after_the_reason(world):
    t = world.roots["trailhead"]
    _put(t, "adr", "anchor", "draft")
    _put(t, "spec", "s", "planned", related={"adr": ["anchor"]})
    _link(t, "s")
    r = world.run()
    assert "  trailhead/spec/s  2026-03-01  planned  planned, not yet distilled; its ADR is still draft" in r.stdout


def test_only_deferred_specs_omit_the_waiting_section(world):
    _put(world.roots["default"], "spec", "alone", "complete")
    r = world.run()
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[0] == "lore distill-queue — 0 waiting, 1 deferred"
    assert "waiting:" not in lines
    assert "deferred, no linked task (distill each by target):" in lines


def test_with_nothing_waiting_it_prints_the_nothing_waiting_line_and_exits_zero(world):
    _put(world.roots["default"], "spec", "d", "draft")
    r = world.run()
    assert r.returncode == 0
    assert r.stdout.splitlines() == ["lore distill-queue — nothing waiting"]


def test_a_vault_whose_directory_is_unreadable_is_refused_naming_it_and_the_fallback(world):
    _put(world.roots["default"], "spec", "s", "planned")
    os.chmod(world.roots["trailhead"], 0)
    try:
        r = world.run()
    finally:
        os.chmod(world.roots["trailhead"], 0o755)
    assert r.returncode == 1
    assert r.stdout == ""
    assert r.stderr.splitlines() == [
        "lore: vault trailhead could not be read, so the distill queue is refused rather than built partial; "
        "repair or remove the vault to sweep again, or distill one spec by name with /craft:distill spec/<name>"]


def test_a_missing_vault_directory_is_refused_and_never_reads_as_an_empty_queue(world):
    os.rmdir(world.roots["trailhead"])
    r = world.run()
    assert r.returncode == 1
    assert HEADER not in r.stdout + r.stderr
    assert r.stderr.startswith("lore: vault trailhead could not be read")


def test_a_single_unreadable_record_does_not_refuse(world):
    d = world.roots["default"]
    _put(d, "spec", "s", "planned")
    _link(d, "s")
    (d / "lesson").mkdir()
    (d / "lesson" / "bad.json").write_text("{not json")
    (d / "lesson" / "bad.md").write_text("x")
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "lore distill-queue — 1 waiting, 0 deferred"


def test_unreadable_config_is_one_lore_line_exit_one_and_no_header(world):
    (world.cfg / "lore" / "config.json").write_text("{not json")
    r = world.run()
    assert r.returncode == 1
    assert r.stdout == ""
    assert r.stderr.startswith("lore: ") and len(r.stderr.splitlines()) == 1


@pytest.mark.parametrize("n_waiting", [0, 1])
def test_a_success_always_starts_with_the_header(world, n_waiting):
    if n_waiting:
        _put(world.roots["default"], "spec", "s", "planned")
        _link(world.roots["default"], "s")
    r = world.run()
    assert r.returncode == 0
    assert r.stdout.startswith(HEADER)


def test_a_spec_name_that_is_not_a_valid_record_id_prints_the_fixed_phrase_never_the_name(world):
    d = world.roots["default"]
    _put(d, "spec", "Bad Name\x1b[2J", "planned")
    r = world.run()
    assert r.returncode == 0
    assert "default/spec/ (a record whose name is not a valid record id)" in r.stdout
    assert "Bad Name" not in r.stdout and "\x1b" not in r.stdout


def test_a_vault_name_with_a_control_character_is_printed_escaped(tmp_path):
    evil = "ev\nil\x1b[31mv"
    w = World(tmp_path, names=("default", evil))
    _put(w.roots[evil], "spec", "s", "planned")
    _link(w.roots[evil], "s")
    r = w.run()
    assert r.returncode == 0, r.stderr
    assert "\x1b" not in r.stdout
    assert "  ev\\nil\\x1b[31mv/spec/s  2026-03-01  planned  planned, not yet distilled" in r.stdout


def test_a_refusal_escapes_the_vault_name(tmp_path):
    evil = "ev\nil\x1b[31mv"
    w = World(tmp_path, names=("default", evil))
    os.chmod(w.roots[evil], 0)
    try:
        r = w.run()
    finally:
        os.chmod(w.roots[evil], 0o755)
    assert r.returncode == 1
    assert "\x1b" not in r.stderr and len(r.stderr.splitlines()) == 1
    assert "vault ev\\nil\\x1b[31mv could not be read" in r.stderr


def test_a_run_creates_and_changes_no_vault_file_index_or_lock(world):
    t, d = world.roots["trailhead"], world.roots["default"]
    _put(t, "spec", "s", "planned")
    _link(t, "s")
    _put(d, "spec", "lonely", "complete")
    before = _snapshot(*world.roots.values(), world.state, world.cfg)
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert _snapshot(*world.roots.values(), world.state, world.cfg) == before


def test_an_unexpected_failure_is_one_lore_line_exit_one_and_never_an_empty_queue(world, monkeypatch):
    dq_cli = importlib.import_module("lore.cli.distill_queue")

    def boom(scans):
        raise RuntimeError("kaput")

    monkeypatch.setattr(dq_cli.queue_mod, "build_distill_queue", boom)
    r = world.run()
    assert r.returncode == 1
    assert r.stdout == ""
    assert r.stderr.splitlines() == ["lore: distill queue failed: RuntimeError: kaput"]


def test_the_command_scans_with_the_index_probe_off_and_health_and_validate_leave_it_on(world, monkeypatch):
    dq_cli = importlib.import_module("lore.cli.distill_queue")
    health_cli = importlib.import_module("lore.cli.health")
    validate_cli = importlib.import_module("lore.cli.validate")
    calls = []
    real = dq_cli._scan_vault

    def spy(name, root, **kw):
        calls.append(kw)
        return real(name, root, **kw)

    monkeypatch.setattr(dq_cli, "_scan_vault", spy)
    monkeypatch.setattr(health_cli, "_scan_vault", spy)
    monkeypatch.setattr(validate_cli, "_scan_vault", spy)
    _put(world.roots["default"], "spec", "s", "planned")
    run_cli(["distill-queue"], vault=world.roots["default"], state_dir=world.state, env_extra=world.env())
    assert calls and all(c.get("probe_index") is False for c in calls)
    n = len(calls)
    run_cli(["health"], vault=world.roots["default"], state_dir=world.state, env_extra=world.env())
    assert len(calls) > n and all("probe_index" not in c for c in calls[n:])
    m = len(calls)
    run_cli(["validate"], vault=world.roots["default"], state_dir=world.state, env_extra=world.env())
    assert len(calls) > m and all("probe_index" not in c for c in calls[m:])


def test_help_describes_the_command(world):
    r = world.run("--help")
    assert r.returncode == 0
    assert r.stdout.startswith("usage: lore distill-queue")


@pytest.mark.seam_real
def test_the_real_process_prints_the_queue_end_to_end(world):
    _put(world.roots["default"], "spec", "s", "planned", updated="2026-02-03T00:00:00Z")
    _link(world.roots["default"], "s")
    env = {**os.environ, "XDG_STATE_HOME": str(world.state), "XDG_CONFIG_HOME": str(world.cfg),
           "LORE_EMAIL": "tester@example.com"}
    p = subprocess.run([sys.executable, str(CLI_PATH), "distill-queue"], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == [
        "lore distill-queue — 1 waiting, 0 deferred", "", "waiting:",
        "  default/spec/s  2026-02-03  planned  planned, not yet distilled"]
