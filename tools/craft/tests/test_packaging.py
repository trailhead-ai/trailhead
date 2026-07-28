"""Manifest-validity tests for the craft plugin packaging.

Mirrors lore's packaging coverage: the root marketplace.json and the
plugins/craft/plugin.json must be valid JSON with the required fields, and the
marketplace `source` must resolve to the real plugin directory. `source: "."`
is rejected by Claude Code, so the plugin must live in a `plugins/craft/`
subdir referenced by `source: "./plugins/craft"`.
"""

import json
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "craft"

# Every runtime reference to a craft template, anywhere in the prose the shipped
# skills and agents read.
_TEMPLATE_REF = re.compile(r"templates/(plan|task|spec)\.md")
_PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}/"


def test_plugin_json_parses_and_has_required_keys():
    """plugin.json is valid JSON and has name, version, description."""
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert path.exists(), f"Expected {path} to exist"
    data = json.loads(path.read_text())
    assert "name" in data, "plugin.json must have 'name'"
    assert "version" in data, "plugin.json must have 'version'"
    assert "description" in data, "plugin.json must have 'description'"
    assert data["name"] == "craft"


# NOTE: craft's per-tool .claude-plugin/marketplace.json was removed when the
# dev marketplace consolidated into the repo-root `trailhead-local` marketplace.
# The marketplace shape and the `source: "."` regression guard now live in
# trailhead/tests/test_dev_marketplace.py at the monorepo level.


def test_capabilities_toml_base_includes_templates():
    """`templates` ships in craft's always-on base set.

    Without it, `${CLAUDE_PLUGIN_ROOT}/templates/*.md` never lands in the
    installed plugin, so every runtime reference to it (planning, refine)
    resolves to a missing path.
    """
    path = REPO_ROOT / "capabilities.toml"
    data = tomllib.loads(path.read_text())
    assert "templates" in data["tool"]["base"]


def test_capabilities_comment_names_every_template_reader():
    """The base comment is the "why this ships" note; a missed reader invites a trim.

    `agents/planner.md` renders all three templates, so a hand-picked selection that
    drops `templates` on the strength of an incomplete reader list breaks it.
    """
    text = (REPO_ROOT / "capabilities.toml").read_text()
    assert "agents/planner.md" in text, (
        "capabilities.toml's `templates` base comment must name agents/planner.md "
        "among the runtime readers — it renders spec.md, plan.md, and task.md"
    )


def test_template_references_resolve_through_the_plugin_root():
    """A bare `templates/plan.md` only resolves when cwd happens to be the plugin root.

    The composition repair puts `templates/` in the installed plugin; a reference that
    does not go through `${CLAUDE_PLUGIN_ROOT}` still fails to find it.
    """
    offenders: list[str] = []
    for directory in ("skills", "agents"):
        for path in sorted((PLUGIN_ROOT / directory).rglob("*.md")):
            text = path.read_text()
            for match in _TEMPLATE_REF.finditer(text):
                if not text[: match.start()].endswith(_PLUGIN_ROOT_VAR):
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{line}")
    assert not offenders, (
        "These template references are spelled bare and will not resolve unless the "
        f"reader's cwd is the plugin root — prefix each with {_PLUGIN_ROOT_VAR!r}: "
        f"{offenders}"
    )


def test_task_template_names_standalone_leaf_usage():
    """task.md's docstring names the standalone-leaf reuse of its payload shape."""
    path = PLUGIN_ROOT / "templates" / "task.md"
    assert "standalone" in path.read_text().lower()


def test_plan_template_names_standalone_leaf_usage():
    """plan.md's docstring names the standalone reuse of its Flow-out checklist."""
    path = PLUGIN_ROOT / "templates" / "plan.md"
    assert "standalone" in path.read_text().lower()
