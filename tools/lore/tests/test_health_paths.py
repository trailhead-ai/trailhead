"""Missing-paths check behind the health report.

Fixtures are the real ``ScannedRecord`` / ``VaultScan`` / ``Repository`` types; the
repository views are hand-built plain data (the git reads are ``repos``'s tests).
"""

from pathlib import Path

import pytest

from conftest import load_script

model = load_script("lore.health.model")
repos_mod = load_script("lore.health.repos")
paths = load_script("lore.health.paths")


def repo(*files, root="/r/one", submodules=()):
    allp = set()
    for f in files:
        parts = f.split("/")
        for i in range(1, len(parts) + 1):
            allp.add("/".join(parts[:i]))
    return repos_mod.Repository(
        Path(root), "refs/remotes/origin/main", False, frozenset(allp),
        frozenset(p for p in allp if "/" not in p), frozenset(submodules), None,
    )


def rec(body="", kind="decision", status="active", name="d1", vault="a", sidecar=None):
    sc = dict(sidecar or {})
    if status is not None:
        sc["status"] = status
    return model.ScannedRecord(vault, kind, name, "2026-01-01T00:00:00+00:00", sc, body)


def vault(name, *records, **kw):
    s = model.VaultScan(name, f"/nowhere/{name}", **kw)
    s.records.extend(records)
    return s


def run(records, *repositories, selected=("a",), vault_repos=None):
    repos = vault_repos if vault_repos is not None else {"a": tuple(repositories)}
    return paths.check_missing_paths([vault("a", *records)], selected, repos)


TREE = ("tools/lore/scan.py", "tools/lore/test_x.py", "tools/keep.py")


def missing(result):
    return [f.path for f in result.findings]


def test_missing_file_reported_present_not():
    gone = run([rec("see `tools/gone.py` here")], repo(*TREE))
    assert missing(gone) == ["tools/gone.py"]
    present = run([rec("see `tools/keep.py` here")], repo(*TREE))
    assert present.findings == []


def test_present_in_second_repository_only_is_not_reported():
    r1 = repo(*TREE)
    r2 = repo("tools/other.py", root="/r/two")
    result = run([rec("`tools/other.py`")], r1, r2)
    assert result.findings == []


def test_absent_from_every_repository_is_reported():
    result = run([rec("`tools/zzz.py`")], repo(*TREE), repo("tools/o.py", root="/r/two"))
    assert missing(result) == ["tools/zzz.py"]


def test_path_under_submodule_is_not_reported():
    r = repo("tools/keep.py", "vendor", submodules=("vendor",))
    assert run([rec("`vendor/inner/x.py`")], r).findings == []
    assert missing(run([rec("`tools/inner/x.py`")], r)) == ["tools/inner/x.py"]


@pytest.mark.parametrize("text", ["lore health", "config.toml", "docs/x.md"])
def test_first_segment_gate(text):
    assert run([rec(f"`{text}`")], repo(*TREE)).findings == []


def test_first_segment_in_any_repository_opens_gate():
    r2 = repo("docs/a.md", root="/r/two")
    assert missing(run([rec("`docs/x.md`")], repo(*TREE), r2)) == ["docs/x.md"]


@pytest.mark.parametrize("text", [
    "~/x", "/etc/x", "$HOME/x", "%APPDATA%/x", "https://x/y", "tools/a://b",
    "tools/*.py", "tools/{a,b}", "tools/a?.py", "tools/[a].py", "mailto:tools/x",
    "tools/a b.py", "tools/" + "x" * 300, "tools/<name>/", "tools/_shared/<doc>.md", "tools/a>b.py",
])
def test_exclusion_never_reported(text):
    assert run([rec(f"`{text}`")], repo(*TREE)).findings == []


ODD_TOPS = ("~/gone", "$HOME/gone", "%APPDATA%/gone", "mailto:tools/gone", "tools/keep.py")


@pytest.mark.parametrize("text", ["~/x", "$HOME/x", "%APPDATA%/x", "mailto:tools/x"])
def test_prefix_exclusion_holds_when_first_segment_is_a_top_level_entry(text):
    odd = repo("~/gone", "$HOME/gone", "%APPDATA%/gone", "mailto:tools/gone", "tools/keep.py")
    assert run([rec(f"`{text}`")], odd).findings == []
    assert missing(run([rec("`tools/x`")], odd)) == ["tools/x"]


def test_exclusion_over_300_by_one():
    assert run([rec("`" + "tools/" + "x" * 295 + "`")], repo(*TREE)).findings == []


