"""``lore validate`` end to end: ``run_cli`` in this process against fixture vaults.

Every vault, config and state dir lives under ``tmp_path``; the per-call
``XDG_CONFIG_HOME`` keeps the live install out of reach. Failures are caused on
the real disk (chmod, symlinks, a file where a directory is expected).
"""

import importlib
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import CLI_PATH, _cli_env, run_cli, write_vault_config

SUMMARY_RE = re.compile(r"^lore: validated (\d+) records in (\d+) vaults: (\d+) findings")


def _put(root, kind, name, body="text\n", status=None, **extra):
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    side = {
        "kind": kind,
        "title": name,
        "status": status or ("dirty" if kind == "session" else "active"),
        "created-at": "2026-01-01T00:00:00Z",
        "updated-at": "2026-03-01T00:00:00Z",
        **extra,
    }
    (d / f"{name}.json").write_text(json.dumps(side))
    (d / f"{name}.md").write_text(body)


class World:
    """Named configured vaults under ``tmp_path`` plus a place to run the CLI."""

    def __init__(self, tmp_path, names=("default",), scopes=None):
        self.tmp_path = tmp_path
        self.state = tmp_path / "state"
        self.cfg = tmp_path / "config"
        (self.state / "lore" / "vaults").mkdir(parents=True)
        self.roots = {}
        triples = []
        for n in names:
            root = tmp_path / "vaults" / n
            root.mkdir(parents=True)
            self.roots[n] = root
            scope = (scopes or {}).get(n, "default" if n == "default" else "team")
            triples.append((n, scope, root))
        write_vault_config(self.cfg, triples)

    def run(self, *args):
        return run_cli(
            ["validate", *args],
            vault=next(iter(self.roots.values())),
            state_dir=self.state,
            env_extra={"XDG_CONFIG_HOME": str(self.cfg)},
        )


def lines(r):
    return r.stdout.splitlines()


def summary(r):
    m = SUMMARY_RE.match(r.stderr.strip().splitlines()[0])
    assert m, r.stderr
    return tuple(int(g) for g in m.groups())


def one_lore_line(r):
    err = r.stderr.splitlines()
    assert len(err) == 1 and err[0].startswith("lore: "), r.stderr
    assert "Traceback" not in r.stderr
    assert r.stdout == ""


def snapshot(folder):
    out = {}
    for p in sorted(folder.rglob("*")):
        out[str(p.relative_to(folder))] = (
            p.read_bytes() if p.is_file() and not p.is_symlink() else None
        )
    return out


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture
def two(tmp_path):
    return World(tmp_path, names=("default", "other"))


# --- basic results (AC21, AC22, AC23, AC24) ---------------------------------


def test_clean_vault_prints_nothing_and_exits_zero(world):
    _put(world.roots["default"], "lesson", "good")
    r = world.run()
    assert (r.returncode, r.stdout) == (0, "")
    assert summary(r) == (1, 1, 0)


def test_bad_status_is_one_invalid_sidecar_line_naming_the_record(world):
    _put(world.roots["default"], "lesson", "bad", status="nonsense")
    r = world.run()
    assert r.returncode == 1
    out = lines(r)
    assert len(out) == 1
    assert re.fullmatch(r"default lesson/bad: invalid-sidecar: .*nonsense.*", out[0])
    assert summary(r) == (1, 1, 1)
    assert "invalid-sidecar" in r.stderr.splitlines()[0]


def test_unparseable_sidecar_is_one_line_naming_the_record(world):
    _put(world.roots["default"], "lesson", "torn")
    (world.roots["default"] / "lesson" / "torn.json").write_text("{not json")
    r = world.run()
    assert r.returncode == 1
    assert [l.split(":")[0] + ":" + l.split(":")[1] for l in lines(r)] == [
        "default lesson/torn: sidecar-malformed"
    ]


def test_dangling_related_edge_is_one_missing_target_line(world):
    _put(world.roots["default"], "task", "src", status="open", related={"lesson": ["gone"]})
    r = world.run()
    assert r.returncode == 1
    assert lines(r) == ["default task/src: missing-target: related lesson → lesson/gone: no such record"]


def test_wrong_kind_wikilink_names_the_kind_it_exists_under(world):
    root = world.roots["default"]
    _put(root, "lesson", "thing")
    _put(root, "lesson", "src", body="see [[decision/thing]]\n")
    r = world.run()
    assert r.returncode == 1
    assert lines(r) == [
        "default lesson/src: wrong-kind: wikilink → decision/thing: exists only as lesson"
    ]


def test_malformed_link_is_reported_by_location_never_text(world):
    _put(world.roots["default"], "lesson", "src", body="ok\n[[Not A Name!]]\n")
    r = world.run()
    assert r.returncode == 1
    assert lines(r) == [
        "default lesson/src: not-a-record-id: wikilink → malformed reference on body line 2"
    ]
    assert "Not A Name" not in r.stdout


