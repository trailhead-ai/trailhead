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
    LoadError, NotRegularFile, cached, derive, is_branch, missing_loads, tree_reader,
)
from portage.eval_gate.mapping import MAX_BLOB_BYTES, MAX_PROCEDURE_FILES
from portage.eval_gate.loads import parse_declared_mentions, parse_directives, read_declared_mentions

REPO_TOOLS = Path(__file__).resolve().parents[2]

ENTRY = "skills/x/SKILL.md"
B1 = "skills/x/b1.md"
B2 = "skills/x/b2.md"
R = "skills/_shared/r.md"
R2 = "skills/_shared/r2.md"
BRANCH_HEAD = "Applies when the run is unattended.\n\n"

ENTRY_TEXT = (
    "Read `../_shared/r.md` now and follow it in full.\n\n"
    "When the run is unattended, read `b1.md` now and follow it in full.\n\n"
    "When the run is attended, read `b2.md` now and follow it in full.\n"
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

    def test_a_sentence_after_a_load_naming_a_path_adds_it_to_the_closure(self):
        text = "Read `../_shared/r.md` now. Also see `../_shared/r2.md`.\n"
        got = derive(ENTRY, [], reader(corpus(**{ENTRY: text})))
        assert {R, R2} <= set(got.files)

    @pytest.mark.parametrize("text", [
        "See [the rules](../_shared/r.md#step) for detail.\n",
        "See [the rules](../_shared/r.md) for detail.\n",
        "Rules at `../_shared/r.md#step` apply.\n",
        "Rules at ``../_shared/r.md`` apply.\n",
        "A stray ` backtick, then ../_shared/r.md applies\n",
        "```\ncat ../_shared/r.md\n```\n",
    ], ids=["link-anchor", "link", "backtick-anchor", "double-backtick", "unpaired-backtick", "fence"])
    def test_a_rule_file_named_only_in_a_link_anchor_double_backtick_unpaired_backtick_or_fence_is_in_the_closure(
        self, text
    ):
        files = corpus(**{ENTRY: text})
        assert R in derive(ENTRY, [], reader(files)).files

    def test_an_anchored_or_linked_mention_of_an_undeclared_branch_is_not_in_the_closure(self):
        files = corpus(**{ENTRY: "See [b](b2.md#top) and ``b2.md#top``.\n```\nb2.md\n```\n"})
        assert B2 not in derive(ENTRY, [], reader(files)).files

    def test_a_linked_path_that_resolves_nowhere_or_escapes_is_silently_ignored(self):
        files = corpus(**{ENTRY: "See [x](../_shared/gone.md#a) and [y](../../../../x.md) and [z](/abs/x.md).\n"})
        assert derive(ENTRY, [], reader(files)).files == (ENTRY,)
        assert missing_loads([ENTRY], reader(files)) == []

    def test_a_mention_inside_a_branch_file_is_followed_through_a_link(self):
        files = corpus(**{B1: BRANCH_HEAD + "See [rule two](../_shared/r2.md#x).\n"})
        assert R2 in derive(ENTRY, [B1], reader(files)).files

    def test_a_missing_entry_is_a_fault(self):
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], reader({}))
        assert str(e.value) == f"entry '{ENTRY}' does not exist"

    def test_an_entry_that_is_a_branch_file_is_refused_naming_it(self):
        with pytest.raises(LoadError) as e:
            derive(B1, [], reader(corpus()))
        assert str(e.value) == (f"entry '{B1}' is a branch file; declare its core as entry "
                                "and the branch under branches")

    def test_an_entry_that_is_a_branch_file_is_refused_even_when_a_branch_is_declared(self):
        with pytest.raises(LoadError) as e:
            derive(B1, [B2], reader(corpus()))
        assert str(e.value).startswith(f"entry '{B1}' is a branch file")

    def test_a_missing_text_is_a_fault_not_an_attribute_error(self):
        reads = []

        def vanishing(path: str):
            reads.append(path)
            return "Plain text.\n" if len(reads) == 1 else None

        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], vanishing)
        assert str(e.value) == f"'{ENTRY}' does not exist"


