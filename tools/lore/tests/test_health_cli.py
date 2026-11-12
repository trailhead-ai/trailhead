"""``lore health`` end to end: ``run_cli`` in this process against fixture vaults.

Every vault, config and index lives under ``tmp_path``; the autouse ambient-env
fixture in conftest plus the per-call ``XDG_CONFIG_HOME`` keep the live install
out of reach. Failures are caused on the real disk (chmod, symlinks).
"""

import importlib
import json
import os
import re
import shutil

import pytest

from conftest import load_script, run_cli, write_vault_config

ROW_RE = re.compile(r"^(\S+)\s+dangling-links\s+(\S+)\s+(\S+)\s+(.*)$")
FINDING_RE = re.compile(r"^  \S.*(not found in any vault|not shaped like a record id)$")


def _put(root, kind, name, updated="2026-03-01", body="text\n", **extra):
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    side = {"title": name, "status": "active", "created-at": "2026-01-01", **extra}
    if updated is not None:
        side["updated-at"] = updated
    (d / f"{name}.json").write_text(json.dumps(side))
    (d / f"{name}.md").write_text(body)


class World:
    def __init__(self, tmp_path, names=("default", "trailhead", "levr")):
        self.tmp_path = tmp_path
        self.state = tmp_path / "state"
        self.cfg = tmp_path / "config"
        # config validation creates the vaults root when a vault has an explicit
        # path; an installed lore already has it.
        (self.state / "lore" / "vaults").mkdir(parents=True)
        self.roots = {}
        triples = []
        for n in names:
            root = tmp_path / "vaults" / f"v{len(self.roots)}"
            root.mkdir(parents=True)
            self.roots[n] = root
            triples.append((n, "default" if n == "default" else "team", root))
        write_vault_config(self.cfg, triples)

    def run(self, *args):
        return run_cli(
            ["health", *args],
            vault=self.roots["default"],
            state_dir=self.state,
            env_extra={"XDG_CONFIG_HOME": str(self.cfg)},
        )

    def index(self):
        idx = load_script("lore.search.index")
        conn = idx.open_index(env={"XDG_STATE_HOME": str(self.state)})
        try:
            idx.rebuild([str(r) for r in self.roots.values()], conn)
            conn.commit()
        finally:
            conn.close()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def rows(out):
    found = {}
    for line in out.splitlines():
        m = ROW_RE.match(line)
        if m:
            found[m.group(1)] = m.groups()[1:]
    return found


def finding_lines(out):
    return [ln for ln in out.splitlines() if FINDING_RE.match(ln)]


def _twenty_five(root):
    for i in range(25):
        _put(root, "task", f"t{i:02d}", updated=f"2026-02-{i + 1:02d}",
             related={"lesson": [f"gone-{i:02d}"]})
    _put(root, "task", "t-undated", updated=None, related={"lesson": ["gone-undated"]})
    # 26 records, but one of the dated ones is removed so exactly 25 findings remain
    (root / "task" / "t24.json").unlink()
    (root / "task" / "t24.md").unlink()


# --- findings and the cap ----------------------------------------------------


def test_report_survives_an_in_place_reload_of_the_dangling_check(world):
    """``load_script`` reloads a module in place, which replaces its classes.

    After the dangling check's module is reloaded, the already-imported command
    must still classify the check's result: one finding is reported, exit 0.
    """
    _put(world.roots["default"], "lesson", "a", related={"decision": ["gone"]})
    assert world.run().returncode == 0  # the command module is now imported
    load_script("lore.health.dangling")
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert rows(r.stdout)["default"] == ("1", "1", "no")
    assert len(finding_lines(r.stdout)) == 1


def test_no_findings_every_row_clean_and_group_says_so(world):
    _put(world.roots["default"], "lesson", "a", related={"decision": ["b"]})
    _put(world.roots["default"], "decision", "b")
    _put(world.roots["trailhead"], "lesson", "c", body="see [[b]]\n")
    _put(world.roots["levr"], "session", "s1")
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert rows(r.stdout) == {
        "default": ("2", "0", "no"),
        "trailhead": ("1", "0", "no"),
        "levr": ("0", "0", "no"),
    }
    assert "dangling-links — no findings." in r.stdout
    assert "/lore:correct" not in r.stdout


