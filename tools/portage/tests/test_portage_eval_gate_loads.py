"""The eval gate's load reader: which files a declared case depends on, derived from the loads in its prose.

Every test runs ``loads`` against an injected in-memory reader or a real tree under ``tmp_path`` and asserts
on what came back, including the exact fault text for each refusal.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from portage.eval_gate import loads
from portage.eval_gate.loads import (
    LoadError, NotRegularFile, cached, derive, is_branch, loads_in, missing_loads, tree_reader,
)
from portage.eval_gate.mapping import MAX_PROCEDURE_FILES

REPO_TOOLS = Path(__file__).resolve().parents[2]

ENTRY = "skills/x/SKILL.md"
B1 = "skills/x/b1.md"
B2 = "skills/x/b2.md"
R = "skills/_shared/r.md"
R2 = "skills/_shared/r2.md"
BRANCH_HEAD = "Applies when the run is unattended.\n\n"

ENTRY_TEXT = (
    "Read `../_shared/r.md` now and follow it in full.\n\n"
    "Read `b1.md` when the run is unattended. Read `b2.md` when the run is attended.\n"
)


def reader(files: dict[str, str]):
    def read(path: str):
        return files.get(path)
    return read


def counting(files: dict[str, str]):
    seen: list[str] = []

    def read(path: str):
        seen.append(path)
        return files.get(path)
    return read, seen


def corpus(**overrides: str) -> dict[str, str]:
    files = {
        ENTRY: ENTRY_TEXT,
        R: "Rule text.\n",
        B1: BRANCH_HEAD + "Read `../_shared/r2.md` now.\n",
        B2: BRANCH_HEAD + "Branch two.\n",
        R2: "Second rule.\n",
    }
    files.update(overrides)
    return files


class TestDerive:
    def test_a_declared_branch_adds_its_rule_files_and_not_the_other_branch(self):
        got = derive(ENTRY, [B1], reader(corpus()))
        assert set(got.files) == {ENTRY, B1, R, R2}
        assert got.split is True

    def test_declaring_the_second_branch_too_adds_it(self):
        got = derive(ENTRY, [B1, B2], reader(corpus()))
        assert set(got.files) == {ENTRY, B1, B2, R, R2}

    def test_with_no_branch_loads_split_is_false_and_the_files_are_the_entry_and_its_rules(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now.\n"})
        got = derive(ENTRY, [], reader(files))
        assert got.split is False
        assert set(got.files) == {ENTRY, R}

    def test_a_rule_file_that_loads_a_rule_file_adds_both(self):
        files = corpus(**{R: "Read `r2.md` now.\n"})
        got = derive(ENTRY, [], reader(files))
        assert {R, R2} <= set(got.files)

    def test_a_cycle_between_two_rule_files_terminates_with_each_once(self):
        files = corpus(**{R: "Read `r2.md` now.\n", R2: "Read `r.md` now.\n"})
        got = derive(ENTRY, [], reader(files))
        assert sorted(got.files) == sorted({ENTRY, R, R2})

    def test_the_entry_is_first_and_the_declared_branches_follow_in_order(self):
        got = derive(ENTRY, [B2, B1], reader(corpus()))
        assert got.files[:3] == (ENTRY, B2, B1)

    def test_a_backticked_md_path_in_a_sentence_without_read_is_in_the_closure_when_it_exists(self):
        files = corpus(**{ENTRY: "The notes live in `../_shared/r.md`, which matter.\n"})
        got = derive(ENTRY, [], reader(files))
        assert set(got.files) == {ENTRY, R}
        assert got.split is False

    def test_a_backticked_md_path_naming_a_missing_file_is_neither_a_load_nor_a_fault(self):
        files = {ENTRY: "The notes live in `../_shared/gone.md`.\n"}
        assert derive(ENTRY, [], reader(files)).files == (ENTRY,)
        assert missing_loads([ENTRY], reader(files)) == []

    def test_a_non_read_mention_of_an_undeclared_branch_is_not_in_the_closure(self):
        files = corpus(**{ENTRY: "Branch notes: `b2.md` is described elsewhere.\n"})
        assert B2 not in derive(ENTRY, [], reader(files)).files

    def test_a_load_wrapped_mid_sentence_across_lines_puts_every_path_in_the_closure(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` in full, together with\n`../_shared/r2.md` now.\n"})
        assert {R, R2} <= set(derive(ENTRY, [], reader(files)).files)

    def test_an_eg_inside_the_sentence_still_puts_every_path_in_the_closure(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` (e.g. the part on `../_shared/r2.md`) now.\n"})
        assert {R, R2} <= set(derive(ENTRY, [], reader(files)).files)

    def test_a_sentence_after_a_load_naming_a_path_adds_it_to_the_closure_but_is_not_a_load(self):
        text = "Read `../_shared/r.md` now. Also see `../_shared/r2.md`.\n"
        assert loads_in(ENTRY, text, reader(corpus())) == [R]
        got = derive(ENTRY, [], reader(corpus(**{ENTRY: text})))
        assert R2 in got.files

    def test_a_missing_entry_is_a_fault(self):
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], reader({}))
        assert str(e.value) == f"entry '{ENTRY}' does not exist"


class TestFaults:
    def test_a_declared_branch_the_entry_never_loads_names_the_entry_and_the_branch(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now.\n"})
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [B1], reader(files))
        assert str(e.value) == f"entry '{ENTRY}' does not load the declared branch '{B1}'"

    def test_a_declared_file_that_is_not_a_branch_file_is_refused(self):
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [R], reader(corpus()))
        assert str(e.value) == f"declared branch '{R}' does not open with 'Applies when'"

    def test_a_declared_branch_that_does_not_exist_is_refused(self):
        files = corpus()
        del files[B2]
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [B2], reader(files))
        assert str(e.value) == f"declared branch '{B2}' does not exist"

    def test_a_branch_that_loads_a_branch_faults_and_terminates(self):
        files = corpus(**{B1: BRANCH_HEAD + "Read `b2.md` now.\n"})
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [B1], reader(files))
        assert str(e.value) == f"branch '{B1}' loads the branch '{B2}'"

    def test_a_load_resolving_outside_the_plugin_root_faults(self):
        files = corpus(**{R: "Read `../../../x.md` now.\n"})
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], reader(files))
        assert str(e.value) == f"'{R}' loads '../../../x.md', which is outside the plugin root"

    def test_loads_in_refuses_an_escaping_load_directly(self):
        with pytest.raises(LoadError) as e:
            loads_in("skills/a.md", "Read `../../x.md` now.\n", reader({}))
        assert "outside the plugin root" in str(e.value)

    def test_a_load_path_with_a_character_outside_the_allowlist_faults_without_echoing_it(self):
        with pytest.raises(LoadError) as e:
            loads_in(ENTRY, "Read `a b$.md` now.\n", reader({}))
        assert str(e.value) == f"'{ENTRY}' load #1 has characters outside [A-Za-z0-9._/-]"

    def test_a_reader_that_raises_is_a_fault_not_an_empty_closure(self):
        def read(path):
            if path == R:
                raise OSError("boom")
            return corpus().get(path)

        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], read)
        assert str(e.value) == f"'{R}' could not be read"

    def test_a_reader_that_reports_a_non_regular_file_faults(self):
        def read(path):
            if path == R:
                raise NotRegularFile(path)
            return corpus().get(path)

        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], read)
        assert str(e.value) == f"'{R}' is a symlink or not a regular file"

    def test_a_real_symlinked_file_faults_through_the_tree_reader(self, tmp_path):
        root = tmp_path / "plugin"
        (root / "skills/x").mkdir(parents=True)
        (root / "skills/_shared").mkdir()
        (tmp_path / "outside.md").write_text("secret\n")
        (root / ENTRY).write_text("Read `../_shared/r.md` now.\n")
        (root / R).symlink_to(tmp_path / "outside.md")
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], tree_reader(root))
        assert str(e.value) == f"'{R}' is a symlink or not a regular file"

    def test_a_directory_is_not_a_regular_file(self, tmp_path):
        root = tmp_path / "plugin"
        (root / "skills/x").mkdir(parents=True)
        (root / "skills/_shared/r.md").mkdir(parents=True)
        (root / ENTRY).write_text("Read `../_shared/r.md` now.\n")
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], tree_reader(root))
        assert "is a symlink or not a regular file" in str(e.value)

    def test_the_tree_reader_reads_a_regular_file_and_reports_a_missing_one_as_none(self, tmp_path):
        (tmp_path / "a.md").write_text("hello\n")
        read = tree_reader(tmp_path)
        assert read("a.md") == "hello\n"
        assert read("nope.md") is None


class TestClosureCap:
    @staticmethod
    def _fan(n: int) -> dict[str, str]:
        mentions = " ".join(f"`rule{i}.md`" for i in range(n))
        files = {ENTRY: f"Read `rule0.md` and {mentions}.\n"}
        files.update({f"skills/x/rule{i}.md": "r\n" for i in range(n)})
        return files

    def test_a_closure_of_exactly_the_cap_is_allowed(self):
        got = derive(ENTRY, [], reader(self._fan(MAX_PROCEDURE_FILES - 1)))
        assert len(got.files) == MAX_PROCEDURE_FILES

    def test_a_closure_one_over_the_cap_faults(self):
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], reader(self._fan(MAX_PROCEDURE_FILES)))
        assert str(e.value) == f"the closure of '{ENTRY}' exceeds {MAX_PROCEDURE_FILES} files"

    def test_the_cap_counts_the_entry_and_the_declared_branches(self):
        files = self._fan(MAX_PROCEDURE_FILES - 2)
        files[ENTRY] = files[ENTRY].rstrip("\n") + " Read `b1.md` now.\n"
        files[B1] = BRANCH_HEAD
        assert len(derive(ENTRY, [B1], reader(files)).files) == MAX_PROCEDURE_FILES
        files["skills/x/extra.md"] = "e\n"
        files[ENTRY] = files[ENTRY].rstrip("\n") + " See `extra.md`.\n"
        with pytest.raises(LoadError):
            derive(ENTRY, [B1], reader(files))


class TestLoadsIn:
    @pytest.mark.parametrize("text, expected", [
        ("Read `../_shared/a.md` now and follow it in full.\n", ["a"]),
        ("Read `../_shared/a.md` and `../_shared/b.md` now.\n", ["a", "b"]),
        ("Read `../_shared/a.md` in full, together with `../_shared/b.md`, `../_shared/c.md`\n"
         "and `../_shared/d.md`.\n", ["a", "b", "c", "d"]),
        ("**Read `../_shared/a.md` now**\n", ["a"]),
        ("Read `../_shared/a.md` now. Then see `../_shared/b.md`.\n", ["a"]),
        ("Read `../_shared/a.md` now; `../_shared/b.md` is for later.\n", ["a"]),
        ("Read `review_bot_login` and `external_tracker` from the `[release]` block of the group TOML.\n", []),
        ("Read `manifest.json` at the derived root.\n", []),
        ("The file `../_shared/a.md` explains it.\n", []),
        ("Intro.\n\nRead `../_shared/a.md` now.\n\nBoth `../_shared/b.md` matter.\n", ["a"]),
    ])
    def test_each_form_yields_exactly_its_loads(self, text, expected):
        assert loads_in("skills/x/SKILL.md", text, reader({})) == [f"skills/_shared/{n}.md" for n in expected]

    def test_a_load_resolves_against_the_loading_files_directory(self):
        assert loads_in("agents/m.md", "Read `../skills/_shared/a.md` now.\n", reader({})) == ["skills/_shared/a.md"]

    def test_an_eg_does_not_end_the_sentence_so_the_path_after_it_is_still_a_load(self):
        text = "Read `../_shared/a.md` (e.g. the part on `../_shared/b.md`) now.\n"
        assert loads_in("skills/x/SKILL.md", text, reader({})) == ["skills/_shared/a.md", "skills/_shared/b.md"]

    def test_a_read_inside_a_code_fence_is_not_a_load(self):
        assert loads_in(ENTRY, "```\nRead `a.md` now.\n```\n", reader({})) == []


class TestResolution:
    G = "skills/g/SKILL.md"
    TEXT = "Read `g/a.md` now.\n"

    def test_an_ancestor_directory_holds_it_when_the_loading_directory_does_not(self):
        read = reader({self.G: self.TEXT, "skills/g/a.md": "a\n"})
        assert loads_in(self.G, self.TEXT, read) == ["skills/g/a.md"]
        assert missing_loads([self.G], read) == []

    def test_the_loading_directory_wins_when_both_exist(self):
        read = reader({self.G: self.TEXT, "skills/g/a.md": "a\n", "skills/g/g/a.md": "b\n"})
        assert loads_in(self.G, self.TEXT, read) == ["skills/g/g/a.md"]

    def test_a_path_found_nowhere_is_reported_in_its_loading_directory_form(self):
        read = reader({self.G: self.TEXT})
        assert missing_loads([self.G], read) == [(self.G, "skills/g/g/a.md")]

    def test_the_walk_reaches_the_plugin_root(self):
        read = reader({self.G: "Read `top/a.md` now.\n", "top/a.md": "a\n"})
        assert loads_in(self.G, "Read `top/a.md` now.\n", read) == ["top/a.md"]

    def test_the_walk_never_resolves_outside_the_plugin_root(self):
        read = reader({self.G: "Read `../../../x.md` now.\n", "x.md": "inside\n"})
        with pytest.raises(LoadError) as e:
            loads_in(self.G, "Read `../../../x.md` now.\n", read)
        assert str(e.value) == f"'{self.G}' loads '../../../x.md', which is outside the plugin root"

    def test_an_ancestor_form_that_leaves_the_root_is_skipped_even_when_the_reader_has_it(self):
        read = reader({self.G: "Read `../a.md` now.\n", "../a.md": "outside\n"})
        assert loads_in(self.G, "Read `../a.md` now.\n", read) == ["skills/a.md"]

    def test_the_closure_resolves_a_non_load_mention_by_the_same_rule(self):
        files = {ENTRY: "See `x/a.md`.\n", "skills/x/a.md": "a\n"}
        assert "skills/x/a.md" in derive(ENTRY, [], reader(files)).files

    def test_an_ancestor_resolved_load_reaches_the_closure(self):
        files = {self.G: self.TEXT, "skills/g/a.md": "a\n"}
        assert set(derive(self.G, [], reader(files)).files) == {self.G, "skills/g/a.md"}


class TestIsBranch:
    @pytest.mark.parametrize("text, expected", [
        ("Applies when the run is unattended.\n", True),
        ("**Applies only when** the run is unattended.\n", True),
        ("\n\n_Applies when_ x.\n", True),
        ("applies WHEN lower case.\n", True),
        ("# Heading\n\nApplies when the run is unattended.\n", False),
        ("Rules for all runs. Applies when nothing else.\n", False),
        ("First line.\nApplies when the run is unattended.\n", False),
        ("Applicable when the run is unattended.\n", False),
        ("", False),
    ])
    def test_opening_line_decides(self, text, expected):
        assert is_branch(text) is expected


class TestMissingLoads:
    def test_a_file_loading_a_missing_file_returns_the_pair(self):
        files = {ENTRY: "Read `../_shared/gone.md` now.\n"}
        assert missing_loads([ENTRY], reader(files)) == [(ENTRY, "skills/_shared/gone.md")]

    def test_files_whose_loads_all_exist_return_nothing(self):
        assert missing_loads([ENTRY, B1], reader(corpus())) == []

    def test_a_load_outside_the_plugin_root_raises(self):
        with pytest.raises(LoadError) as e:
            missing_loads([ENTRY], reader({ENTRY: "Read `../../../x.md` now.\n"}))
        assert "outside the plugin root" in str(e.value)

    def test_only_loads_count_not_other_missing_mentions(self):
        files = {ENTRY: "See `gone.md`.\n\nRead `r.md` now.\n", "skills/x/r.md": "r\n"}
        assert missing_loads([ENTRY], reader(files)) == []


class TestCachedReader:
    def test_a_file_is_read_once_across_cases(self):
        read, seen = counting(corpus())
        shared = cached(read)
        derive(ENTRY, [B1], shared)
        derive(ENTRY, [B1, B2], shared)
        assert len(seen) == len(set(seen))

    def test_a_missing_file_and_a_fault_are_remembered_too(self):
        calls = []

        def read(path):
            calls.append(path)
            if path == "boom.md":
                raise OSError("x")
            return None

        shared = cached(read)
        assert shared("gone.md") is None and shared("gone.md") is None
        for _ in range(2):
            with pytest.raises(OSError):
                shared("boom.md")
        assert calls == ["gone.md", "boom.md"]


class TestParseOncePerRevision:
    @staticmethod
    def _parse_counter(monkeypatch):
        calls: list[str] = []
        real = getattr(loads, "_parse", lambda text: None)

        def counting_parse(text):
            calls.append(text)
            return real(text)

        monkeypatch.setattr(loads, "_parse", counting_parse, raising=False)
        return calls

    def test_two_cases_sharing_a_rule_file_parse_it_once_per_revision_cache(self, monkeypatch):
        calls = self._parse_counter(monkeypatch)
        files = corpus()
        shared = cached(reader(files))
        derive(ENTRY, [B1], shared)
        derive(ENTRY, [B1, B2], shared)
        missing_loads(list(files), shared)
        assert len(calls) == len(set(calls)) == len(set(files.values()))

    def test_without_a_cache_wrapper_each_call_still_parses_and_answers(self):
        assert derive(ENTRY, [B1], reader(corpus())).files == derive(ENTRY, [B1], cached(reader(corpus()))).files

    def test_separate_revision_caches_do_not_share_parses_for_the_same_path(self):
        base = {ENTRY: "Read `old.md` now.\n", "skills/x/old.md": "o\n", "skills/x/new.md": "n\n"}
        head = {ENTRY: "Read `new.md` now.\n", "skills/x/old.md": "o\n", "skills/x/new.md": "n\n"}
        base_read, head_read = cached(reader(base)), cached(reader(head))
        assert derive(ENTRY, [], base_read).files == (ENTRY, "skills/x/old.md")
        assert derive(ENTRY, [], head_read).files == (ENTRY, "skills/x/new.md")
        assert loads_in(ENTRY, base[ENTRY], base_read) == ["skills/x/old.md"]
        assert loads_in(ENTRY, head[ENTRY], head_read) == ["skills/x/new.md"]
        assert missing_loads([ENTRY], head_read) == []


class TestLinearSentences:
    def test_twenty_thousand_abbreviations_in_one_sentence_are_split_in_linear_time(self):
        text = "Read `r.md` now, " + "go e.g. " * 20000 + "then read `late.md`. Read `last.md` now.\n"
        files = {ENTRY: text, "skills/x/r.md": "r\n", "skills/x/late.md": "l\n", "skills/x/last.md": "l\n"}
        started = time.perf_counter()
        got = loads_in(ENTRY, text, reader(files))
        elapsed = time.perf_counter() - started
        assert got == ["skills/x/r.md", "skills/x/late.md", "skills/x/last.md"]
        assert elapsed < 1.0

    def test_e_g_and_i_e_do_not_end_a_sentence_but_a_word_ending_in_them_does(self):
        text = "Read `a.md` e.g. now and (i.e. later) then `b.md`. Read `c.md` once. Mre.g. x. Read `d.md` too.\n"
        files = {ENTRY: text, **{f"skills/x/{n}.md": "x\n" for n in "abcd"}}
        assert loads_in(ENTRY, text, reader(files)) == [f"skills/x/{n}.md" for n in "abc"] + ["skills/x/d.md"]


PLUGINS = ("craft", "lore", "portage", "outpost")


def _procedures(root: Path) -> list[str]:
    found = {p.relative_to(root).as_posix() for p in (root / "skills").glob("**/*.md")}
    found |= {p.relative_to(root).as_posix() for p in (root / "agents").glob("*.md")}
    return sorted(found)


class TestLiveCorpus:
    def test_every_procedure_in_the_four_plugins_parses_loads_nothing_missing_and_is_no_branch(self):
        total = 0
        for name in PLUGINS:
            root = REPO_TOOLS / name / "plugins" / name
            read = cached(tree_reader(root))
            files = _procedures(root)
            total += len(files)
            assert files, name
            assert missing_loads(files, read) == [], name
            assert [f for f in files if is_branch(read(f))] == [], name
        assert total > 20

    def test_craft_skill_cases_and_every_procedure_derive_and_check_in_under_a_second(self):
        started = time.perf_counter()
        craft = REPO_TOOLS / "craft" / "plugins" / "craft"
        read = cached(tree_reader(craft))
        cases = json.loads((craft / "evals" / "eval-gate.json").read_text(encoding="utf-8"))["cases"]
        derived = 0
        for case in cases.values():
            entry = next((m for m in case["measures"] if m.startswith("skills/") and m.endswith("/SKILL.md")), None)
            if entry is not None:
                derive(entry, (), read)
                derived += 1
        for name in PLUGINS:
            root = REPO_TOOLS / name / "plugins" / name
            assert missing_loads(_procedures(root), cached(tree_reader(root))) == [], name
        elapsed = time.perf_counter() - started
        assert derived > 20
        assert elapsed < 1.0, f"{elapsed:.2f}s"
