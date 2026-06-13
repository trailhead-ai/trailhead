"""
Tests for trailhead/compose.py — install-composition seam.

TDD: written BEFORE implementation. All must pass after trailhead/compose.py
is implemented.

Contract pinned:
  - compose_plan is PURE — touches no filesystem.
  - apply_plan is the only function that writes.
  - always-on set: .claude-plugin/ + base dirs + hooks_json (if declared).
  - selected capabilities union their skills dirs into the plan.
  - de-dup: same src→same dest referenced twice → one CopyOp, no error.
  - collision: two different src dirs resolve to same dest → CollisionError.
  - unknown capability in selected → UnknownCapabilityError.
  - D-F confinement on both src and dest sides.
  - apply_plan copy mode uses symlinks=False.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from trailhead.capabilities import ConfineError, load_manifest
from trailhead.compose import (
    CollisionError,
    CopyOp,
    Plan,
    UnknownCapabilityError,
    apply_plan,
    compose_plan,
)

_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "capabilities.toml"
    p.write_text(content)
    return p


def _make_plugin_root(tmp_path: Path, tool_name: str) -> Path:
    plugin_root = tmp_path / "plugins" / tool_name
    plugin_root.mkdir(parents=True)
    # Always create .claude-plugin/plugin.json (every valid plugin has one)
    claude_plugin = plugin_root / ".claude-plugin"
    claude_plugin.mkdir()
    (claude_plugin / "plugin.json").write_text('{"name": "' + tool_name + '"}')
    return plugin_root


def _make_skill_dir(plugin_root: Path, skill: str) -> Path:
    d = plugin_root / skill
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {skill}")
    return d


def _make_hooks_json(plugin_root: Path, hooks_path: str = "hooks/hooks.json") -> Path:
    p = plugin_root / hooks_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"hooks": []}')
    return p


# ---------------------------------------------------------------------------
# T1: composing lore with selected={capture} → correct CopyOps
# ---------------------------------------------------------------------------


class TestLoreCaptureComposePlan:
    def test_plan_returned(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        assert isinstance(plan, Plan)

    def test_plan_contains_claude_plugin(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        assert dest / ".claude-plugin" in dest_paths

    def test_plan_contains_all_base_dirs(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        for base_dir in m.base:
            assert dest / base_dir in dest_paths, f"base dir missing: {base_dir}"

    def test_plan_contains_hooks_json(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        # hooks_json is wired via its containing directory so the sibling hook
        # scripts it invokes ship too; assert the hooks dir is in the plan.
        assert dest / str(Path(m.hooks_json).parent) in dest_paths

    def test_plan_contains_all_capture_skill_dirs(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        for skill in m.capabilities["capture"]["skills"]:
            assert dest / skill in dest_paths, f"capture skill missing: {skill}"

    def test_plan_has_exactly_expected_ops_count(self, tmp_path):
        # always-on: .claude-plugin (1) + len(base) dirs + hooks_json (1)
        # capture: its declared skill dirs
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        expected = 1 + len(m.base) + len(m.capabilities["capture"]["skills"]) + 1
        assert len(plan.ops) == expected

    def test_src_paths_are_under_plugin_root(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        lore_plugin_root = _LORE_MANIFEST.parent / "plugins" / "lore"
        plan = compose_plan(m, {"capture"}, dest)
        for op in plan.ops:
            assert op.src.is_relative_to(lore_plugin_root.resolve()), (
                f"src escapes plugin_root: {op.src}"
            )

    def test_dest_paths_are_under_dest(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        for op in plan.ops:
            assert op.dest.is_relative_to(dest), (
                f"dest escapes target: {op.dest}"
            )

    def test_dest_relative_structure_preserved(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        lore_plugin_root = (_LORE_MANIFEST.parent / "plugins" / "lore").resolve()
        for op in plan.ops:
            rel = op.src.relative_to(lore_plugin_root)
            assert op.dest == dest / rel, (
                f"dest path mismatch: {op.dest} != {dest / rel}"
            )


# ---------------------------------------------------------------------------
# T2: de-dup — benign overlap (same src→same dest listed twice) → one CopyOp
# ---------------------------------------------------------------------------


class TestDedupBenignOverlap:
    def test_same_src_in_base_and_capability_deduped(self, tmp_path):
        """A capability re-listing a base dir (same src) → only one CopyOp."""
        plugin_root = _make_plugin_root(tmp_path, "mytool")
        _make_skill_dir(plugin_root, "skills/shared")

        content = """
