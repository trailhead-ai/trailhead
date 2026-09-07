"""Manifest-validity tests for the craft plugin packaging.

Mirrors lore's packaging coverage: the root marketplace.json and the
plugins/craft/plugin.json must be valid JSON with the required fields, and the
marketplace `source` must resolve to the real plugin directory. `source: "."`
is rejected by Claude Code, so the plugin must live in a `plugins/craft/`
subdir referenced by `source: "./plugins/craft"`.
"""

import json
import re
import shutil
import subprocess
import sys
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


def test_scripts_directory_installs_as_a_unit_with_the_sibling_import_intact(tmp_path):
    """`candidate_set.py` imports its sibling `covers_gate.py` at runtime — the
    first cross-script import in `plugins/craft/scripts/`. `capabilities.toml`
    declares `scripts` as a whole-directory `base` entry, and trailhead's
    composer (`trailhead/compose.py`) copies a `base` entry with
    `shutil.copytree`, the whole directory as one unit, for every install.
    Reproducing that exact copy here pins the actual mechanism that keeps the
    import resolvable — a regression that drops `scripts` from `base`, or that
    moves either script out of this directory, must fail here, at test time,
    rather than post-install with a bare `ImportError`.

    `maturity_stamp.py` is the first script importing from *two* siblings
    (`covers_gate.py` and `maturity_resolve.py`) rather than one, so it is the
    case most likely to break on a partial install — this test executes the
    copied script as a subprocess, not merely checks the file is present, so a
    broken sibling import surfaces here as a real `ImportError` rather than
    passing on file existence alone.
    """
    data = tomllib.loads((REPO_ROOT / "capabilities.toml").read_text())
    assert "scripts" in data["tool"]["base"], (
        "capabilities.toml must declare 'scripts' as a base directory — without "
        "it, an install can select a subset that drops the sibling import"
    )

    dest = tmp_path / "scripts"
    shutil.copytree(PLUGIN_ROOT / "scripts", dest)

    assert (dest / "covers_gate.py").is_file()
    assert (dest / "candidate_set.py").is_file()
    assert (dest / "maturity_resolve.py").is_file()
    assert (dest / "maturity_stamp.py").is_file()

    result = subprocess.run(
        [sys.executable, str(dest / "maturity_stamp.py")],
        input=b"# X\n\n## Maturity\n\n- trailhead: production\n",
        capture_output=True,
    )
    assert result.returncode == 0, (
        "maturity_stamp.py must run cleanly from a copied-as-a-unit scripts/ "
        f"directory — stderr: {result.stderr.decode('utf-8')!r}"
    )
    assert result.stdout.decode("utf-8") == "maturity: trailhead=production\n"


# A skill document that pipes into `${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py`
# is telling an agent to execute that path directly. Without the executable
# bit the documented command exits 126 before the script's own code runs, and
# the dispatchers' "a non-zero exit refuses the dispatch" rule then turns every
# review into a refusal. Nothing else in the suite executes a script the way
# its own documentation says to.
_BARE_INVOCATION_RE = re.compile(r"CLAUDE_PLUGIN_ROOT\}/scripts/([a-z_]+\.py)")


def _bare_invoked_script_names():
    names = set()
    for path in (PLUGIN_ROOT / "skills").rglob("*.md"):
        names.update(_BARE_INVOCATION_RE.findall(path.read_text(encoding="utf-8")))
    return sorted(names)


def test_every_bare_invoked_script_is_executable():
    names = _bare_invoked_script_names()
    assert names, (
        "no bare `${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py` invocation found in "
        "any skill document — the scan itself is broken, so this test would "
        "pass vacuously"
    )
    not_executable = [
        name
        for name in names
        if not (PLUGIN_ROOT / "scripts" / name).stat().st_mode & 0o111
    ]
    assert not not_executable, (
        f"{not_executable} are invoked directly by a skill document but are not "
        "executable — the documented command exits 126 before the script runs"
    )


def test_a_bare_invoked_script_actually_runs_as_documented():
    """The mode-bit check above is a proxy; this executes one for real."""
    script = PLUGIN_ROOT / "scripts" / "maturity_bars.py"
    result = subprocess.run([str(script)], input=b"", capture_output=True)
    assert result.returncode == 0, (
        f"{script} could not be executed directly: exit {result.returncode}, "
        f"stderr={result.stderr.decode('utf-8', 'replace')[:200]}"
    )