class TestFaults:
    def test_a_declared_branch_the_entry_never_loads_names_the_entry_and_the_branch(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now.\n"})
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [B1], reader(files))
        assert str(e.value) == f"entry '{ENTRY}' does not load the declared branch '{B1}'"

    def test_a_declared_branch_the_entry_only_mentions_is_refused_on_head(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now and follow it in full.\n\nThe branch `b1.md` exists.\n"})
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [B1], reader(files))
        assert str(e.value) == f"entry '{ENTRY}' does not load the declared branch '{B1}'"

    def test_a_declared_branch_the_entry_only_mentions_derives_on_the_base_revision(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now and follow it in full.\n\nThe branch `b1.md` exists.\n"})
        got = derive(ENTRY, [B1], reader(files), base_tolerant=True)
        assert B1 in got.files

    def test_the_two_modes_do_not_share_a_remembered_derivation(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now and follow it in full.\n\nThe branch `b1.md` exists.\n"})
        read = loads.cached(reader(files))
        assert B1 in derive(ENTRY, [B1], read, base_tolerant=True).files
        with pytest.raises(LoadError):
            derive(ENTRY, [B1], read)

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
        files = corpus(**{B1: BRANCH_HEAD + "Read `b2.md` now and follow it in full.\n"})
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [B1], reader(files))
        assert str(e.value) == f"branch '{B1}' loads the branch '{B2}'"

    def test_a_load_resolving_outside_the_plugin_root_faults(self):
        files = corpus(**{R: "Read `../../../x.md` now and follow it in full.\n"})
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], reader(files))
        assert str(e.value) == f"'{R}' loads '../../../x.md', which is outside the plugin root"

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