[tool]
name = "mytool"
base = ["skills/shared"]
validate = false

[capabilities.cap]
description = "re-lists the base dir"
skills = ["skills/shared"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"cap"}, dest)

        dest_for_shared = dest / "skills/shared"
        matching = [op for op in plan.ops if op.dest == dest_for_shared]
        assert len(matching) == 1, (
            f"expected 1 CopyOp for shared dir, got {len(matching)}"
        )

    def test_same_src_listed_by_two_capabilities_deduped(self, tmp_path):
        """Two capabilities both listing same dir → one CopyOp."""
        plugin_root = _make_plugin_root(tmp_path, "mytool")
        _make_skill_dir(plugin_root, "skills/shared")

        content = """
[tool]
name = "mytool"
base = []
validate = false

[capabilities.cap_a]
description = "uses shared"
skills = ["skills/shared"]
agents = []

[capabilities.cap_b]
description = "also uses shared"
skills = ["skills/shared"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"cap_a", "cap_b"}, dest)

        dest_for_shared = dest / "skills/shared"
        matching = [op for op in plan.ops if op.dest == dest_for_shared]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# T3: collision — two different src dirs → same dest → CollisionError (pure phase)
# ---------------------------------------------------------------------------


class TestCollisionDetection:
    def test_collision_raises_before_any_write(self, tmp_path):
        """Different src dirs resolving to same dest → CollisionError, pure phase."""
        plugin_root = _make_plugin_root(tmp_path, "mytool")
        # Create two distinct dirs that the manifest sees as different entries
        # but we'll engineer a dest collision by having two caps each contributing
        # a skill that ends up at the same dest path but from different srcs.
        # We achieve this by making the manifest reference two separate real dirs
        # under different subdirs, but both map to `skills/collide` under dest.
        # Actually the simpler way: craft a situation where base + capability
        # map different real directories to the same relative dest. We can't
        # change the relative-path mapping logic, so we need a different approach.
        #
        # The collision scenario: two capabilities list skills at DIFFERENT source
        # paths but both resolve to the SAME relative path under dest. This can
        # happen if the manifest contains the same relative dest path from two
        # different resolved sources — which compose_plan must detect.
        #
        # Since compose_plan maps src→dest via relative path (same relative),
        # a genuine collision can only occur if two distinct absolute src paths
        # happen to share the same relative path under plugin_root — which is
        # structurally impossible with well-formed manifests.
        #
        # We therefore test the collision via a crafted scenario: pass two separate
        # manifests' worth of entries in a single manifest that declares the same
        # relative path for two distinct real paths. Since the relative path IS the
        # dest key, we must test CollisionError via the programmatic API directly,
        # passing a crafted plan or by producing a scenario where different plugin
        # src roots yield the same dest path.
        #
        # The correct scenario: compose_plan for a SINGLE manifest cannot produce
        # a genuine collision from well-formed data (same relative path = same src
        # after confinement). So we test by directly invoking compose_plan with a
        # manifest that has been manipulated to expose the guard — specifically by
        # making two CopyOps with same dest but different src. We test this at the
        # collision-detection internal level by creating such a plan manually.
        #
        # Actually: re-read the spec — "two different sources resolve to the same
        # dest path". The most natural case: a future compose_plan that merges
        # two manifest outputs. For NOW, test via a synthetic Plan with a collision.
        from trailhead.compose import _detect_collisions

        src_a = tmp_path / "src_a"
        src_b = tmp_path / "src_b"
        src_a.mkdir()
        src_b.mkdir()
        dest_shared = tmp_path / "dest" / "skills" / "x"

        ops = [
            CopyOp(src=src_a, dest=dest_shared),
            CopyOp(src=src_b, dest=dest_shared),
        ]
        with pytest.raises(CollisionError) as exc_info:
            _detect_collisions(ops)
        err = exc_info.value
        assert err.dest == dest_shared
        assert {err.src_a, err.src_b} == {src_a, src_b}

    def test_collision_names_dest_and_both_sources(self, tmp_path):
        """CollisionError message must name dest + both src paths."""
        from trailhead.compose import _detect_collisions

        dest_conflict = tmp_path / "dest" / "x"
        src_a = tmp_path / "a"
        src_b = tmp_path / "b"
        ops = [
            CopyOp(src=src_a, dest=dest_conflict),
            CopyOp(src=src_b, dest=dest_conflict),
        ]
        with pytest.raises(CollisionError) as exc_info:
            _detect_collisions(ops)
        msg = str(exc_info.value)
        assert str(dest_conflict) in msg

    def test_no_collision_with_same_src(self, tmp_path):
        """Same src→same dest (benign overlap) must NOT raise."""
        from trailhead.compose import _detect_collisions

        src = tmp_path / "src"
        dest = tmp_path / "dest" / "x"
        ops = [
            CopyOp(src=src, dest=dest),
            CopyOp(src=src, dest=dest),
        ]
        # Should not raise
        _detect_collisions(ops)


# ---------------------------------------------------------------------------
# T4: dry-run / purity — compose_plan never writes
# ---------------------------------------------------------------------------


class TestComposePlanPurity:
    def test_compose_plan_does_not_create_dest_dir(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "never_created"
        assert not dest.exists()
        compose_plan(m, {"capture"}, dest)
        assert not dest.exists()

    def test_compose_plan_does_not_create_any_files(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        before = set(tmp_path.rglob("*"))
        compose_plan(m, set(), dest)
        after = set(tmp_path.rglob("*"))
        assert before == after, f"compose_plan created files: {after - before}"


# ---------------------------------------------------------------------------
# T5: empty selection → only always-on set (setup-time gating property)
# ---------------------------------------------------------------------------


class TestEmptySelection:
    def test_empty_selection_contains_claude_plugin(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, set(), dest)
        dest_paths = {op.dest for op in plan.ops}
        assert dest / ".claude-plugin" in dest_paths

    def test_empty_selection_contains_base_dirs(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, set(), dest)
        dest_paths = {op.dest for op in plan.ops}
        for base_dir in m.base:
            assert dest / base_dir in dest_paths

    def test_empty_selection_contains_hooks_json(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, set(), dest)
        dest_paths = {op.dest for op in plan.ops}
        # Wired via the containing hooks dir (see test_plan_contains_hooks_json).
        assert dest / str(Path(m.hooks_json).parent) in dest_paths

    def test_empty_selection_contains_no_capability_dirs(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, set(), dest)
        # Capability-only dirs: those NOT in base, NOT .claude-plugin, NOT the hooks dir.
        always_on_rel = (
            {".claude-plugin"}
            | set(m.base)
            | ({str(Path(m.hooks_json).parent)} if m.hooks_json else set())
        )
        for op in plan.ops:
            rel = str(op.dest.relative_to(dest))
            assert rel in always_on_rel, (
                f"unexpected dir in empty-selection plan: {rel}"
            )

    def test_empty_selection_exact_count(self, tmp_path):
        # .claude-plugin (1) + len(base) + hooks_json (1)
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, set(), dest)
        assert len(plan.ops) == 1 + len(m.base) + 1


# ---------------------------------------------------------------------------
# T6: unknown capability name → UnknownCapabilityError
# ---------------------------------------------------------------------------


class TestUnknownCapabilityError:
    def test_unknown_capability_raises_named_error(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        with pytest.raises(UnknownCapabilityError) as exc_info:
            compose_plan(m, {"nonexistent"}, dest)
        assert "nonexistent" in str(exc_info.value)

    def test_valid_plus_unknown_capability_raises(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        with pytest.raises(UnknownCapabilityError):
            compose_plan(m, {"capture", "bogus"}, dest)


# ---------------------------------------------------------------------------
# T7: D-F confinement — src escape and dest escape
# ---------------------------------------------------------------------------


class TestConfinementBothEnds:
    def test_manifest_traversal_in_capability_raises_confine_error(self, tmp_path):
        """A manifest with ../escape in skills raises ConfineError during load,
        which means compose_plan never sees it. Verify that load_manifest raises."""
        content = """