def test_dangling_link_in_a_session_record_is_reported(world):
    _put(world.roots["default"], "session", "s1", body="see [[gone]]\n")
    r = world.run()
    assert r.returncode == 1
    assert lines(r) == ["default session/s1: missing-target: wikilink → gone: no such record"]


# --- scoping (AC25, AC26, AC27) ---------------------------------------------


def _defect_pair(two):
    _put(two.roots["default"], "lesson", "in-default", status="nonsense")
    _put(two.roots["other"], "lesson", "in-other", status="nonsense")


def test_no_flag_checks_every_configured_vault(two):
    _defect_pair(two)
    r = two.run()
    assert r.returncode == 1
    assert sorted(l.split(":")[0] for l in lines(r)) == ["default lesson/in-default", "other lesson/in-other"]
    assert summary(r) == (2, 2, 2)


def test_named_vault_checks_only_that_vault(two):
    _defect_pair(two)
    r = two.run("--vault", "default")
    assert r.returncode == 1
    assert [l.split(":")[0] for l in lines(r)] == ["default lesson/in-default"]
    assert summary(r) == (1, 1, 1)


def test_named_vault_resolves_links_against_every_configured_vault(two):
    _put(two.roots["other"], "lesson", "only-in-other")
    _put(two.roots["default"], "lesson", "src", body="see [[lesson/only-in-other]]\n")
    r = two.run("--vault", "default")
    assert (r.returncode, r.stdout) == (0, "")


def test_path_resolves_links_only_within_the_directory(two, tmp_path):
    _put(two.roots["other"], "lesson", "only-in-other")
    d = tmp_path / "bare"
    _put(d, "lesson", "src", body="see [[lesson/only-in-other]]\n")
    r = two.run("--path", str(d))
    assert r.returncode == 1
    assert lines(r) == ["path lesson/src: missing-target: wikilink → lesson/only-in-other: no such record"]
    assert lines(r)[0].startswith("path ")


def test_path_with_the_target_inside_it_reports_nothing(two, tmp_path):
    d = tmp_path / "bare"
    _put(d, "lesson", "only-in-other")
    _put(d, "lesson", "src", body="see [[lesson/only-in-other]]\n")
    r = two.run("--path", str(d))
    assert (r.returncode, r.stdout) == (0, "")
    assert summary(r) == (2, 1, 0)


def test_path_on_a_configured_vaults_folder_still_resolves_only_within_it(two):
    _put(two.roots["other"], "lesson", "only-in-other")
    _put(two.roots["default"], "lesson", "src", body="see [[lesson/only-in-other]]\n")
    r = two.run("--path", str(two.roots["default"]))
    assert r.returncode == 1
    assert [l.split(": ")[1] for l in lines(r)] == ["missing-target"]


def test_path_reads_no_lore_config(world, tmp_path):
    d = tmp_path / "bare"
    _put(d, "lesson", "good")
    cfg = world.cfg / "lore" / "config.json"
    cfg.write_text("{ this is not json")
    cfg.chmod(0)
    try:
        r = world.run("--path", str(d))
    finally:
        cfg.chmod(0o644)
    assert (r.returncode, r.stdout) == (0, "")