class TestSplitReachesBelowTheEntry:
    def test_a_branch_loaded_by_a_rule_file_the_entry_loads_makes_the_closure_split(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now.\n", R: "Rule.\n\nRead `../x/b1.md` when attended.\n"})
        got = derive(ENTRY, [], reader(files))
        assert got.split is True
        assert B1 not in got.files

    def test_a_branch_linked_only_as_a_markdown_token_from_a_rule_file_makes_the_closure_split(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now.\n", R: "See [the branch](../x/b1.md).\n"})
        got = derive(ENTRY, [], reader(files))
        assert got.split is True
        assert B1 not in got.files

    def test_a_closure_that_reaches_no_branch_anywhere_is_not_split(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now.\n", R: "See [other](r2.md).\n"})
        assert derive(ENTRY, [], reader(files)).split is False

    def test_a_branch_reached_below_the_entry_stays_out_of_the_files_even_when_another_is_declared(self):
        files = corpus(**{R: "See [b2](../x/b2.md).\n"})
        got = derive(ENTRY, [B1], reader(files))
        assert B2 not in got.files and got.split is True


class TestDeriveIsMemoisedPerReader:
    def test_a_second_case_with_the_same_declaration_walks_nothing_again(self, monkeypatch):
        read = cached(reader(corpus()))
        first = derive(ENTRY, [B1], read)
        walked: list[str] = []
        real = loads._get
        monkeypatch.setattr(loads, "_get", lambda r, path: walked.append(path) or real(r, path))
        assert derive(ENTRY, [B1], read) == first
        assert walked == []

    def test_a_different_declaration_is_derived_on_its_own(self, monkeypatch):
        read = cached(reader(corpus()))
        one = derive(ENTRY, [B1], read)
        walked: list[str] = []
        real = loads._get
        monkeypatch.setattr(loads, "_get", lambda r, path: walked.append(path) or real(r, path))
        two = derive(ENTRY, [B2], read)
        assert walked and two != one and B2 in two.files and B1 not in two.files

    def test_a_declaration_that_faults_faults_the_same_way_again_without_walking(self, monkeypatch):
        read = cached(reader(corpus()))
        with pytest.raises(LoadError) as first:
            derive(ENTRY, ["skills/x/nope.md"], read)
        walked: list[str] = []
        real = loads._get
        monkeypatch.setattr(loads, "_get", lambda r, path: walked.append(path) or real(r, path))
        with pytest.raises(LoadError) as again:
            derive(ENTRY, ["skills/x/nope.md"], read)
        assert str(again.value) == str(first.value) and walked == []

    def test_an_uncached_reader_still_derives_every_time(self):
        read, seen = counting(corpus())
        derive(ENTRY, [B1], read)
        before = len(seen)
        derive(ENTRY, [B1], read)
        assert len(seen) == 2 * before


class TestMissingLoadsKeepsFirstOccurrenceOrder:
    def test_many_repeats_collapse_to_each_path_once_in_first_seen_order(self):
        text = "".join(f"Read `r{i % 3}.md` now and follow it in full.\n\n" for i in range(300))
        assert missing_loads([ENTRY], reader({ENTRY: text})) == [
            (ENTRY, "skills/x/r0.md"), (ENTRY, "skills/x/r1.md"), (ENTRY, "skills/x/r2.md")]


class TestTreeReaderBlobCap:
    def test_a_file_of_exactly_the_cap_is_read(self, tmp_path):
        (tmp_path / "a.md").write_text("x" * MAX_BLOB_BYTES)
        assert len(tree_reader(tmp_path)("a.md")) == MAX_BLOB_BYTES

    def test_a_file_one_byte_over_the_cap_is_a_fault_naming_it(self, tmp_path):
        (tmp_path / "a.md").write_text("x" * (MAX_BLOB_BYTES + 1))
        with pytest.raises(LoadError) as e:
            derive("a.md", [], tree_reader(tmp_path))
        assert str(e.value) == f"'a.md' is larger than {MAX_BLOB_BYTES} bytes"


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
        files[ENTRY] = files[ENTRY].rstrip("\n") + "\nRead `b1.md` now and follow it in full.\n"
        files[B1] = BRANCH_HEAD
        assert len(derive(ENTRY, [B1], reader(files)).files) == MAX_PROCEDURE_FILES
        files["skills/x/extra.md"] = "e\n"
        files[ENTRY] = files[ENTRY].rstrip("\n") + " See `extra.md`.\n"
        with pytest.raises(LoadError):
            derive(ENTRY, [B1], reader(files))


class TestMentionResolution:
    G = "skills/g/SKILL.md"

    def test_a_mention_resolves_in_an_ancestor_directory_when_the_mentioning_directory_lacks_it(self):
        files = {ENTRY: "See `x/a.md`.\n", "skills/x/a.md": "a\n"}
        assert "skills/x/a.md" in derive(ENTRY, [], reader(files)).files

    def test_the_mentioning_directory_wins_when_both_exist(self):
        files = {self.G: "See `g/a.md`.\n", "skills/g/a.md": "a\n", "skills/g/g/a.md": "b\n"}
        got = derive(self.G, [], reader(files)).files
        assert "skills/g/g/a.md" in got and "skills/g/a.md" not in got

    def test_the_walk_reaches_the_plugin_root(self):
        files = {self.G: "See `top/a.md`.\n", "top/a.md": "a\n"}
        assert "top/a.md" in derive(self.G, [], reader(files)).files

    def test_a_mention_that_leaves_the_plugin_root_is_never_resolved_even_when_the_reader_has_it(self):
        files = {self.G: "See `../../../x.md` and `../../a.md`.\n", "x.md": "inside\n", "../a.md": "outside\n"}
        assert derive(self.G, [], reader(files)).files == (self.G,)


class TestDeclaredBranchLoadedBelowTheEntry:
    CORE = "skills/_shared/core.md"
    B = "skills/_shared/b.md"
    ENTRY_FULL = "Read `../_shared/core.md` now and follow it in full.\n"

    def _files(self, core_text: str) -> dict[str, str]:
        return {ENTRY: self.ENTRY_FULL, self.CORE: core_text, self.B: BRANCH_HEAD + "Branch.\n"}

    def test_a_declared_branch_a_core_in_the_closure_loads_is_accepted_on_head(self):
        files = self._files("Core.\n\nWhen the run is unattended, read `b.md` now and follow it in full.\n")
        got = derive(ENTRY, [self.B], reader(files))
        assert set(got.files) == {ENTRY, self.CORE, self.B}
        assert got.split is True

    def test_a_branch_the_core_loads_stays_out_of_the_closure_when_it_is_not_declared(self):
        files = self._files("Core.\n\nWhen the run is unattended, read `b.md` now and follow it in full.\n")
        assert set(derive(ENTRY, [], reader(files)).files) == {ENTRY, self.CORE}

    def test_a_declared_branch_a_core_in_the_closure_only_mentions_is_refused_on_head(self):
        files = self._files("Core.\n\nThe branch `b.md` exists.\n")
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [self.B], reader(files))
        assert str(e.value) == f"entry '{ENTRY}' does not load the declared branch '{self.B}'"

    def test_a_declared_branch_no_file_of_the_closure_loads_is_refused_on_head(self):
        files = {**self._files("Core.\n"), "skills/_shared/other.md": "Other.\n"}
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [self.B], reader(files))
        assert str(e.value) == f"entry '{ENTRY}' does not load the declared branch '{self.B}'"

    def test_a_declared_branch_only_another_branch_file_loads_is_refused_on_head(self):
        other = "skills/_shared/other.md"
        files = {**self._files("Core.\n\nWhen the run is unattended, read `other.md` now and follow it in full.\n"),
                 other: BRANCH_HEAD + "When the run is attended, read `b.md` now and follow it in full.\n"}
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [self.B], reader(files))
        assert str(e.value) == f"entry '{ENTRY}' does not load the declared branch '{self.B}'"

    def test_a_branch_loaded_by_a_file_outside_the_closure_is_refused_on_head(self):
        files = {**self._files("Core.\n"), "skills/y/SKILL.md": self.ENTRY_FULL,
                 "skills/_shared/elsewhere.md": "When the run is unattended, read `b.md` now and follow it in full.\n"}
        with pytest.raises(LoadError):
            derive(ENTRY, [self.B], reader(files))


