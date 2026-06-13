"""Tests for trailhead/compose.py — name-based composition + overrides.

Contract pinned:
  - compose_plan is PURE (it stats override paths but writes nothing).
  - apply_plan is the only function that writes.
  - always-on set: .claude-plugin/ + base dirs + hooks dir (if declared).
  - selected subagents -> agents/<name>.md file copies.
  - selected skills (in-repo) -> skills/<name>/ dir copies.
  - override file_path: file -> skills/<name>/SKILL.md (or agents/<name>.md);
    dir -> skills/<name>/ whole tree. Src confinement is skipped for overrides
    (they deliberately point outside the repo); dest confinement is always kept.
  - de-dup: same src->same dest twice -> one CopyOp; collision: different src ->
    same dest -> CollisionError (pure phase).
  - unknown non-override name -> UnknownSubagentError / UnknownSkillError.
  - missing override path -> OverrideError.
"""

import json
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import (
    CollisionError,
    CopyOp,
    DestConfinementError,
    OverrideError,
    Plan,
    UnknownSkillError,
    UnknownSubagentError,
    _detect_collisions,
    apply_plan,
    compose_plan,
)

_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"


# ---------------------------------------------------------------------------
# Helpers — synthetic plugin trees (new manifest schema, no capability tables)
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "capabilities.toml"
    p.write_text(content)
    return p


def _make_plugin_root(tmp_path: Path, tool_name: str) -> Path:
    plugin_root = tmp_path / "plugins" / tool_name
    plugin_root.mkdir(parents=True)
    claude_plugin = plugin_root / ".claude-plugin"
    claude_plugin.mkdir()
    (claude_plugin / "plugin.json").write_text(json.dumps({"name": tool_name}))
    return plugin_root


def _make_skill(plugin_root: Path, name: str) -> Path:
    d = plugin_root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n")
    return d


def _make_agent(plugin_root: Path, name: str) -> Path:
    d = plugin_root / "agents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(f"---\nname: {name}\n---\n")
    return p


def _basic_manifest(tmp_path: Path, tool: str, *, base: list[str] | None = None) -> Path:
    base_line = f"base = {base!r}\n" if base else ""
    return _write_manifest(
        tmp_path, f'[tool]\nname = "{tool}"\nvalidate = false\n{base_line}'
    )


# ---------------------------------------------------------------------------
# Always-on set (real lore manifest, empty selection)
# ---------------------------------------------------------------------------