def test_one_dangling_related_edge_is_one_finding_line(world):
    _put(world.roots["trailhead"], "decision", "use-a-pooled-client", updated="2026-03-14T10:00:00Z",
         related={"decision": ["pool-size-limits"]})
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert finding_lines(r.stdout) == [
        "  trailhead/decision/use-a-pooled-client  2026-03-14  "
        "related decision → decision/pool-size-limits: not found in any vault"
    ]
    assert "dangling-links — 1 finding, oldest first" in r.stdout
    assert "/lore:correct" in r.stdout
    assert rows(r.stdout)["trailhead"] == ("1", "1", "no")


def test_default_cap_shows_twenty_oldest_first_undated_first(world):
    _twenty_five(world.roots["default"])
    r = world.run()
    lines = finding_lines(r.stdout)
    assert r.returncode == 0, r.stderr
    assert len(lines) == 20
    assert "default/task/t-undated" in lines[0]
    dates = [re.search(r"  (\d{4}-\d\d-\d\d|undated)  ", ln).group(1) for ln in lines]
    assert dates[0] == "undated" and dates[1:] == sorted(dates[1:])
    assert "5 more." in r.stdout
    assert "default/task/t23" not in r.stdout  # newest are the ones cut


@pytest.mark.parametrize(
    "extra,shown,more",
    [(["--limit", "22"], 22, "3 more."), (["--all"], 25, None), (["--all", "--limit", "3"], 25, None)],
)
def test_limit_and_all_change_how_many_are_shown(world, extra, shown, more):
    _twenty_five(world.roots["default"])
    r = world.run(*extra)
    assert len(finding_lines(r.stdout)) == shown
    if more:
        assert more in r.stdout
    else:
        assert " more." not in r.stdout


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_non_positive_limit_is_rejected_by_argparse(world, bad):
    r = world.run("--limit", bad)
    assert r.returncode == 2
    assert finding_lines(r.stdout) == []


def test_non_string_updated_at_sorts_oldest_without_raising(world):
    _put(world.roots["default"], "lesson", "dated", updated="2020-01-01", related={"lesson": ["x1"]})
    _put(world.roots["default"], "lesson", "weird", updated=7, related={"lesson": ["x2"]})
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert [ln.split()[0] for ln in finding_lines(r.stdout)] == [
        "default/lesson/weird", "default/lesson/dated"]


def test_cap_hint_repeats_the_operators_vault(world):
    _twenty_five(world.roots["trailhead"])
    narrowed = world.run("--vault", "trailhead")
    wide = world.run()
    assert "`lore health --vault trailhead --all`" in narrowed.stdout
    assert "`lore health --all`" in wide.stdout


# --- vault scope and skips ----------------------------------------------------


def test_default_run_has_one_row_per_vault(world):
    r = world.run()
    assert sorted(rows(r.stdout)) == ["default", "levr", "trailhead"]


def test_vault_flag_narrows_rows_but_targets_resolve_everywhere(world):
    _put(world.roots["levr"], "lesson", "only-in-levr")
    _put(world.roots["trailhead"], "task", "t", related={"lesson": ["only-in-levr"]})
    _put(world.roots["default"], "task", "d", related={"lesson": ["nowhere"]})
    r = world.run("--vault", "trailhead")
    assert r.returncode == 0, r.stderr
    assert list(rows(r.stdout)) == ["trailhead"]
    assert finding_lines(r.stdout) == []
    assert "targets looked up in all 3" in r.stdout