def test_path_on_an_empty_directory_is_a_no_records_finding(world, tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    r = world.run("--path", str(d))
    assert r.returncode == 1
    assert [l.split(": ")[:2] for l in lines(r)] == [["path", "no-records"]]
    assert summary(r) == (0, 1, 1)


def test_path_with_records_outside_kind_directories_is_no_records(world, tmp_path):
    d = tmp_path / "misplaced"
    d.mkdir()
    (d / "stray.md").write_text("x\n")
    (d / "stray.json").write_text("{}")
    r = world.run("--path", str(d))
    assert r.returncode == 1
    assert [l.split(": ")[:2] for l in lines(r)] == [["path", "no-records"]]


def test_configured_vault_with_no_records_is_not_a_finding(world):
    r = world.run()
    assert (r.returncode, r.stdout) == (0, "")


# --- cannot run: exit 2, one lore: line -------------------------------------


def test_unknown_vault_exits_two_naming_the_configured_vaults(two):
    r = two.run("--vault", "nope")
    assert r.returncode == 2
    one_lore_line(r)
    assert "default" in r.stderr and "other" in r.stderr and "nope" in r.stderr


def test_path_naming_a_file_exits_two(world, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    r = world.run("--path", str(f))
    assert r.returncode == 2
    one_lore_line(r)


def test_both_flags_exit_two_without_scanning(world, tmp_path):
    r = world.run("--vault", "default", "--path", str(tmp_path))
    assert r.returncode == 2
    one_lore_line(r)


def test_invalid_config_exits_two(world):
    (world.cfg / "lore" / "config.json").write_text("{ nope")
    r = world.run()
    assert r.returncode == 2
    one_lore_line(r)


def test_a_scan_that_raises_exits_two_without_a_traceback(world):
    _put(world.roots["default"], "lesson", "good")
    validate = importlib.import_module("lore.cli.validate")
    saved = validate._scan_vault

    def boom(name, root):
        raise RuntimeError("scan exploded")

    validate._scan_vault = boom
    try:
        r = world.run()
    finally:
        validate._scan_vault = saved
    assert r.returncode == 2
    one_lore_line(r)
    assert "RuntimeError" in r.stderr


# --- vault-level findings ----------------------------------------------------


def test_configured_vault_whose_folder_is_missing_is_vault_missing(two):
    os.rmdir(two.roots["other"])
    _put(two.roots["default"], "lesson", "good")
    r = two.run()
    assert r.returncode == 1
    assert sorted(l.split(": ")[:2] for l in lines(r) if l.startswith("other")) == [
        ["other", "links-not-checked"],
        ["other", "vault-missing"],
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list any directory")
def test_vault_that_cannot_be_listed_is_vault_unreadable(world):
    root = world.roots["default"]
    _put(root, "lesson", "good")
    root.chmod(0)
    try:
        r = world.run()
    finally:
        root.chmod(0o755)
    assert r.returncode == 1
    assert sorted(l.split(": ")[:2] for l in lines(r)) == [
        ["default", "links-not-checked"],
        ["default", "vault-unreadable"],
    ]


def _symlink_kind_dir(root, kind, target):
    target.mkdir(exist_ok=True)
    (root / kind).symlink_to(target)


def test_symlinked_kind_directory_is_counted_once_without_a_path_when_checked(world, tmp_path):
    outside = tmp_path / "outside-secret-dir"
    _put(world.roots["default"], "lesson", "good")
    _symlink_kind_dir(world.roots["default"], "task", outside)
    r = world.run()
    assert r.returncode == 1
    assert [l for l in lines(r) if "links-not-checked" not in l] == [
        "default: records-outside-vault: 1 record lies outside the vault root and was not read"
    ]
    assert "outside-secret" not in r.stdout + r.stderr


def test_two_symlinked_kind_directories_are_counted_twice(world, tmp_path):
    root = world.roots["default"]
    _put(root, "lesson", "good")
    _symlink_kind_dir(root, "task", tmp_path / "o1")
    _symlink_kind_dir(root, "decision", tmp_path / "o2")
    r = world.run()
    assert [l.split(": ")[:3] for l in lines(r) if "records-outside-vault" in l] == [
        ["default", "records-outside-vault", "2 records lie outside the vault root and were not read"]
    ]


def test_symlinked_md_orphan_is_counted_once_without_a_path(world, tmp_path):
    outside = tmp_path / "outside-secret.md"
    outside.write_text("secret\n")
    _put(world.roots["default"], "lesson", "good")
    (world.roots["default"] / "lesson" / "ghost.md").symlink_to(outside)
    r = world.run()
    assert r.returncode == 1
    assert [l for l in lines(r) if "links-not-checked" not in l] == [
        "default: records-outside-vault: 1 record lies outside the vault root and was not read"
    ]
    assert "outside-secret" not in r.stdout + r.stderr


def test_md_orphan_symlinked_inside_the_vault_is_sidecar_missing_not_outside(world):
    root = world.roots["default"]
    _put(root, "lesson", "good")
    (root / "lesson" / "alias.md").symlink_to(root / "lesson" / "good.md")
    r = world.run()
    assert r.returncode == 1
    out = [l for l in lines(r) if "links-not-checked" not in l]
    assert len(out) == 1 and out[0].startswith("default lesson/alias: sidecar-missing: ")
    assert "records-outside-vault" not in r.stdout


def test_an_unreadable_structure_walk_is_a_vault_unreadable_finding_not_a_pass(world):
    _put(world.roots["default"], "lesson", "good")
    validate = importlib.import_module("lore.cli.validate")
    saved = validate._check_structure

    def boom(scan, root):
        raise PermissionError("no")

    validate._check_structure = boom
    try:
        r = world.run()
    finally:
        validate._check_structure = saved
    assert r.returncode == 1
    assert lines(r) == ["default: vault-unreadable: the vault directory could not be read"]


def test_symlinked_kind_directory_is_counted_with_path(world, tmp_path):
    d = tmp_path / "bare"
    _put(d, "lesson", "good")
    _symlink_kind_dir(d, "task", tmp_path / "outside-secret-dir")
    r = world.run("--path", str(d))
    assert r.returncode == 1
    assert [l for l in lines(r) if "links-not-checked" not in l] == [
        "path: records-outside-vault: 1 record lies outside the vault root and was not read"
    ]
    assert "outside-secret" not in r.stdout + r.stderr


def test_unchecked_vault_in_the_resolution_set_is_reported_and_links_not_checked(two, tmp_path):
    _put(two.roots["default"], "lesson", "good")
    _symlink_kind_dir(two.roots["other"], "task", tmp_path / "outside")
    r = two.run("--vault", "default")
    assert r.returncode == 1
    assert sorted(l.split(": ")[:2] for l in lines(r)) == [
        ["other", "links-not-checked"],
        ["other", "records-outside-vault"],
    ]
    assert not any(l.startswith("default") for l in lines(r))


def test_links_not_checked_names_the_missing_vault_and_a_has_no_record_findings(two):
    _put(two.roots["default"], "lesson", "good")
    os.rmdir(two.roots["other"])
    r = two.run("--vault", "default")
    assert r.returncode == 1
    assert sorted(l.split(": ")[:2] for l in lines(r)) == [
        ["other", "links-not-checked"],
        ["other", "vault-missing"],
    ]


def test_structural_findings_still_run_when_links_are_not_checked(two):
    _put(two.roots["default"], "lesson", "bad", status="nonsense")
    os.rmdir(two.roots["other"])
    r = two.run("--vault", "default")
    assert any(": invalid-sidecar: " in l and l.startswith("default lesson/bad") for l in lines(r))


# --- output safety and order -------------------------------------------------


def test_control_characters_in_a_validation_detail_are_escaped(world):
    root = world.roots["default"]
    _put(root, "lesson", "bad")
    side = json.loads((root / "lesson" / "bad.json").read_text())
    side["related"] = {"evil\x1b[31m\nINJECTED": ["x"]}
    (root / "lesson" / "bad.json").write_text(json.dumps(side))
    r = world.run()
    assert r.returncode == 1
    assert len(lines(r)) == 1
    assert "\x1b" not in r.stdout and "\x1b" not in r.stderr
    assert "\\x1b" in r.stdout


def test_output_is_sorted_and_byte_identical_across_runs(world):
    root = world.roots["default"]
    _put(root, "lesson", "two", status="nonsense", bogus="x", body="[[zzz]] then [[aaa]]\n")
    _put(root, "lesson", "one", status="nonsense")
    first, second = world.run(), world.run()
    assert first.stdout == second.stdout
    out = lines(first)
    assert len(out) == 5
    keys = [(l.split(": ")[0], l.split(": ")[1], l.split(": ", 2)[2]) for l in out]
    assert keys == sorted(keys)
    assert [l.split("→ ")[1].split(":")[0] for l in out if "missing-target" in l] == ["aaa", "zzz"]


# --- never changes any file (AC28) ------------------------------------------


def test_run_over_two_vaults_one_read_only_changes_nothing(tmp_path):
    w = World(tmp_path, names=("default", "ro"), scopes={"ro": "read-only"})
    for root in w.roots.values():
        _put(root, "lesson", "bad", status="nonsense", body="[[gone]]\n")
        (root / "lesson" / "lonely.md").write_text("orphan\n")
        (root / ".git").mkdir()
        (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    before = {n: snapshot(r) for n, r in w.roots.items()}
    r = w.run()
    assert r.returncode == 1
    assert {n: snapshot(rt) for n, rt in w.roots.items()} == before


def test_path_run_changes_nothing_and_leaves_no_lock_or_index(world, tmp_path):
    d = tmp_path / "bare"
    _put(d, "lesson", "bad", status="nonsense", body="[[gone]]\n")
    def state_snapshot():
        # the harness itself writes a config under _xdg_config on every run
        return {k: v for k, v in snapshot(world.state).items() if not k.startswith("_xdg_config")}

    before, state_before = snapshot(d), state_snapshot()
    r = world.run("--path", str(d))
    assert r.returncode == 1
    assert snapshot(d) == before
    assert state_snapshot() == state_before


# --- a closed stdout pipe -----------------------------------------------------


def test_closed_stdout_pipe_ends_without_a_traceback(world):
    root = world.roots["default"]
    for i in range(50):
        _put(root, "lesson", f"bad-{i}", status="nonsense")
    env = _cli_env(root, world.state, {"XDG_CONFIG_HOME": str(world.cfg)})
    p = subprocess.Popen(
        [sys.executable, str(CLI_PATH), "validate"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
    )
    p.stdout.close()
    err = p.stderr.read()
    p.wait()
    assert "Traceback" not in err and "BrokenPipe" not in err
    assert p.returncode == 1
    assert "lore: validated 50 records in 1 vaults: 50 findings (invalid-sidecar 50)" in err