def test_exclusion_control_character():
    assert run([rec("`tools/a\x01b.py`")], repo(*TREE)).findings == []


def test_boundary_300_characters_is_a_candidate():
    p = "tools/" + "x" * 294
    assert len(p) == 300
    assert missing(run([rec(f"`{p}`")], repo(*TREE))) == [p]


@pytest.mark.parametrize("written, bare", [
    ("tools/lore/scan.py:198", "tools/lore/scan.py"),
    ("tools/lore/scan.py:198:7", "tools/lore/scan.py"),
    ("tools/lore/scan.py#L40-L52", "tools/lore/scan.py"),
    ("tools/lore/scan.py:42-50", "tools/lore/scan.py"),
    ("tools/lore/scan.py:17,35", "tools/lore/scan.py"),
    ("tools/lore/scan.py:9-12,40", "tools/lore/scan.py"),
    ("tools/lore/scan.py#scan_vault", "tools/lore/scan.py"),
    ("tools/lore/test_x.py::TestA::test_b", "tools/lore/test_x.py"),
    ("tools/lore/", "tools/lore"),
])
def test_suffix_stripped_resolves_present(written, bare):
    assert run([rec(f"`{written}`")], repo(*TREE)).findings == []


@pytest.mark.parametrize("written, bare", [
    ("tools/lore/gone.py:198", "tools/lore/gone.py"),
    ("tools/lore/gone.py:198:7", "tools/lore/gone.py"),
    ("tools/lore/gone.py#L40-L52", "tools/lore/gone.py"),
    ("tools/lore/gone.py:42-50", "tools/lore/gone.py"),
    ("tools/lore/gone.py:17,35", "tools/lore/gone.py"),
    ("tools/lore/gone.py#gone_symbol", "tools/lore/gone.py"),
    ("tools/lore/gone.py::TestA::test_b", "tools/lore/gone.py"),
    ("tools/gonedir/", "tools/gonedir"),
])
def test_suffix_stripped_missing_prints_as_written(written, bare):
    result = run([rec(f"`{written}`")], repo(*TREE))
    assert missing(result) == [written]


def test_fenced_block_is_not_candidate_next_line_is():
    body = "intro\n```\n`tools/gone.py`\n```\n`tools/gone.py`\n"
    result = run([rec(body)], repo(*TREE))
    assert [(f.location, f.number) for f in result.findings] == [("body line", 5)]


def test_tilde_fence_and_longer_fence():
    body = "~~~\n`tools/a.py`\n~~~\n````\n```\n`tools/b.py`\n```\n````\n`tools/c.py`"
    assert missing(run([rec(body)], repo(*TREE))) == ["tools/c.py"]


def test_double_backtick_span_is_not_candidate():
    assert run([rec("``tools/gone.py``")], repo(*TREE)).findings == []


def test_related_file_reported_with_position_and_non_string_skipped():
    sc = {"related-files-or-folders": [7, "tools/keep.py", None, "tools/gone.py"]}
    result = run([rec("", sidecar=sc)], repo(*TREE))
    assert [(f.location, f.number, f.path) for f in result.findings] == [
        ("related file", 4, "tools/gone.py")]


@pytest.mark.parametrize("value", ["tools/gone.py", {"tools/gone.py": 1}, ("tools/gone.py",), 5, None])
def test_related_files_not_a_list_ignored(value):
    assert run([rec("", sidecar={"related-files-or-folders": value})], repo(*TREE)).findings == []


@pytest.mark.parametrize("kind, status", [
    ("decision", "superseded"), ("spec", "ready"), ("task", "open"),
    ("session", "active"), ("adr", "dropped"), ("area", "draft"),
])
def test_only_live_decision_adr_area_judged(kind, status):
    result = run([rec("`tools/gone.py`", kind=kind, status=status)], repo(*TREE))
    assert result.findings == []
    assert result.judged == {"a": 0}


@pytest.mark.parametrize("kind", ["decision", "adr", "area"])
def test_live_kinds_judged_and_counted(kind):
    result = run([rec("`tools/gone.py`", kind=kind), rec("x", kind="task", status="open", name="t")],
                 repo(*TREE))
    assert missing(result) == ["tools/gone.py"]
    assert result.judged == {"a": 1}