class TestPluginRootVariableMentions:
    VAR = "${CLAUDE_PLUGIN_ROOT}"

    def _entry(self, rest: str) -> str:
        return f"Read `../_shared/r.md` now and follow it in full.\n\nThe template is {self.VAR}{rest}.\n"

    def test_a_path_written_under_the_plugin_root_variable_is_followed_to_the_plugin_relative_file(self):
        files = corpus(**{ENTRY: self._entry("/templates/t.md"), "templates/t.md": "T\n"})
        assert "templates/t.md" in derive(ENTRY, [], reader(files)).files

    def test_the_variable_path_names_a_file_the_plugin_lacks_so_nothing_is_added(self):
        files = corpus(**{ENTRY: self._entry("/templates/gone.md"), "templates/t.md": "T\n"})
        assert set(derive(ENTRY, [], reader(files)).files) == {ENTRY, R}

    def test_the_variable_path_resolves_from_the_plugin_root_and_never_from_an_ancestor_of_the_mentioner(self):
        files = corpus(**{ENTRY: self._entry("/t.md"), "skills/x/t.md": "near\n"})
        assert "skills/x/t.md" not in derive(ENTRY, [], reader(files)).files

    def test_the_variable_path_resolves_to_the_plugin_root_file_when_a_nearer_one_exists(self):
        files = corpus(**{ENTRY: self._entry("/t.md"), "skills/x/t.md": "near\n", "t.md": "root\n"})
        assert "t.md" in derive(ENTRY, [], reader(files)).files

    def test_the_variable_path_cannot_climb_out_of_the_plugin_root(self):
        files = corpus(**{ENTRY: self._entry("/../x.md"), "x.md": "inside\n", "../x.md": "outside\n"})
        assert set(derive(ENTRY, [], reader(files)).files) == {ENTRY, R}


class TestAbsoluteMentions:
    def test_an_absolute_path_to_a_real_file_outside_the_plugin_is_not_in_the_closure(self, tmp_path):
        outside = tmp_path / "outside.md"
        outside.write_text("outside\n")
        plugin = tmp_path / "plugin"
        write_tree(plugin, corpus(**{ENTRY: f"Read `../_shared/r.md` now and follow it in full.\n\nSee {outside}.\n"}))
        got = derive(ENTRY, [], loads.tree_reader(plugin))
        assert set(got.files) == {ENTRY, R}

    def test_a_resolution_from_an_absolute_file_name_terminates(self):
        calls = []

        def read(path: str):
            calls.append(path)
            if len(calls) >= 50:
                pytest.fail("the upward search did not terminate")
            return None
        assert loads.resolve_existing("/x/y.md", "gone.md", read) is None


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
        files = {ENTRY: "Read `../_shared/gone.md` now and follow it in full.\n"}
        assert missing_loads([ENTRY], reader(files)) == [(ENTRY, "skills/_shared/gone.md")]

    def test_files_whose_loads_all_exist_return_nothing(self):
        assert missing_loads([ENTRY, B1], reader(corpus())) == []

    def test_a_load_outside_the_plugin_root_raises(self):
        with pytest.raises(LoadError) as e:
            missing_loads([ENTRY], reader({ENTRY: "Read `../../../x.md` now and follow it in full.\n"}))
        assert "outside the plugin root" in str(e.value)

    def test_only_loads_count_not_other_missing_mentions(self):
        files = {ENTRY: "See `gone.md`.\n\nRead `r.md` now and follow it in full.\n", "skills/x/r.md": "r\n"}
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
        calls: list[tuple[str, str]] = []
        real = loads.parse_directives

        def counting_parse(path, text, read):
            calls.append((path, text))
            return real(path, text, read)

        monkeypatch.setattr(loads, "parse_directives", counting_parse)
        return calls

    def test_two_cases_sharing_a_rule_file_parse_it_once_per_revision_cache(self, monkeypatch):
        calls = self._parse_counter(monkeypatch)
        files = corpus()
        shared = cached(reader(files))
        derive(ENTRY, [B1], shared)
        derive(ENTRY, [B1, B2], shared)
        missing_loads(list(files), shared)
        assert calls and len(calls) == len(set(calls))

    def test_without_a_cache_wrapper_each_call_still_parses_and_answers(self):
        assert derive(ENTRY, [B1], reader(corpus())).files == derive(ENTRY, [B1], cached(reader(corpus()))).files

    def test_separate_revision_caches_do_not_share_parses_for_the_same_path(self):
        base = {ENTRY: "Read `old.md` now and follow it in full.\n", "skills/x/old.md": "o\n", "skills/x/new.md": "n\n"}
        head = {ENTRY: "Read `new.md` now and follow it in full.\n", "skills/x/old.md": "o\n", "skills/x/new.md": "n\n"}
        base_read, head_read = cached(reader(base)), cached(reader(head))
        assert derive(ENTRY, [], base_read).files == (ENTRY, "skills/x/old.md")
        assert derive(ENTRY, [], head_read).files == (ENTRY, "skills/x/new.md")
        assert missing_loads([ENTRY], head_read) == []