def test_unreadable_vault_skips_every_row_and_names_it(world):
    _put(world.roots["default"], "task", "d", related={"lesson": ["nowhere"]})
    world.index()
    os.chmod(world.roots["levr"], 0)
    try:
        r = world.run()
    finally:
        os.chmod(world.roots["levr"], 0o755)
    assert r.returncode == 0, r.stderr
    got = rows(r.stdout)
    assert sorted(got) == ["default", "levr", "trailhead"]
    for vault, (records, findings, skipped) in got.items():
        assert (records, findings) == ("—", "—"), vault
        assert skipped.startswith("yes:"), vault
    for v in ("default", "trailhead"):
        assert rows(r.stdout)[v][2] == "yes: vault levr could not be read, so no target can be confirmed missing", v
    assert rows(r.stdout)["levr"][2] == "yes: its directory could not be read"
    assert finding_lines(r.stdout) == []
    assert "no findings" not in r.stdout
    assert "dangling-links — skipped." in r.stdout
    assert ("Could not confirm the count against the index: vault levr was not fully read. "
            "1 record read.") in r.stdout


def test_records_outside_the_vault_root_skip_the_check_and_are_counted_nameless(world, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret-name.md").write_text("x")
    (world.roots["trailhead"] / "lesson").mkdir()
    (world.roots["trailhead"] / "lesson" / "esc.md").symlink_to(outside / "secret-name.md")
    (world.roots["trailhead"] / "lesson" / "esc.json").write_text("{}")
    world.index()
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert ("Could not confirm the count against the index: vault trailhead was not fully read. "
            "0 records read.") in r.stdout
    assert "trailhead: 1 record lies outside the vault root and was not read." in r.stdout
    assert "secret-name" not in r.stdout and "elsewhere" not in r.stdout
    assert all(v[2].startswith("yes:") for v in rows(r.stdout).values())
    assert rows(r.stdout)["default"][2] == (
        "yes: vault trailhead could not be fully read, so no target can be confirmed missing")
    assert rows(r.stdout)["trailhead"][2] == "yes: records lie outside its vault root and were not read"


# --- the index line and unreadable records ------------------------------------


def test_index_match_line_counts_sessions(world):
    _put(world.roots["default"], "lesson", "a")
    _put(world.roots["default"], "session", "s1")
    _put(world.roots["levr"], "lesson", "b")
    world.index()
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert rows(r.stdout)["default"][0] == "1"
    assert "Records read match the index (sessions included): 3 read, 3 indexed." in r.stdout


def test_index_missing_a_record_names_the_vault_with_both_numbers(world):
    _put(world.roots["levr"], "lesson", "b")
    world.index()
    _put(world.roots["levr"], "lesson", "c")
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert ("Records read differ from the index for levr: 2 read, 1 indexed. "
            "Run `lore reindex`, then run the report again.") in r.stdout
    assert "default:" not in r.stdout.split("differ from the index")[1].splitlines()[0]


def test_no_index_says_the_count_could_not_be_confirmed(world):
    _put(world.roots["levr"], "lesson", "b")
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert "Could not confirm the count against the index: no index found. 1 record read." in r.stdout
    assert "match the index" not in r.stdout


def test_malformed_sidecar_is_reported_and_drops_the_judged_count(world):
    _put(world.roots["default"], "lesson", "ok")
    before = rows(world.run().stdout)["default"][0]
    (world.roots["default"] / "lesson" / "bad.json").write_text("{not json")
    (world.roots["default"] / "lesson" / "bad.md").write_text("x")
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert "default: 1 record could not be read and was not checked." in r.stdout
    assert (before, rows(r.stdout)["default"][0]) == ("1", "1")
    _put(world.roots["default"], "lesson", "ok2")
    assert rows(world.run().stdout)["default"][0] == "2"


# --- refusals and failures ----------------------------------------------------


def test_unknown_vault_refuses_with_one_line_naming_configured_vaults(world):
    r = world.run("--vault", "scratch")
    assert r.returncode == 1
    assert r.stdout == ""
    assert r.stderr.splitlines() == [
        "lore: cannot run the health report: no vault named 'scratch' is configured "
        "(configured: default, trailhead, levr)"
    ]


def test_invalid_config_takes_the_same_refusal(world):
    (world.cfg / "lore" / "config.json").write_text("{not json")
    r = world.run()
    assert r.returncode == 1
    assert len(r.stderr.splitlines()) == 1
    assert r.stderr.startswith("lore: cannot run the health report: ")


def test_missing_config_runs_against_the_single_default_vault(world):
    (world.cfg / "lore" / "config.json").unlink()
    default = world.state / "lore" / "vaults" / "default"
    _put(default, "task", "t", related={"lesson": ["nowhere"]})
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert list(rows(r.stdout)) == ["default"]
    assert len(finding_lines(r.stdout)) == 1


def test_fault_injected_into_the_report_build_is_one_lore_line(world, monkeypatch):
    health = importlib.import_module("lore.cli.health")

    def boom(name, root):
        raise RuntimeError("boom\nsecond line")

    monkeypatch.setattr(health, "_scan_vault", boom)
    r = world.run()
    assert r.returncode == 1
    assert r.stdout == ""
    assert len(r.stderr.splitlines()) == 1
    assert r.stderr.startswith("lore: ")
    assert "Traceback" not in r.stderr


# --- read-only and output safety ---------------------------------------------


def _snapshot(*roots):
    snap = {}
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            for entry in dirs + files:
                p = os.path.join(dirpath, entry)
                if "_xdg_config" in p:  # run_cli rewrites its own seed config on every call
                    continue
                st = os.lstat(p)
                data = open(p, "rb").read() if os.path.isfile(p) and not os.path.islink(p) else None
                snap[p] = (data, st.st_mtime_ns)
    return snap


def test_run_is_read_only_end_to_end(world):
    _put(world.roots["default"], "task", "t", related={"lesson": ["nowhere"]})
    _put(world.roots["levr"], "lesson", "b")
    world.index()
    _put(world.roots["levr"], "lesson", "c")
    (world.roots["default"] / "lesson").mkdir()
    (world.roots["default"] / "lesson" / "bad.json").write_text("{x")
    (world.roots["default"] / "lesson" / "bad.md").write_text("x")
    before = _snapshot(*world.roots.values(), world.state, world.cfg)
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert "not found in any vault" in r.stdout and "differ from the index" in r.stdout
    assert _snapshot(*world.roots.values(), world.state, world.cfg) == before


EVIL_VAULT = "ev\nil\x1b[31mv"
EVIL_NAME = "rec\nforged\x1b[2Jname"


@pytest.fixture
def evil(tmp_path):
    w = World(tmp_path, names=("default", EVIL_VAULT))
    _put(w.roots[EVIL_VAULT], "lesson", EVIL_NAME, related={"lesson": ["gone"]},
         body="[[bad\x1b target]] and [[ok-one]]\n")
    return w


def test_finding_lines_neutralize_vault_record_and_malformed_targets(evil):
    r = evil.run()
    assert r.returncode == 0, r.stderr
    assert "\x1b" not in r.stdout and "\r" not in r.stdout
    lines = finding_lines(r.stdout)
    assert len(lines) == 3  # dangling related, malformed wikilink, dangling [[ok-one]]
    for ln in lines:
        assert ln.startswith("  ev\\nil\\x1b[31mv/lesson/rec\\nforged\\x1b[2Jname  ")
    assert any("malformed reference" in ln and "bad" not in ln.split("wikilink")[1] for ln in lines)


def test_summary_rows_and_skip_reasons_neutralize_vault_names(evil):
    os.chmod(evil.roots[EVIL_VAULT], 0)
    try:
        r = evil.run()
    finally:
        os.chmod(evil.roots[EVIL_VAULT], 0o755)
    assert r.returncode == 0, r.stderr
    assert "\x1b" not in r.stdout
    got = rows(r.stdout)
    assert sorted(got) == ["default", "ev\\nil\\x1b[31mv"]
    assert "ev\\nil\\x1b[31mv" in got["default"][2]


def test_stderr_refusal_neutralizes_the_vault_echo_and_configured_list(evil):
    r = evil.run("--vault", "no\nsuch\x1b[1m")
    assert r.returncode == 1
    assert "\x1b" not in r.stderr
    assert len(r.stderr.splitlines()) == 1
    assert r.stderr.startswith("lore: cannot run the health report: ")
    assert "no\\nsuch\\x1b[1m" in r.stderr
    assert "ev\\nil\\x1b[31mv" in r.stderr


# --- the index comparison counts what the indexer holds ------------------------

CAVEAT = "The index count is a snapshot and can lag a running writer."


def test_oversize_record_the_index_holds_still_matches(world):
    _put(world.roots["default"], "lesson", "a")
    _put(world.roots["default"], "lesson", "big", body="x" * (1024 * 1024 + 1))
    world.index()
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert "Records read match the index (sessions included): 2 read, 2 indexed." in r.stdout
    assert "default: 1 record could not be read and was not checked." in r.stdout
    assert rows(r.stdout)["default"][0] == "1"


@pytest.mark.parametrize("missing", ["updated-at", "created-at"])
def test_record_the_indexer_cannot_insert_is_judged_but_not_counted_as_read(world, missing):
    _put(world.roots["default"], "lesson", "a")
    _put(world.roots["default"], "lesson", "b", related={"decision": ["gone"]})
    side = world.roots["default"] / "lesson" / "b.json"
    data = json.loads(side.read_text())
    del data[missing]
    side.write_text(json.dumps(data))
    world.index()
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert "Records read match the index (sessions included): 1 read, 1 indexed." in r.stdout
    assert rows(r.stdout)["default"] == ("2", "1", "no")
    assert len(finding_lines(r.stdout)) == 1


def test_snapshot_caveat_follows_a_counted_line_and_not_an_unconfirmed_one(world):
    _put(world.roots["default"], "lesson", "a")
    no_index = world.run()
    assert "no index found" in no_index.stdout
    assert CAVEAT not in no_index.stdout
    world.index()
    matched = world.run()
    assert "match the index" in matched.stdout
    assert CAVEAT in matched.stdout
    _put(world.roots["default"], "lesson", "b")
    differing = world.run()
    assert "differ from the index" in differing.stdout
    assert CAVEAT in differing.stdout


# --- a vault the scan cannot fully list skips the check -------------------------


def test_unreadable_kind_directory_in_another_vault_skips_the_check(world):
    _put(world.roots["default"], "task", "d", related={"lesson": ["target"]})
    _put(world.roots["levr"], "lesson", "target")
    kind_dir = world.roots["levr"] / "lesson"
    os.chmod(kind_dir, 0)
    try:
        r = world.run()
    finally:
        os.chmod(kind_dir, 0o755)
    assert r.returncode == 0, r.stderr
    got = rows(r.stdout)
    assert sorted(got) == ["default", "levr", "trailhead"]
    for vault, (records, findings, skipped) in got.items():
        assert (records, findings) == ("—", "—"), vault
        assert skipped.startswith("yes:"), vault
    assert got["default"][2] == "yes: vault levr could not be read, so no target can be confirmed missing"
    assert finding_lines(r.stdout) == []
    assert "no findings" not in r.stdout


def test_missing_vault_directory_skips_the_check_and_says_so(world):
    _put(world.roots["default"], "task", "d", related={"lesson": ["target"]})
    shutil.rmtree(world.roots["levr"])
    r = world.run()
    assert r.returncode == 0, r.stderr
    got = rows(r.stdout)
    assert sorted(got) == ["default", "levr", "trailhead"]
    assert all(v[2].startswith("yes:") for v in got.values())
    assert got["levr"][2] == "yes: its directory does not exist"
    assert got["default"][2] == "yes: vault levr could not be read, so no target can be confirmed missing"
    assert finding_lines(r.stdout) == []


def test_escaping_record_under_a_nested_name_does_not_skip_the_check(world, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "n.md").write_text("x")
    nested = world.roots["trailhead"] / "lesson" / "sub"
    nested.mkdir(parents=True)
    (nested / "esc.md").symlink_to(outside / "n.md")
    (nested / "esc.json").write_text("{}")
    _put(world.roots["default"], "lesson", "a", related={"decision": ["gone"]})
    r = world.run()
    assert r.returncode == 0, r.stderr
    assert "outside the vault root" not in r.stdout
    assert rows(r.stdout)["default"] == ("1", "1", "no")
    assert rows(r.stdout)["trailhead"][2] == "no"