def test_same_path_twice_one_finding_at_first_location():
    body = "x\n`tools/gone.py` and `tools/gone.py:3`\n`tools/gone.py`"
    sc = {"related-files-or-folders": ["tools/gone.py"]}
    result = run([rec(body, sidecar=sc)], repo(*TREE))
    assert [(f.location, f.number, f.path) for f in result.findings] == [
        ("body line", 2, "tools/gone.py")]


def test_judged_counts_records_without_findings():
    result = run([rec("`tools/keep.py`"), rec("`tools/gone.py`", name="d2"), rec("prose", name="d3")],
                 repo(*TREE))
    assert result.judged == {"a": 3}
    assert [f.name for f in result.findings] == ["d2"]


def test_distinct_paths_each_reported():
    result = run([rec("`tools/a.py` `tools/b.py`")], repo(*TREE))
    assert missing(result) == ["tools/a.py", "tools/b.py"]


def test_finding_carries_record_identity():
    r = rec("`tools/gone.py`", kind="adr", name="n1")
    f = run([r], repo(*TREE)).findings[0]
    assert (f.vault, f.kind, f.name, f.updated_at) == ("a", "adr", "n1", r.updated_at)


def test_unbound_vault_skipped_other_vault_judged():
    scans = [vault("a", rec("`tools/gone.py`")), vault("b", rec("`tools/gone.py`", vault="b"))]
    result = paths.check_missing_paths(
        scans, ["a", "b"], {"a": repos_mod.UNBOUND, "b": (repo(*TREE),)})
    assert result.skipped == {"a": paths.VaultSkip(repos_mod.UNBOUND, None)}
    assert result.judged == {"b": 1}
    assert [f.vault for f in result.findings] == ["b"]


@pytest.mark.parametrize("view", [(), None])
def test_vault_with_no_repositories_is_unbound(view):
    scan = vault("a", rec("`tools/gone.py`"))
    vr = {} if view is None else {"a": view}
    result = paths.check_missing_paths([scan], ["a"], vr)
    assert result.skipped == {"a": paths.VaultSkip(repos_mod.UNBOUND, None)}
    assert result.findings == [] and result.judged == {}


def test_unreadable_repository_skips_vault_naming_it():
    # built from paths' own binding: other test files reload repos, which would stale a class bound at import
    bad = paths.Unreadable(Path("/r/bad"), repos_mod.MISSING)
    result = run([rec("`tools/gone.py`")], repo(*TREE), bad)
    assert result.skipped == {"a": paths.VaultSkip(repos_mod.MISSING, Path("/r/bad"))}
    assert result.findings == [] and result.judged == {}


def test_vault_not_fully_read_skipped():
    scan = vault("a", rec("`tools/gone.py`"), skipped_escaping=1)
    result = paths.check_missing_paths([scan], ["a"], {"a": (repo(*TREE),)})
    assert result.skipped == {"a": paths.VaultSkip(paths.NOT_FULLY_READ, None)}
    assert result.findings == [] and result.judged == {}


def test_unselected_vault_not_judged():
    result = run([rec("`tools/gone.py`")], repo(*TREE), selected=())
    assert result.findings == [] and result.judged == {} and result.skipped == {}


PACKAGED = ("src/index.ts", "services/auth/src/step-up.ts", "services/auth/test/step-up.test.ts",
            "services/auth/mysrc/lib.ts", "apps/web/src/worker.ts")


@pytest.mark.parametrize("written", ["src/step-up.ts", "test/step-up.test.ts", "src/worker.ts",
                                     "services/auth/src/step-up.ts", "auth/src/step-up.ts"])
def test_a_path_written_relative_to_a_package_resolves_when_it_is_the_tail_of_a_real_path(written):
    assert run([rec(f"`{written}`")], repo(*PACKAGED, "test/other.test.ts")).findings == []


@pytest.mark.parametrize("written", ["src/gone.ts", "src/tep-up.ts", "src/lib.ts", "test/gone.test.ts"])
def test_a_relative_path_that_is_no_tail_of_any_real_path_is_still_reported(written):
    assert missing(run([rec(f"`{written}`")], repo(*PACKAGED, "test/other.test.ts"))) == [written]


def test_a_tail_must_start_at_a_directory_boundary():
    assert missing(run([rec("`src/lib.ts`")], repo(*PACKAGED))) == ["src/lib.ts"]


def test_a_tail_in_another_repository_of_the_vault_resolves():
    r2 = repo("services/auth/src/step-up.ts", root="/r/two")
    assert run([rec("`src/step-up.ts`")], repo("src/index.ts"), r2).findings == []