[tool]
name = "badtool"
base = []

[capabilities.bad]
description = "attempts escape"
skills = ["../../../etc/passwd"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_manifest_absolute_path_raises_confine_error(self, tmp_path):
        """Absolute path in skills raises ConfineError during load."""
        content = """
[tool]
name = "badtool"
base = []

[capabilities.bad]
description = "absolute injection"
skills = ["/etc/passwd"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_dest_escape_via_symlink_not_followed(self, tmp_path):
        """Symlinks inside src that would escape plugin_root are NOT copied
        as symlinks (symlinks=False guarantee). After apply_plan, the dest
        should not contain a live symlink pointing outside dest."""
        plugin_root = _make_plugin_root(tmp_path, "mytool")
        skill_dir = plugin_root / "skills" / "tricky"
        skill_dir.mkdir(parents=True)
        # Create a symlink inside the skill dir that points outside
        escape_target = tmp_path / "outside"
        escape_target.mkdir()
        (escape_target / "secret.txt").write_text("secret")
        link = skill_dir / "escape_link"
        link.symlink_to(escape_target)

        content = """
[tool]
name = "mytool"
base = []
validate = false

[capabilities.tricky]
description = "has symlink"
skills = ["skills/tricky"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"tricky"}, dest)
        apply_plan(plan, mode="copy")

        copied_link = dest / "skills" / "tricky" / "escape_link"
        # symlinks=False: the symlink target's CONTENTS should be copied, not
        # a dangling/escaping symlink
        assert not copied_link.is_symlink(), (
            "symlink was preserved in dest — symlinks=False not honored"
        )