class TestAlwaysOnSet:
    def test_returns_plan(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        assert isinstance(compose_plan(m, {}, {}, tmp_path / "dest"), Plan)

    def test_contains_claude_plugin(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        dests = {op.dest for op in compose_plan(m, {}, {}, dest).ops}
        assert dest / ".claude-plugin" in dests

    def test_contains_base_and_hooks_dir(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        dests = {op.dest for op in compose_plan(m, {}, {}, dest).ops}
        for b in m.base:
            assert dest / b in dests
        assert dest / str(Path(m.hooks_json).parent) in dests

    def test_empty_selection_exact_count(self, tmp_path):
        # .claude-plugin (1) + len(base) + hooks dir (1)
        m = load_manifest(_LORE_MANIFEST)
        plan = compose_plan(m, {}, {}, tmp_path / "dest")
        assert len(plan.ops) == 1 + len(m.base) + 1

    def test_no_selectable_dirs_in_empty_plan(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        always_on = (
            {".claude-plugin"}
            | set(m.base)
            | ({str(Path(m.hooks_json).parent)} if m.hooks_json else set())
        )
        for op in compose_plan(m, {}, {}, dest).ops:
            assert str(op.dest.relative_to(dest)) in always_on


# ---------------------------------------------------------------------------
# Name-based selection (real lore manifest)
# ---------------------------------------------------------------------------


class TestNameSelection:
    def test_selected_skill_dir_in_plan(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        dests = {op.dest for op in compose_plan(m, {}, {"decision": None}, dest).ops}
        assert dest / "skills" / "decision" in dests

    def test_selected_subagent_file_in_plan(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        dests = {op.dest for op in compose_plan(m, {"librarian": None}, {}, dest).ops}
        assert dest / "agents" / "librarian.md" in dests

    def test_src_under_plugin_root_for_in_repo(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        root = (_LORE_MANIFEST.parent / "plugins" / "lore").resolve()
        plan = compose_plan(m, {"librarian": None}, {"decision": None}, tmp_path / "d")
        for op in plan.ops:
            assert op.src.is_relative_to(root)

    def test_unknown_skill_raises(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        with pytest.raises(UnknownSkillError, match="nope"):
            compose_plan(m, {}, {"nope": None}, tmp_path / "d")

    def test_unknown_subagent_raises(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        with pytest.raises(UnknownSubagentError, match="nope"):
            compose_plan(m, {"nope": None}, {}, tmp_path / "d")


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


class TestOverrides:
    def test_subagent_file_override_lands_at_canonical_path(self, tmp_path):
        _make_plugin_root(tmp_path, "t")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        custom = tmp_path / "outside" / "custom.md"
        custom.parent.mkdir()
        custom.write_text("custom agent")
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"librarian": str(custom)}, {}, dest)
        apply_plan(plan, mode="copy")
        landed = dest / "agents" / "librarian.md"
        assert landed.read_text() == "custom agent"

    def test_skill_file_override_lands_as_skill_md(self, tmp_path):
        _make_plugin_root(tmp_path, "t")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        custom = tmp_path / "outside" / "SKILL.md"
        custom.parent.mkdir()
        custom.write_text("custom skill")
        dest = tmp_path / "dest"
        plan = compose_plan(m, {}, {"resolve": str(custom)}, dest)
        apply_plan(plan, mode="copy")
        assert (dest / "skills" / "resolve" / "SKILL.md").read_text() == "custom skill"

    def test_skill_dir_override_copies_whole_tree(self, tmp_path):
        _make_plugin_root(tmp_path, "t")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        custom = tmp_path / "outside" / "myskill"
        custom.mkdir(parents=True)
        (custom / "SKILL.md").write_text("body")
        (custom / "helper.md").write_text("helper")
        dest = tmp_path / "dest"
        plan = compose_plan(m, {}, {"resolve": str(custom)}, dest)
        apply_plan(plan, mode="copy")
        assert (dest / "skills" / "resolve" / "SKILL.md").read_text() == "body"
        assert (dest / "skills" / "resolve" / "helper.md").read_text() == "helper"

    def test_override_path_outside_repo_is_allowed(self, tmp_path):
        # Src confinement is skipped for overrides — an absolute path outside the
        # plugin root must compose without ConfineError.
        _make_plugin_root(tmp_path, "t")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        custom = tmp_path / "elsewhere" / "x.md"
        custom.parent.mkdir()
        custom.write_text("x")
        # Must not raise:
        compose_plan(m, {"librarian": str(custom)}, {}, tmp_path / "dest")

    def test_missing_override_raises(self, tmp_path):
        _make_plugin_root(tmp_path, "t")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        with pytest.raises(OverrideError):
            compose_plan(m, {"librarian": str(tmp_path / "ghost.md")}, {}, tmp_path / "d")


# ---------------------------------------------------------------------------
# De-dup / collision
# ---------------------------------------------------------------------------


class TestDedupCollision:
    def test_collision_detected(self, tmp_path):
        src_a, src_b = tmp_path / "a", tmp_path / "b"
        src_a.mkdir(); src_b.mkdir()
        shared = tmp_path / "dest" / "x"
        with pytest.raises(CollisionError) as exc:
            _detect_collisions([CopyOp(src_a, shared), CopyOp(src_b, shared)])
        assert {exc.value.src_a, exc.value.src_b} == {src_a, src_b}

    def test_same_src_no_collision(self, tmp_path):
        src = tmp_path / "a"
        d = tmp_path / "dest" / "x"
        _detect_collisions([CopyOp(src, d), CopyOp(src, d)])  # no raise

    def test_base_relisted_as_skill_dedups(self, tmp_path):
        # A skill dir also listed in base resolves to the same src+dest -> one op.
        root = _make_plugin_root(tmp_path, "t")
        _make_skill(root, "shared")
        m = load_manifest(_basic_manifest(tmp_path, "t", base=["skills/shared"]))
        dest = tmp_path / "dest"
        # 'shared' is base (always-on) and not selectable; selecting it would be
        # UnknownSkillError, so it appears once via base only.
        plan = compose_plan(m, {}, {}, dest)
        matching = [op for op in plan.ops if op.dest == dest / "skills" / "shared"]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


class TestPurity:
    def test_compose_plan_creates_nothing(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        before = set(tmp_path.rglob("*"))
        compose_plan(m, {"librarian": None}, {"decision": None}, tmp_path / "dest")
        assert set(tmp_path.rglob("*")) == before


# ---------------------------------------------------------------------------
# apply round-trip + cruft exclusion + symlinks
# ---------------------------------------------------------------------------


class TestApply:
    def test_roundtrip_structural_validity(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "composed"
        apply_plan(compose_plan(m, {}, {"decision": None}, dest), mode="copy")
        plugin_json = dest / ".claude-plugin" / "plugin.json"
        assert json.loads(plugin_json.read_text())["name"] == "lore"
        assert (dest / "skills" / "decision").is_dir()
        assert (dest / m.hooks_json).is_file()

    def test_excludes_pycache_cruft(self, tmp_path):
        root = _make_plugin_root(tmp_path, "t")
        skill = root / "skills" / "withcruft"
        (skill / "__pycache__").mkdir(parents=True)
        (skill / "__pycache__" / "m.cpython-313.pyc").write_bytes(b"\x00")
        (skill / "SKILL.md").write_text("ok")
        (skill / "helper.py").write_text("x = 1\n")
        (skill / "loose.pyc").write_bytes(b"\x00")
        m = load_manifest(_basic_manifest(tmp_path, "t", base=["skills/withcruft"]))
        dest = tmp_path / "composed"
        apply_plan(compose_plan(m, {}, {}, dest), mode="copy")
        landed = dest / "skills" / "withcruft"
        assert (landed / "helper.py").is_file()
        assert not (landed / "__pycache__").exists()
        assert not (landed / "loose.pyc").exists()

    def test_symlink_not_preserved(self, tmp_path):
        root = _make_plugin_root(tmp_path, "t")
        skill = root / "skills" / "withlink"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("s")
        (skill / "real.txt").write_text("content")
        (skill / "linked.txt").symlink_to(skill / "real.txt")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        dest = tmp_path / "dest"
        apply_plan(compose_plan(m, {}, {"withlink": None}, dest), mode="copy")
        dest_link = dest / "skills" / "withlink" / "linked.txt"
        assert dest_link.exists() and not dest_link.is_symlink()
        assert dest_link.read_text() == "content"


# ---------------------------------------------------------------------------
# Dest confinement (override can't escape dest)
# ---------------------------------------------------------------------------


class TestDestConfinement:
    def test_override_cannot_escape_dest(self, tmp_path):
        # A skill name containing traversal would push dest outside the target —
        # _confine_dest must reject it. (Names come from config; guard anyway.)
        _make_plugin_root(tmp_path, "t")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        custom = tmp_path / "x.md"
        custom.write_text("x")
        with pytest.raises(DestConfinementError):
            compose_plan(m, {}, {"../../escape": str(custom)}, tmp_path / "dest")


# ---------------------------------------------------------------------------
# No hooks_json
# ---------------------------------------------------------------------------


class TestNoHooksJson:
    def test_plan_omits_hooks_when_absent(self, tmp_path):
        root = _make_plugin_root(tmp_path, "t")
        _make_skill(root, "only")
        m = load_manifest(_basic_manifest(tmp_path, "t"))
        assert m.hooks_json is None
        dest = tmp_path / "dest"
        for op in compose_plan(m, {}, {"only": None}, dest).ops:
            assert "hooks" not in str(op.dest.relative_to(dest))