class TestLinearSentences:
    def test_twenty_thousand_abbreviations_in_one_sentence_are_split_in_linear_time(self):
        text = "go " + "e.g. " * 20000 + "then stop.\n\nRead `r.md` now and follow it in full.\n"
        started = time.perf_counter()
        got = parse_directives(ENTRY, text, reader({}))
        elapsed = time.perf_counter() - started
        assert [load.raw for load in got.loads] == ["r.md"]
        assert elapsed < 1.0

    def test_e_g_and_i_e_do_not_end_a_sentence_but_a_word_ending_in_them_does(self):
        text = ("When asked e.g. now and (i.e. later), read `a.md` now and follow it in full. Mre.g. x. "
                "Read `d.md` now and follow it in full.\n")
        got = parse_directives(ENTRY, text, reader({}))
        assert [load.raw for load in got.loads] == ["a.md", "d.md"]


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
            assert missing_loads(_procedures(root), read if root == craft else cached(tree_reader(root))) == [], name
        elapsed = time.perf_counter() - started
        assert derived > 20
        assert elapsed < 1.0, f"{elapsed:.2f}s"


class TestTreeReaderContainment:
    def test_a_directory_symlink_inside_the_plugin_cannot_read_a_file_outside_it(self, tmp_path):
        root = tmp_path / "plugin"
        (root / "skills").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("secret\n")
        (root / "skills" / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(NotRegularFile):
            tree_reader(root)("skills/link/secret.md")

    def test_a_real_directory_inside_the_plugin_reads_normally(self, tmp_path):
        root = tmp_path / "plugin"
        (root / "skills" / "real").mkdir(parents=True)
        (root / "skills" / "real" / "a.md").write_text("fine\n")
        assert tree_reader(root)("skills/real/a.md") == "fine\n"

    def test_a_plugin_root_that_is_itself_reached_through_a_symlink_still_reads(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        (real / "a.md").write_text("fine\n")
        (tmp_path / "via").symlink_to(real, target_is_directory=True)
        assert tree_reader(tmp_path / "via")("a.md") == "fine\n"


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def directives(tmp_path: Path, text: str, extra: dict[str, str] | None = None, path: str = "skills/x/SKILL.md"):
    """Parse ``text`` as the file ``path`` of a real plugin tree that also holds ``extra`` files."""
    root = tmp_path / "plugin"
    write_tree(root, {path: text, "skills/x/a.md": "A\n", "skills/x/b.md": "B\n",
                      "skills/_shared/r.md": "R\n", **(extra or {})})
    return parse_directives(path, text, tree_reader(root))


class TestParseDirectivesFixedForms:
    @pytest.mark.parametrize(("text", "kind", "raw", "condition"), [
        ("Read `a.md` now and follow it in full.\n", "full", "a.md", ""),
        ("Read `a.md` now. Use its second section only.\n", "section", "a.md", ""),
        ("When the run is unattended, read `a.md` now and follow it in full.\n", "branch", "a.md",
         "the run is unattended"),
        ("- Read `a.md` now and follow it in full.\n", "full", "a.md", ""),
        ("1. When it is `x`, read `a.md` now and follow it in full.\n", "branch", "a.md", "it is `x`"),
    ])
    def test_each_fixed_form_is_one_load_of_its_kind(self, tmp_path, text, kind, raw, condition):
        result = directives(tmp_path, "# T\n\n" + text)
        assert result.misworded == ()
        assert [(load.kind, load.raw, load.condition, load.line) for load in result.loads] == [
            (kind, raw, condition, 3)]

    def test_a_load_wrapped_over_two_lines_reports_the_line_it_starts_on(self, tmp_path):
        result = directives(tmp_path, "# T\n\nRead `a.md`\nnow and follow it in full.\n")
        assert [(load.raw, load.line) for load in result.loads] == [("a.md", 3)]

    def test_a_load_wrapped_inside_an_indented_list_item_is_still_one_load(self, tmp_path):
        result = directives(tmp_path, "# T\n\n- Intro sentence here. Read\n  `a.md`\n  now and follow it in full.\n")
        assert result.misworded == ()
        assert [(load.raw, load.line) for load in result.loads] == [("a.md", 3)]

    def test_two_loads_in_one_paragraph_are_both_found_in_order(self, tmp_path):
        result = directives(tmp_path, "Read `a.md` now and follow it in full. "
                                      "When x, read `b.md` now and follow it in full.\n")
        assert [load.raw for load in result.loads] == ["a.md", "b.md"]


class TestParseDirectivesBold:
    @pytest.mark.parametrize(("text", "kind", "condition"), [
        ("**Read `a.md` now and follow it in full.**\n", "full", ""),
        ("**Read `a.md` now.** Use its second section only.\n", "section", ""),
        ("**When the run is unattended, read `a.md` now and follow it in full.**\n", "branch",
         "the run is unattended"),
        ("**Do not skip this.** Read `a.md` now and follow it in full.\n", "full", ""),
        ("**Read `a.md` now and follow it in full.** Then stop.\n", "full", ""),
    ])
    def test_a_fixed_sentence_wrapped_whole_in_bold_is_a_load_of_its_kind(self, tmp_path, text, kind, condition):
        result = directives(tmp_path, "# T\n\n" + text)
        assert result.misworded == ()
        assert [(load.kind, load.raw, load.condition, load.line) for load in result.loads] == [
            (kind, "a.md", condition, 3)]

    @pytest.mark.parametrize("text", [
        "**Read `a.md` from the top and follow it in full.**\n",
        "**Read `a.md` and `b.md` now and follow them in full.**\n",
        "**Read `a.md` now.**\n",
    ])
    def test_a_bold_sentence_outside_the_fixed_wording_is_still_misworded(self, tmp_path, text):
        result = directives(tmp_path, "# T\n\n" + text)
        assert result.loads == ()
        assert [m.line for m in result.misworded] == [3]


class TestParseDirectivesStrictResolution:
    def test_a_path_resolves_against_the_loading_files_own_directory(self, tmp_path):
        result = directives(tmp_path, "Read `a.md` now and follow it in full.\n")
        assert result.loads[0].target == "skills/x/a.md"

    def test_a_shared_path_written_from_the_loading_directory_reaches_the_shared_file(self, tmp_path):
        result = directives(tmp_path, "Read `../_shared/r.md` now and follow it in full.\n")
        assert result.loads[0].target == "skills/_shared/r.md"

    def test_a_path_that_exists_only_through_an_ancestor_does_not_resolve_there(self, tmp_path):
        result = directives(tmp_path, "Read `_shared/r.md` now and follow it in full.\n")
        assert result.loads[0].target == "skills/x/_shared/r.md"

    @pytest.mark.parametrize("raw", ["/etc/passwd", "../../../out.md", "../../../../a.md"])
    def test_an_absolute_or_escaping_path_has_no_target(self, tmp_path, raw):
        result = directives(tmp_path, f"Read `{raw}` now and follow it in full.\n")
        assert [load.target for load in result.loads] == [None]


class TestParseDirectivesMisworded:
    @pytest.mark.parametrize("text", [
        "Read `a.md` from the top and follow it in full.\n",
        "Read `a.md` in full, together with `b.md`.\n",
        "Read both `a.md` and `b.md` now and follow them in full.\n",
        "Read `a.md` now: its Steps section.\n",
        "read `a.md` now and follow it in full.\n",
        "See [the steps](a.md) for details.\n",
        "Apply `a.md` before you start.\n",
        "Per `a.md`, check the result.\n",
        "When x, read `a.md` now.\n",
        "Read `a.md` now and follow it in full, and read `b.md` now and follow it in full.\n",
        "Unlike a.md, consult `a.md`.\n",
        "Unlike a.md, consult [a](a.md).\n",
    ])
    def test_a_directive_naming_an_existing_file_outside_the_fixed_wording_is_reported(self, tmp_path, text):
        result = directives(tmp_path, "# T\n\n" + text)
        assert result.loads == ()
        assert [m.line for m in result.misworded] == [3]

    def test_a_load_naming_two_files_is_reported_with_both_paths(self, tmp_path):
        result = directives(tmp_path, "Read `a.md` and `b.md` now and follow it in full.\n")
        assert result.misworded[0].paths == ("a.md", "b.md")

    @pytest.mark.parametrize("text", [
        "The attribution lists `a.md` and `b.md`.\n",
        "See `missing.md` for details.\n",
        "Read `a.md` now and follow it in full.\n\n```\nsee `b.md`\n```\n",
        "<!-- see `b.md` -->\n",
        "Use the `git status` command.\n",
        "Unlike a.md, the file `a.md` is a plain mention.\n",
    ])
    def test_text_that_names_no_existing_file_after_a_directive_is_not_reported(self, tmp_path, text):
        assert directives(tmp_path, text).misworded == ()

    def test_a_directive_verb_only_counts_before_the_path_it_names(self, tmp_path):
        assert directives(tmp_path, "`a.md` is the file to use.\n").misworded == ()

    def test_a_section_read_with_nothing_after_it_is_reported(self, tmp_path):
        result = directives(tmp_path, "Read `a.md` now.\n")
        assert result.loads == ()
        assert [m.line for m in result.misworded] == [1]

    def test_a_section_read_followed_by_a_sentence_in_the_next_paragraph_is_reported(self, tmp_path):
        result = directives(tmp_path, "Read `a.md` now.\n\nUse the Steps section.\n")
        assert [m.line for m in result.misworded] == [1]

    def test_an_existing_file_is_found_from_the_loading_directory_or_an_ancestor(self, tmp_path):
        result = directives(tmp_path, "See `skills/_shared/r.md`.\n")
        assert [m.paths for m in result.misworded] == [("skills/_shared/r.md",)]


def declared(tmp_path: Path, body: str | None) -> dict[str, tuple[str, ...]]:
    root = tmp_path / "plugin"
    root.mkdir()
    if body is not None:
        (root / "declared-mentions.json").write_text(body, encoding="utf-8")
    return read_declared_mentions(root)


class TestDeclaredMentions:
    def test_a_missing_file_is_an_empty_list(self, tmp_path):
        assert declared(tmp_path, None) == {}

    def test_each_mentioning_file_maps_to_the_paths_it_names(self, tmp_path):
        body = json.dumps({"skills/x/SKILL.md": ["skills/_shared/r.md", "skills/y/a.md"], "agents/e.md": []})
        assert declared(tmp_path, body) == {"skills/x/SKILL.md": ("skills/_shared/r.md", "skills/y/a.md"),
                                            "agents/e.md": ()}

    def test_the_same_text_parses_the_same_without_a_file(self):
        assert parse_declared_mentions('{"a.md": ["b.md"]}') == {"a.md": ("b.md",)}

    @pytest.mark.parametrize(("body", "fragment"), [
        ("{not json", "is not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"a.md": "b.md"}', "must map to a list"),
        ('{"a.md": [1]}', "path #1"),
        ('{"a.md": ["../b.md"]}', "path '../b.md'"),
        ('{"a.md": ["/b.md"]}', "path '/b.md'"),
        ('{"a.md": ["b c.md"]}', "path #1"),
        ('{"a.md": ["b\\\\c.md"]}', "path #1"),
        ('{"../a.md": []}', "file '../a.md'"),
        ('{"a b.md": []}', "file #1"),
    ])
    def test_a_malformed_list_is_refused_in_one_line(self, tmp_path, body, fragment):
        with pytest.raises(LoadError) as caught:
            declared(tmp_path, body)
        message = str(caught.value)
        assert fragment in message
        assert "\n" not in message
        assert "declared-mentions.json" in message

    def test_an_unsafe_value_is_echoed_by_position_only(self, tmp_path):
        with pytest.raises(LoadError) as caught:
            declared(tmp_path, '{"a.md": ["b c.md"]}')
        assert "b c.md" not in str(caught.value)


class TestClosureFollowsLoadsAndUndeclaredMentions:
    A = "skills/a/SKILL.md"
    OTHER = "skills/b/SKILL.md"
    MENTION = "Run the sibling procedure `../b/SKILL.md` on its own.\n"
    DECLARATION = json.dumps({A: [OTHER]})

    def files(self, entry_text: str, declared: str | None = None) -> dict[str, str]:
        files = {self.A: entry_text, self.OTHER: "Other core.\n"}
        if declared is not None:
            files["declared-mentions.json"] = declared
        return files

    def test_a_mention_the_plugin_declares_does_not_pull_the_mentioned_core_into_the_closure(self):
        got = derive(self.A, [], reader(self.files(self.MENTION, self.DECLARATION)))
        assert got.files == (self.A,)

    def test_the_same_mention_undeclared_pulls_it_in(self):
        got = derive(self.A, [], reader(self.files(self.MENTION)))
        assert got.files == (self.A, self.OTHER)

    def test_a_fixed_wording_load_of_it_pulls_it_in_even_when_the_file_declares_the_path(self):
        text = "Read `../b/SKILL.md` now and follow it in full.\n"
        got = derive(self.A, [], reader(self.files(text, self.DECLARATION)))
        assert got.files == (self.A, self.OTHER)

    def test_a_declaration_for_another_file_does_not_cover_this_files_mention(self):
        declared = json.dumps({"skills/c/SKILL.md": [self.OTHER]})
        got = derive(self.A, [], reader(self.files(self.MENTION, declared)))
        assert self.OTHER in got.files

    def test_each_revisions_reader_serves_its_own_declared_list(self):
        base = cached(reader(self.files(self.MENTION)))
        head = cached(reader(self.files(self.MENTION, self.DECLARATION)))
        assert derive(self.A, [], base).files == (self.A, self.OTHER)
        assert derive(self.A, [], head).files == (self.A,)

    def test_a_malformed_declared_list_is_a_one_line_fault(self):
        with pytest.raises(LoadError) as e:
            derive(self.A, [], reader(self.files(self.MENTION, "[]")))
        assert str(e.value).startswith("declared-mentions.json ")

    @pytest.mark.parametrize("name", ["helper.py", "table.json"])
    def test_a_fixed_wording_load_of_a_file_that_is_not_markdown_pulls_it_into_the_closure(self, name):
        target = f"skills/_shared/{name}"
        files = self.files(f"Read `../_shared/{name}` now and follow it in full.\n", json.dumps({self.A: [target]}))
        files[target] = "payload\n"
        assert target in derive(self.A, [], reader(files)).files

    def test_a_file_reached_only_through_a_non_markdown_load_is_followed_from_there(self):
        files = self.files("Read `../_shared/helper.py` now and follow it in full.\n",
                           json.dumps({self.A: ["skills/_shared/helper.py"]}))
        files["skills/_shared/helper.py"] = "See `tail.md`.\n"
        files["skills/_shared/tail.md"] = "tail\n"
        assert "skills/_shared/tail.md" in derive(self.A, [], reader(files)).files

    def test_a_load_phrased_outside_every_recognised_form_still_pulls_its_file_in(self):
        files = self.files("Apply `../_shared/x.md`.\n")
        files["skills/_shared/x.md"] = "x\n"
        assert derive(self.A, [], reader(files)).files == (self.A, "skills/_shared/x.md")

    def test_a_declared_branch_the_entry_names_in_the_old_wording_still_derives(self):
        text = ("Read `r.md` now.\n\nUse the rest.\n\n"
                "Read `b1.md` when the run is unattended.\n")
        files = {ENTRY: text, B1: BRANCH_HEAD + "Branch.\n", "skills/x/r.md": "r\n"}
        got = derive(ENTRY, [B1], reader(files), base_tolerant=True)
        assert got.files == (ENTRY, B1, "skills/x/r.md") and got.split is True

    def test_an_old_wording_load_resolved_only_through_an_ancestor_is_still_followed(self):
        files = {ENTRY: "Read `_shared/r.md` now.\n\nUse the rest.\n", R: "Rule.\n"}
        assert R in derive(ENTRY, [], reader(files)).files


class TestStrictLoadResolution:
    G = "skills/g/SKILL.md"
    TEXT = "Read `g/a.md` now and follow it in full.\n"

    def test_a_load_that_exists_only_in_an_ancestor_directory_is_reported_missing(self):
        read = reader({self.G: self.TEXT, "skills/g/a.md": "a\n"})
        assert missing_loads([self.G], read) == [(self.G, "skills/g/g/a.md")]

    def test_a_load_that_exists_in_the_loading_directory_is_not_missing(self):
        read = reader({self.G: self.TEXT, "skills/g/g/a.md": "a\n"})
        assert missing_loads([self.G], read) == []

    def test_a_load_that_exists_nowhere_is_reported_in_its_loading_directory_form(self):
        assert missing_loads([self.G], reader({self.G: self.TEXT})) == [(self.G, "skills/g/g/a.md")]

    def test_an_escaping_fixed_wording_load_is_refused(self):
        text = "Read `../../../x.md` now and follow it in full.\n"
        with pytest.raises(LoadError) as e:
            missing_loads([self.G], reader({self.G: text}))
        assert str(e.value) == f"'{self.G}' loads '../../../x.md', which is outside the plugin root"

    def test_a_fixed_wording_load_path_with_a_character_outside_the_allowlist_is_refused(self):
        text = "Read `a b$.md` now and follow it in full.\n"
        with pytest.raises(LoadError) as e:
            missing_loads([self.G], reader({self.G: text}))
        assert str(e.value) == f"'{self.G}' load #1 has characters outside [A-Za-z0-9._/-]"


class TestBinaryMentions:
    PNG = "skills/x/pic.png"

    def _binary_reader(self, files: dict[str, str]):
        def read(path: str):
            if path == self.PNG:
                raise UnicodeDecodeError("utf-8", b"\x89PNG", 0, 1, "invalid start byte")
            return files.get(path)
        return read

    def test_a_mentioned_binary_file_is_a_leaf_in_the_closure(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now and follow it in full.\n\nSee pic.png for the layout.\n"})
        got = derive(ENTRY, [], self._binary_reader(files))
        assert self.PNG in got.files

    def test_a_binary_file_reached_through_a_fixed_wording_load_is_a_leaf(self):
        files = corpus(**{ENTRY: "Read `pic.png` now and follow it in full.\n"})
        got = derive(ENTRY, [], self._binary_reader(files))
        assert self.PNG in got.files
        assert loads.missing_loads(got.files, self._binary_reader(files)) == []

    def test_a_real_binary_file_through_the_tree_reader_is_a_leaf(self, tmp_path):
        write_tree(tmp_path, corpus(**{ENTRY: "Read `../_shared/r.md` now and follow it in full.\n\nSee pic.png.\n"}))
        (tmp_path / self.PNG).write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
        got = derive(ENTRY, [], loads.tree_reader(tmp_path))
        assert self.PNG in got.files

    def test_a_symlinked_mention_still_faults(self, tmp_path):
        write_tree(tmp_path, corpus(**{ENTRY: "Read `../_shared/r.md` now and follow it in full.\n\nSee pic.png.\n"}))
        (tmp_path / "outside.png").write_bytes(b"\xff\xfe")
        (tmp_path / self.PNG).symlink_to(tmp_path / "outside.png")
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], loads.tree_reader(tmp_path))
        assert str(e.value) == f"'{self.PNG}' is a symlink or not a regular file"

    def test_an_unreadable_mention_still_faults(self):
        files = corpus(**{ENTRY: "Read `../_shared/r.md` now and follow it in full.\n\nSee pic.png.\n"})

        def read(path: str):
            if path == self.PNG:
                raise PermissionError(path)
            return files.get(path)
        with pytest.raises(LoadError) as e:
            derive(ENTRY, [], read)
        assert str(e.value) == f"'{self.PNG}' could not be read"