# ---------------------------------------------------------------------------
# T8: apply round-trip + U3 spike — structural validity
# ---------------------------------------------------------------------------


class TestApplyRoundTripU3:
    def test_apply_creates_dest_dir(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "composed"
        plan = compose_plan(m, {"capture"}, dest)
        apply_plan(plan, mode="copy")
        assert dest.exists()

    def test_u3_claude_plugin_json_exists(self, tmp_path):
        """U3 spike: composed dest has .claude-plugin/plugin.json → structurally valid plugin."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "composed"
        plan = compose_plan(m, {"capture"}, dest)
        apply_plan(plan, mode="copy")
        plugin_json = dest / ".claude-plugin" / "plugin.json"
        assert plugin_json.exists(), ".claude-plugin/plugin.json missing after compose"

    def test_u3_plugin_json_parses_as_json(self, tmp_path):
        """U3 spike: plugin.json is valid JSON."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "composed"
        plan = compose_plan(m, {"capture"}, dest)
        apply_plan(plan, mode="copy")
        plugin_json = dest / ".claude-plugin" / "plugin.json"
        parsed = json.loads(plugin_json.read_text())
        assert isinstance(parsed, dict)

    def test_u3_capture_skill_dirs_exist_with_content(self, tmp_path):
        """U3 spike: selected skill dirs exist under dest with content."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "composed"
        plan = compose_plan(m, {"capture"}, dest)
        apply_plan(plan, mode="copy")
        for skill in m.capabilities["capture"]["skills"]:
            skill_dest = dest / skill
            assert skill_dest.exists(), f"skill dir missing after compose: {skill}"
            assert skill_dest.is_dir(), f"skill path is not a dir: {skill}"
            # Should have at least some content (not an empty shell)
            contents = list(skill_dest.iterdir())
            assert len(contents) > 0, f"skill dir is empty after compose: {skill}"

    def test_u3_base_dirs_exist_after_apply(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "composed"
        plan = compose_plan(m, set(), dest)
        apply_plan(plan, mode="copy")
        for base_dir in m.base:
            assert (dest / base_dir).is_dir(), f"base dir missing: {base_dir}"

    def test_u3_hooks_json_copied(self, tmp_path):
        """hooks_json file must be present under dest after apply."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "composed"
        plan = compose_plan(m, set(), dest)
        apply_plan(plan, mode="copy")
        assert (dest / m.hooks_json).is_file()

    def test_plan_then_apply_is_idempotent_without_error(self, tmp_path):
        """Plan is pure; applying twice (to a clean dest each time) works."""
        m = load_manifest(_LORE_MANIFEST)
        for i in range(2):
            dest = tmp_path / f"dest_{i}"
            plan = compose_plan(m, {"capture"}, dest)
            apply_plan(plan, mode="copy")
            assert (dest / ".claude-plugin" / "plugin.json").exists()

    def test_apply_excludes_pycache_build_cruft(self, tmp_path):
        """copytree must not ship __pycache__/*.pyc into the install.

        Regression: wiring directories (e.g. the hooks/ dir, which sits beside
        .py scripts) copies the source tree verbatim. Without an ignore filter a
        stray __pycache__ would land in the user's install. Real source content
        (.py / .sh / .md) must still ship.
        """
        plugin_root = _make_plugin_root(tmp_path, "mytool")
        skill_dir = plugin_root / "skills" / "withcruft"
        pycache = skill_dir / "__pycache__"
        pycache.mkdir(parents=True)
        (pycache / "mod.cpython-313.pyc").write_bytes(b"\x00cruft")
        (skill_dir / "helper.py").write_text("x = 1\n")
        (skill_dir / "loose.pyc").write_bytes(b"\x00loose")

        content = """
[tool]
name = "mytool"
base = ["skills/withcruft"]
validate = false
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        dest = tmp_path / "composed"
        apply_plan(compose_plan(m, set(), dest), mode="copy")

        landed = dest / "skills" / "withcruft"
        assert (landed / "helper.py").is_file(), "real source must still ship"
        assert not (landed / "__pycache__").exists(), "__pycache__ must not ship"
        assert not (landed / "loose.pyc").exists(), "stray .pyc must not ship"


# ---------------------------------------------------------------------------
# T9: copy mode symlinks=False
# ---------------------------------------------------------------------------


class TestCopyModeSymlinksOff:
    def test_symlink_in_src_not_preserved_as_symlink_in_dest(self, tmp_path):
        """shutil.copytree with symlinks=False resolves symlinks; verify."""
        plugin_root = _make_plugin_root(tmp_path, "mytool")
        skill_dir = plugin_root / "skills" / "withlink"
        skill_dir.mkdir(parents=True)
        # Create a real file and a symlink to it inside the skill dir
        real_file = skill_dir / "real.txt"
        real_file.write_text("content")
        link_file = skill_dir / "linked.txt"
        link_file.symlink_to(real_file)

        content = """
[tool]
name = "mytool"
base = []
validate = false

[capabilities.withlink]
description = "has internal symlink"
skills = ["skills/withlink"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"withlink"}, dest)
        apply_plan(plan, mode="copy")

        dest_link = dest / "skills" / "withlink" / "linked.txt"
        assert dest_link.exists(), "linked.txt should exist in dest"
        assert not dest_link.is_symlink(), (
            "linked.txt should be a regular file in dest (symlinks=False)"
        )
        assert dest_link.read_text() == "content"


# ---------------------------------------------------------------------------
# T10: hooks_json absent → not included in plan
# ---------------------------------------------------------------------------


class TestNoHooksJson:
    def test_manifest_without_hooks_json_omits_it_from_plan(self, tmp_path):
        plugin_root = _make_plugin_root(tmp_path, "notool")
        _make_skill_dir(plugin_root, "skills/base-skill")

        content = """
[tool]
name = "notool"
base = ["skills/base-skill"]
validate = false

[capabilities.cap]
description = "a capability"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        assert m.hooks_json is None
        dest = tmp_path / "dest"
        plan = compose_plan(m, set(), dest)
        dest_paths = {op.dest for op in plan.ops}
        # Should not contain any path where "hooks" appears in the part relative to dest
        for op_dest in dest_paths:
            rel = op_dest.relative_to(dest)
            assert "hooks" not in str(rel), (
                f"unexpected hooks path in plan without hooks_json: {rel}"
            )
