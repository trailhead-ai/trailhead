"""The load convention check: one plugin root in, one-line faults out.

Every test builds a real plugin tree under ``tmp_path`` (files, symlinks and unreadable files on the real
disk), runs ``check_plugin`` over it and asserts on the faults that came back. Each rule has a tree that
passes and a tree that fails, and the failing one varies exactly the input the rule decides on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from portage.eval_gate.convention import check_plugin

FULL = "Read `{p}` now and follow it in full.\n"
BRANCH = "When the run is unattended, read `{p}` now and follow it in full.\n"
OPENING = "Applies when the run is unattended.\n\nSteps.\n"


def plugin(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    root = tmp_path / "plugin"
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8")
    return root


def faults_of(tmp_path: Path, files: dict[str, str | bytes]) -> list[str]:
    return check_plugin(plugin(tmp_path, files))


def rule_faults(faults: list[str], rule: str) -> list[str]:
    return [f for f in faults if f": {rule}: " in f]


class TestFaultShape:
    def test_a_fault_is_one_line_naming_the_file_the_line_the_rule_and_the_sentence_to_use(self, tmp_path):
        faults = faults_of(tmp_path, {"skills/x/SKILL.md": "# T\n\nread `a.md` now.\n", "skills/x/a.md": "A\n"})
        assert len(faults) == 1
        fault = faults[0]
        assert "\n" not in fault
        assert fault.startswith("skills/x/SKILL.md:3: misworded-load: ")
        assert "Read `<path>` now and follow it in full." in fault

    def test_a_path_with_characters_outside_the_safe_set_is_echoed_by_position(self, tmp_path):
        faults = faults_of(tmp_path, {"skills/x/SKILL.md": "Read `my file.md` now and follow it in full.\n",
                                      "skills/x/my file.md": "A\n"})
        assert faults and all("my file.md" not in f for f in faults)

    def test_a_clean_tree_has_no_faults(self, tmp_path):
        assert faults_of(tmp_path, {"skills/x/SKILL.md": "# T\n\n" + FULL.format(p="a.md"),
                                    "skills/x/a.md": "A\n"}) == []

    def test_a_root_with_no_skills_or_agents_is_clean(self, tmp_path):
        assert faults_of(tmp_path, {"README.md": "x\n"}) == []


class TestWording:
    BASE = {"skills/x/a.md": "A\n", "skills/x/b.md": "B\n"}

    @pytest.mark.parametrize("sentence", [
        "Read `a.md` from the top and follow it in full.",
        "Read `a.md` in full, together with `b.md`.",
        "Read both `a.md` and `b.md` now and follow them in full.",
        "Read `a.md` now: its Steps section.",
        "read `a.md` now and follow it in full.",
        "See [the steps](a.md) for details.",
        "Apply `a.md` before you start.",
    ])
    def test_each_variant_phrasing_is_refused_at_its_line(self, tmp_path, sentence):
        faults = faults_of(tmp_path, {**self.BASE, "skills/x/SKILL.md": f"# T\n\n{sentence}\n"})
        hits = [f for f in faults if f.startswith("skills/x/SKILL.md:3: ")]
        assert len(hits) == 1
        assert ": misworded-load: " in hits[0] or ": load-names-several-files: " in hits[0]

    def test_a_load_naming_two_files_is_refused_as_naming_several(self, tmp_path):
        faults = faults_of(tmp_path, {**self.BASE,
                                      "skills/x/SKILL.md": "Read `a.md` and `b.md` now and follow it in full.\n"})
        assert len(rule_faults(faults, "load-names-several-files")) == 1

    @pytest.mark.parametrize("sentence", [
        "Read `a.md` now and follow it in full.",
        "When the run is unattended, read `b.md` now and follow it in full.",
        "Read `a.md` now. Use its Steps section only.",
    ])
    def test_each_fixed_form_passes(self, tmp_path, sentence):
        b = OPENING if "unattended" in sentence else "B\n"
        files = {**self.BASE, "skills/x/b.md": b if "unattended" in sentence else "B\n",
                 "skills/x/SKILL.md": f"# T\n\n{sentence}\n"}
        assert faults_of(tmp_path, files) == []

    def test_a_plain_mention_with_no_directive_verb_is_not_a_fault(self, tmp_path):
        files = {**self.BASE, "skills/x/SKILL.md": "# T\n\nThe inventory lists `a.md` and `b.md`.\n"}
        assert faults_of(tmp_path, files) == []

    def test_a_fixed_form_load_whose_condition_names_a_second_file_is_refused(self, tmp_path):
        files = {**self.BASE, "skills/x/SKILL.md": "When `b.md` says so, read `a.md` now and follow it in full.\n"}
        assert len(rule_faults(faults_of(tmp_path, files), "load-names-several-files")) == 1

    def test_a_section_read_without_its_following_sentence_is_refused(self, tmp_path):
        faults = faults_of(tmp_path, {**self.BASE, "skills/x/SKILL.md": "# T\n\nRead `a.md` now.\n"})
        assert len(rule_faults(faults, "misworded-load")) == 1

    def test_an_agents_file_with_a_misworded_load_is_refused(self, tmp_path):
        faults = faults_of(tmp_path, {**self.BASE, "agents/e.md": "# E\n\nRead `../skills/x/a.md` from the top.\n"})
        assert len(rule_faults(faults, "misworded-load")) == 1
        assert faults[0].startswith("agents/e.md:3: ")

    def test_the_same_agents_file_in_fixed_wording_passes(self, tmp_path):
        files = {**self.BASE, "agents/e.md": "# E\n\n" + FULL.format(p="../skills/x/a.md")}
        assert faults_of(tmp_path, files) == []


class TestBranchFileLoadsNothingBranch:
    FILES = {"skills/x/SKILL.md": BRANCH.format(p="b.md"), "skills/y/c.md": OPENING}

    def test_a_branch_file_that_branch_loads_another_is_refused(self, tmp_path):
        files = {**self.FILES, "skills/x/b.md": OPENING + BRANCH.format(p="../y/c.md")}
        faults = faults_of(tmp_path, files)
        assert [f.split(": ")[0] for f in rule_faults(faults, "branch-loads-branch")] == ["skills/x/b.md:4"]

    def test_the_same_branch_file_with_that_load_removed_passes(self, tmp_path):
        assert faults_of(tmp_path, {**self.FILES, "skills/x/b.md": OPENING}) == []


class TestSharedFilesStayOneHop:
    """A `_shared` file names a sibling only through its own branch load."""

    def shared(self, tmp_path, body, siblings=("status-ownership.md", "slice.md", "refine.md", "council.md")):
        files: dict[str, str | bytes] = {f"skills/_shared/{name}": "# S\n\nText.\n" for name in siblings}
        files["skills/_shared/doc.md"] = body
        return faults_of(tmp_path, files)

    @pytest.mark.parametrize("body", [
        "# T\n\nSee `../_shared/status-ownership.md` for the contract.\n",
        "# T\n\nThe shapes `_shared/slice.md` fixes.\n",
        "# T\n\nThe `_shared/council.md` precedent.\n",
        "# T\n\nA thin wrapper over it (refine.md's shape).\n",
        "# T\n\nThe shapes [slice](slice.md) fixes.\n",
        "# T\n\nThe shapes `slice.md` fixes.\n",
        "# T\n\nThe shapes slice.md fixes.\n",
    ])
    def test_a_sibling_named_in_any_path_form_is_refused_at_its_line(self, tmp_path, body):
        faults = rule_faults(self.shared(tmp_path, body), "shared-sibling-named")
        assert [f.split(": ")[0] for f in faults] == ["skills/_shared/doc.md:3"]

    def test_two_different_siblings_are_both_named(self, tmp_path):
        faults = rule_faults(self.shared(tmp_path, "# T\n\nThe shapes `slice.md`.\n\nAlso `status-ownership.md`.\n"),
                             "shared-sibling-named")
        assert len(faults) == 2
        assert "slice.md" in faults[0] and "status-ownership.md" in faults[1]

    def test_a_reference_on_the_first_line_after_a_fence_closes_is_refused(self, tmp_path):
        body = "# T\n\n```sh\necho hi\n```\nThe shapes `slice.md` fixes it right after the fence.\n"
        faults = rule_faults(self.shared(tmp_path, body), "shared-sibling-named")
        assert [f.split(": ")[0] for f in faults] == ["skills/_shared/doc.md:6"]

    def test_crlf_line_endings_report_the_same_line(self, tmp_path):
        body = "# T\n\nThe shapes `slice.md` fixes.\n".replace("\n", "\r\n").encode()
        faults = rule_faults(self.shared(tmp_path, body), "shared-sibling-named")
        assert [f.split(": ")[0] for f in faults] == ["skills/_shared/doc.md:3"]

    @pytest.mark.parametrize("hidden", [
        "\n```markdown\nThe shapes `slice.md` fixes.\n```\n",
        "\n~~~markdown\nThe shapes `slice.md` fixes.\n~~~\n",
        "\n````markdown\n```\nThe shapes `slice.md` fixes.\n```\n````\n",
        "\n```sh\nThe shapes slice.md fixes.\n",
        "\n<!-- the shapes slice.md fixes -->\n",
        "\n<!--\nthe shapes slice.md fixes\n-->\n",
    ])
    def test_a_mention_inside_a_fence_or_an_html_comment_is_invisible(self, tmp_path, hidden):
        assert self.shared(tmp_path, "# T\n\nOrdinary prose.\n" + hidden) == []

    def test_a_document_naming_its_own_filename_is_not_a_fault(self, tmp_path):
        assert self.shared(tmp_path, "# T\n\nThe context of `doc.md` itself.\n") == []

    def test_a_filename_that_is_not_a_sibling_is_not_a_fault(self, tmp_path):
        body = "# T\n\nThe context of `README.md`, `SKILL.md` and `plan.md`.\n"
        assert self.shared(tmp_path, body, siblings=("slice.md",)) == []

    def test_a_longer_name_that_embeds_a_sibling_stem_is_not_a_fault(self, tmp_path):
        assert self.shared(tmp_path, "# T\n\nThe `my-slice.md` and `slice.mdx` names.\n") == []

    def test_a_core_naming_its_sibling_through_its_own_branch_load_written_from_the_shared_directory_passes(
        self, tmp_path
    ):
        files = {"skills/_shared/core.md": "# C\n\n" + BRANCH.format(p="../_shared/b.md"),
                 "skills/_shared/b.md": OPENING}
        assert faults_of(tmp_path, files) == []

    @pytest.mark.parametrize("load", [
        "When the run is unattended, read `b.md` now and follow it in full.\n",
        "Read `b.md` now and follow it in full.\n",
        "Read `b.md` now.\n\nThen the section on retries.\n",
    ])
    def test_a_shared_file_loading_its_sibling_by_bare_name_is_refused_at_the_load_line(self, tmp_path, load):
        files = {"skills/_shared/core.md": "# C\n\n" + load, "skills/_shared/b.md": OPENING}
        faults = rule_faults(faults_of(tmp_path, files), "shared-sibling-named")
        assert [f.split(": ")[0] for f in faults] == ["skills/_shared/core.md:3"]

    def test_a_shared_file_nested_below_the_shared_directory_naming_its_sibling_is_refused(self, tmp_path):
        files = {"skills/_shared/deep/doc.md": "# T\n\nThe shapes `slice.md` fixes.\n",
                 "skills/_shared/deep/slice.md": "# S\n\nText.\n"}
        faults = rule_faults(faults_of(tmp_path, files), "shared-sibling-named")
        assert [f.split(": ")[0] for f in faults] == ["skills/_shared/deep/doc.md:3"]

    def test_a_file_outside_the_shared_directory_naming_its_sibling_is_not_a_fault(self, tmp_path):
        files = {"skills/x/doc.md": "# T\n\nThe shapes `slice.md` fixes.\n", "skills/x/slice.md": "# S\n\nText.\n"}
        assert rule_faults(faults_of(tmp_path, files), "shared-sibling-named") == []

    SIBLING_LOAD = {"skills/x/SKILL.md": "# S\n\n" + BRANCH.format(p="../_shared/b.md"),
                    "skills/_shared/b.md": "Applies when the run is unattended.\n\n" + FULL.format(p="r.md"),
                    "skills/_shared/r.md": "R\n"}

    def test_a_shared_branch_file_loading_a_sibling_unconditionally_is_refused_at_its_line(self, tmp_path):
        faults = rule_faults(faults_of(tmp_path, self.SIBLING_LOAD), "shared-sibling-named")
        assert [f.split(": ")[0] for f in faults] == ["skills/_shared/b.md:3"]

    def test_the_same_shared_branch_file_without_that_load_passes(self, tmp_path):
        files = {**self.SIBLING_LOAD, "skills/_shared/b.md": OPENING}
        assert faults_of(tmp_path, files) == []

    def test_a_core_that_also_names_the_sibling_outside_the_branch_load_is_refused(self, tmp_path):
        core = "# C\n\n" + BRANCH.format(p="../_shared/b.md") + "\nThe shape `b.md` fixes.\n"
        files = {"skills/_shared/core.md": core,
                 "skills/_shared/b.md": OPENING}
        faults = faults_of(tmp_path, files)
        assert [f.split(": ")[0] for f in rule_faults(faults, "shared-sibling-named")] == [
            "skills/_shared/core.md:5"]

    def test_a_shared_file_with_an_unconditional_load_is_refused_as_a_rule_file_that_loads(self, tmp_path):
        files = {"skills/_shared/doc.md": "# T\n\n" + FULL.format(p="b.md"), "skills/_shared/b.md": "B\n"}
        assert len(rule_faults(faults_of(tmp_path, files), "rule-file-loads")) == 1

    def test_an_unreadable_shared_file_fails_closed(self, tmp_path):
        if os.name != "posix" or os.geteuid() == 0:
            pytest.skip("permission bits are not enforced for root or on non-POSIX")
        root = plugin(tmp_path, {"skills/_shared/doc.md": "# T\n"})
        (root / "skills/_shared/doc.md").chmod(0o000)
        try:
            faults = check_plugin(root)
        finally:
            (root / "skills/_shared/doc.md").chmod(0o644)
        assert len(rule_faults(faults, "unreadable-file")) == 1

    def test_a_shared_file_that_is_not_utf8_fails_closed(self, tmp_path):
        faults = faults_of(tmp_path, {"skills/_shared/doc.md": b"\xff\xfe\x00 not utf-8"})
        assert [f.split(": ")[0] for f in rule_faults(faults, "unreadable-file")] == ["skills/_shared/doc.md:1"]


class TestWalkFailsClosed:
    def test_a_symlinked_markdown_file_is_a_fault_at_its_own_path(self, tmp_path):
        root = plugin(tmp_path, {"skills/x/real.md": "A\n"})
        (root / "skills/x/a.md").symlink_to(root / "skills/x/real.md")
        faults = rule_faults(check_plugin(root), "unreadable-file")
        assert [f.split(": ")[0] for f in faults] == ["skills/x/a.md:1"]

    def test_a_symlinked_directory_is_a_fault_at_its_own_path(self, tmp_path):
        root = plugin(tmp_path, {"elsewhere/a.md": "A\n", "skills/x/real.md": "A\n"})
        (root / "skills/x/d").symlink_to(root / "elsewhere", target_is_directory=True)
        faults = rule_faults(check_plugin(root), "unreadable-file")
        assert [f.split(": ")[0] for f in faults] == ["skills/x/d:1"]

    def test_a_symlinked_markdown_file_under_agents_is_a_fault(self, tmp_path):
        root = plugin(tmp_path, {"elsewhere/a.md": "A\n", "agents/real.md": "A\n"})
        (root / "agents/a.md").symlink_to(root / "elsewhere/a.md")
        faults = rule_faults(check_plugin(root), "unreadable-file")
        assert [f.split(": ")[0] for f in faults] == ["agents/a.md:1"]

    def test_a_tree_with_no_symlinks_has_no_such_fault(self, tmp_path):
        assert faults_of(tmp_path, {"skills/x/real.md": "A\n", "agents/b.md": "B\n"}) == []

    def test_an_unreadable_directory_is_a_fault_at_its_own_path(self, tmp_path):
        if os.name != "posix" or os.geteuid() == 0:
            pytest.skip("permission bits are not enforced for root or on non-POSIX")
        root = plugin(tmp_path, {"skills/x/d/a.md": "A\n", "skills/x/real.md": "A\n"})
        (root / "skills/x/d").chmod(0o000)
        try:
            faults = rule_faults(check_plugin(root), "unreadable-file")
        finally:
            (root / "skills/x/d").chmod(0o755)
        assert [f.split(": ")[0] for f in faults] == ["skills/x/d:1"]


class TestRuleFilesLoadNothingUnconditionally:
    def test_a_non_skill_file_with_an_unconditional_load_is_refused(self, tmp_path):
        files = {"skills/x/SKILL.md": BRANCH.format(p="rule.md"), "skills/x/rule.md": FULL.format(p="a.md"),
                 "skills/x/a.md": "A\n"}
        files["skills/x/SKILL.md"] = FULL.format(p="rule.md")
        assert len(rule_faults(faults_of(tmp_path, files), "rule-file-loads")) == 1

    def test_a_skill_file_with_an_unconditional_load_passes(self, tmp_path):
        files = {"skills/x/SKILL.md": FULL.format(p="a.md"), "skills/x/a.md": "A\n"}
        assert faults_of(tmp_path, files) == []

    def test_a_non_skill_core_with_only_branch_loads_passes(self, tmp_path):
        files = {"skills/x/SKILL.md": FULL.format(p="core.md"), "skills/x/core.md": BRANCH.format(p="b.md"),
                 "skills/x/b.md": OPENING}
        assert faults_of(tmp_path, files) == []


class TestBranchOpening:
    def test_a_branch_load_target_opening_applies_when_passes(self, tmp_path):
        files = {"skills/x/SKILL.md": BRANCH.format(p="b.md"), "skills/x/b.md": OPENING}
        assert faults_of(tmp_path, files) == []

    def test_a_branch_load_target_opening_applies_only_when_passes(self, tmp_path):
        files = {"skills/x/SKILL.md": BRANCH.format(p="b.md"), "skills/x/b.md": "\n_Applies only when x._\n"}
        assert faults_of(tmp_path, files) == []

    def test_a_branch_load_target_opening_with_anything_else_is_refused(self, tmp_path):
        files = {"skills/x/SKILL.md": "# T\n\n" + BRANCH.format(p="b.md"), "skills/x/b.md": "# Branch\n\nSteps.\n"}
        faults = rule_faults(faults_of(tmp_path, files), "branch-opening")
        assert [f.split(": ")[0] for f in faults] == ["skills/x/SKILL.md:3"]

    def test_a_file_opening_applies_when_loaded_unconditionally_is_refused(self, tmp_path):
        files = {"skills/x/SKILL.md": "# T\n\n" + FULL.format(p="b.md"), "skills/x/b.md": OPENING}
        faults = rule_faults(faults_of(tmp_path, files), "branch-loaded-unconditionally")
        assert [f.split(": ")[0] for f in faults] == ["skills/x/SKILL.md:3"]

    def test_a_section_read_of_a_file_opening_applies_when_is_refused(self, tmp_path):
        files = {"skills/x/SKILL.md": "Read `b.md` now. Use its Steps.\n", "skills/x/b.md": OPENING}
        assert len(rule_faults(faults_of(tmp_path, files), "branch-loaded-unconditionally")) == 1


class TestLoadPaths:
    def test_a_path_written_from_the_loading_directory_to_a_regular_file_passes(self, tmp_path):
        files = {"skills/x/SKILL.md": FULL.format(p="../_shared/r.md"), "skills/_shared/r.md": "R\n"}
        assert faults_of(tmp_path, files) == []

    def test_a_missing_target_is_refused(self, tmp_path):
        faults = faults_of(tmp_path, {"skills/x/SKILL.md": "# T\n\n" + FULL.format(p="gone.md")})
        assert [f.split(": ")[0] for f in rule_faults(faults, "load-target-missing")] == ["skills/x/SKILL.md:3"]

    def test_a_symlinked_target_is_refused_though_it_points_at_a_real_file(self, tmp_path):
        root = plugin(tmp_path, {"skills/x/SKILL.md": FULL.format(p="a.md"), "skills/x/real.md": "A\n"})
        (root / "skills/x/a.md").symlink_to(root / "skills/x/real.md")
        assert len(rule_faults(check_plugin(root), "load-target-not-regular")) == 1

    def test_a_target_reached_through_a_symlinked_directory_is_refused(self, tmp_path):
        root = plugin(tmp_path, {"skills/x/SKILL.md": FULL.format(p="d/a.md"), "elsewhere/a.md": "A\n"})
        (root / "skills/x/d").symlink_to(root / "elsewhere", target_is_directory=True)
        assert len(rule_faults(check_plugin(root), "load-target-not-regular")) == 1

    def test_a_directory_named_as_a_target_is_refused(self, tmp_path):
        files = {"skills/x/SKILL.md": FULL.format(p="d"), "skills/x/d/a.md": "A\n"}
        assert len(rule_faults(faults_of(tmp_path, files), "load-target-not-regular")) == 1

    def test_a_path_escaping_the_plugin_is_refused_even_when_the_file_exists_there(self, tmp_path):
        root = plugin(tmp_path, {"skills/x/SKILL.md": FULL.format(p="../../../outside.md")})
        (tmp_path / "outside.md").write_text("O\n")
        unsafe = rule_faults(check_plugin(root), "load-path-unsafe")
        assert len(unsafe) == 1 and "leaves the plugin root" in unsafe[0]

    def test_an_absolute_path_is_refused_even_when_the_file_exists(self, tmp_path):
        target = tmp_path / "abs.md"
        target.write_text("A\n")
        faults = faults_of(tmp_path / "t", {"skills/x/SKILL.md": FULL.format(p=str(target))})
        unsafe = rule_faults(faults, "load-path-unsafe")
        assert len(unsafe) == 1 and "is an absolute path" in unsafe[0]

    def test_a_path_with_a_space_is_refused_though_the_file_exists(self, tmp_path):
        files = {"skills/x/SKILL.md": FULL.format(p="my file.md"), "skills/x/my file.md": "A\n"}
        assert len(rule_faults(faults_of(tmp_path, files), "load-path-unsafe")) == 1

    def test_a_plugins_prefixed_path_is_refused_though_the_file_exists_at_that_strict_location(self, tmp_path):
        files = {"skills/x/SKILL.md": FULL.format(p="plugins/craft/a.md"), "skills/x/plugins/craft/a.md": "A\n"}
        assert len(rule_faults(faults_of(tmp_path, files), "load-path-unsafe")) == 1

    def test_a_path_that_exists_only_through_an_ancestor_directory_is_refused(self, tmp_path):
        files = {"skills/x/SKILL.md": FULL.format(p="_shared/r.md"), "skills/_shared/r.md": "R\n"}
        assert len(rule_faults(faults_of(tmp_path, files), "load-target-missing")) == 1


class TestSplitCoreLength:
    @staticmethod
    def core(lines: int, load: bool = True) -> dict[str, str]:
        body = (BRANCH.format(p="b.md") if load else "") + "filler\n" * (lines - (1 if load else 0))
        return {"skills/x/SKILL.md": FULL.format(p="core.md"), "skills/x/core.md": body, "skills/x/b.md": OPENING}

    def test_a_core_with_a_branch_load_at_499_lines_passes(self, tmp_path):
        assert faults_of(tmp_path, self.core(499)) == []

    def test_a_core_with_a_branch_load_at_500_lines_is_refused(self, tmp_path):
        faults = rule_faults(faults_of(tmp_path, self.core(500)), "core-too-long")
        assert [f.split(": ")[0] for f in faults] == ["skills/x/core.md:1"]

    def test_a_600_line_file_with_no_branch_load_is_not_a_core(self, tmp_path):
        assert faults_of(tmp_path, self.core(600, load=False)) == []


class TestDeclaredMentions:
    FILES = {"skills/x/SKILL.md": "# T\n\nThe inventory lists `../_shared/r.md`.\n", "skills/_shared/r.md": "R\n"}

    def declare(self, tmp_path, mapping, files=None):
        return faults_of(tmp_path, {**(files or self.FILES), "declared-mentions.json": json.dumps(mapping)})

    def test_an_entry_whose_file_still_names_the_path_passes(self, tmp_path):
        assert self.declare(tmp_path, {"skills/x/SKILL.md": ["skills/_shared/r.md"]}) == []

    def test_the_same_entry_after_the_mention_is_removed_is_refused_as_stale(self, tmp_path):
        files = {**self.FILES, "skills/x/SKILL.md": "# T\n\nThe inventory lists nothing.\n"}
        faults = self.declare(tmp_path, {"skills/x/SKILL.md": ["skills/_shared/r.md"]}, files)
        assert len(rule_faults(faults, "declared-mention-stale")) == 1

    def test_an_entry_for_a_path_the_file_names_under_the_plugin_root_variable_is_not_stale(self, tmp_path):
        files = {"skills/x/SKILL.md": "# T\n\nThe template is ${CLAUDE_PLUGIN_ROOT}/templates/t.md.\n",
                 "templates/t.md": "T\n"}
        assert self.declare(tmp_path, {"skills/x/SKILL.md": ["templates/t.md"]}, files) == []

    def test_an_entry_for_a_variable_path_the_file_no_longer_names_is_refused_as_stale(self, tmp_path):
        files = {"skills/x/SKILL.md": "# T\n\nThe template is ${CLAUDE_PLUGIN_ROOT}/templates/other.md.\n",
                 "templates/t.md": "T\n", "templates/other.md": "O\n"}
        faults = self.declare(tmp_path, {"skills/x/SKILL.md": ["templates/t.md"]}, files)
        assert len(rule_faults(faults, "declared-mention-stale")) == 1

    def test_an_entry_for_a_file_that_does_not_exist_is_refused_as_stale(self, tmp_path):
        faults = self.declare(tmp_path, {"skills/x/gone.md": ["skills/_shared/r.md"]})
        assert len(rule_faults(faults, "declared-mention-stale")) == 1

    def test_an_entry_naming_a_path_the_same_file_also_loads_is_refused(self, tmp_path):
        files = {**self.FILES, "skills/x/SKILL.md": FULL.format(p="../_shared/r.md")}
        faults = self.declare(tmp_path, {"skills/x/SKILL.md": ["skills/_shared/r.md"]}, files)
        assert len(rule_faults(faults, "declared-mention-also-loaded")) == 1

    def test_an_entry_naming_a_path_another_file_loads_passes(self, tmp_path):
        files = {**self.FILES, "skills/y/SKILL.md": FULL.format(p="../_shared/r.md")}
        assert self.declare(tmp_path, {"skills/x/SKILL.md": ["skills/_shared/r.md"]}, files) == []

    MENTION = {"skills/x/SKILL.md": "# T\n\nUse the helper, whose mechanism lives in `../_shared/r.md`.\n",
               "skills/_shared/r.md": "R\n"}

    def test_a_sentence_with_a_directive_verb_naming_a_declared_mention_is_not_misworded(self, tmp_path):
        assert self.declare(tmp_path, {"skills/x/SKILL.md": ["skills/_shared/r.md"]}, self.MENTION) == []

    def test_the_same_sentence_undeclared_is_misworded(self, tmp_path):
        faults = faults_of(tmp_path, self.MENTION)
        assert len(rule_faults(faults, "misworded-load")) == 1

    def test_a_declaration_for_another_file_does_not_clear_the_sentence(self, tmp_path):
        files = {**self.MENTION, "skills/y/SKILL.md": "# T\n\nSee `../_shared/r.md` for the mechanism.\n"}
        faults = self.declare(tmp_path, {"skills/y/SKILL.md": ["skills/_shared/r.md"]}, files)
        assert [f.split(": ")[0] for f in rule_faults(faults, "misworded-load")] == ["skills/x/SKILL.md:3"]

    def test_a_declared_path_does_not_clear_a_sentence_that_also_names_an_undeclared_file(self, tmp_path):
        files = {**self.MENTION, "skills/_shared/q.md": "Q\n",
                 "skills/x/SKILL.md": "# T\n\nUse `../_shared/r.md` and `../_shared/q.md`.\n"}
        faults = self.declare(tmp_path, {"skills/x/SKILL.md": ["skills/_shared/r.md"]}, files)
        assert len(rule_faults(faults, "load-names-several-files")) == 1

    def test_a_malformed_list_is_refused(self, tmp_path):
        files = {**self.FILES, "declared-mentions.json": "{not json"}
        faults = faults_of(tmp_path, files)
        assert len(rule_faults(faults, "declared-mentions-malformed")) == 1
        assert faults[0].startswith("declared-mentions.json:1: ")

    def test_a_missing_list_is_an_empty_list(self, tmp_path):
        assert faults_of(tmp_path, self.FILES) == []


class TestCommandLine:
    def run(self, root: Path) -> subprocess.CompletedProcess:
        plugin_root = Path(_portage_cli.PLUGIN_ROOT)
        env = {**os.environ, "PYTHONPATH": str(plugin_root)}
        return subprocess.run([sys.executable, "-m", "portage.eval_gate.convention", str(root)],
                              capture_output=True, text=True, env=env, cwd=str(root.parent))

    def test_a_clean_tree_exits_zero_and_a_dirty_one_exits_one_with_a_fault_per_line(self, tmp_path):
        clean = plugin(tmp_path / "clean", {"skills/x/SKILL.md": FULL.format(p="a.md"), "skills/x/a.md": "A\n"})
        dirty = plugin(tmp_path / "dirty", {"skills/x/SKILL.md": "Read `a.md` from the top.\nSee `b.md`.\n",
                                            "skills/x/a.md": "A\n", "skills/x/b.md": "B\n"})
        ok, bad = self.run(clean), self.run(dirty)
        assert (ok.returncode, ok.stdout) == (0, "")
        assert bad.returncode == 1
        assert bad.stdout.splitlines() == check_plugin(dirty)
        assert len(bad.stdout.splitlines()) == 2
