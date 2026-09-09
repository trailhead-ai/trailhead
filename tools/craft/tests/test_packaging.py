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

import pytest

from trailhead.capabilities import load_manifest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "craft"
MANIFEST = load_manifest(REPO_ROOT / "capabilities.toml")

# Every runtime path the shipped prose tells an agent to open, and the bare form
# that only resolves when the reader's cwd happens to be the plugin root.
_PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}/"
_RUNTIME_REF = re.compile(re.escape(_PLUGIN_ROOT_VAR) + r"([A-Za-z0-9_./-]+)")
_BARE_REF = re.compile(r"(?<!/)\b(?:templates|scripts)/[A-Za-z0-9_.-]+\.(?:md|py)")
# Only a reference an agent would *run* is a hazard. Naming a script in prose
# ("the block `scripts/maturity_bars.py` renders") is ordinary writing; a bare
# path inside a fenced command block is the thing that resolves against whatever
# cwd the agent happens to have.
_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


def _shipped_prose() -> list[Path]:
    return sorted(
        [*(PLUGIN_ROOT / "skills").rglob("*.md"), *(PLUGIN_ROOT / "agents").rglob("*.md")]
    )


def _runtime_references() -> dict[str, set[str]]:
    """referenced path (relative to the plugin root) -> the documents that open it."""
    found: dict[str, set[str]] = {}
    for path in _shipped_prose():
        for ref in _RUNTIME_REF.findall(path.read_text(encoding="utf-8")):
            ref = ref.rstrip(".,)`")
            if not ref or ref.endswith("/"):
                continue
            found.setdefault(ref, set()).add(str(path.relative_to(PLUGIN_ROOT)))
    return found


def test_the_shipped_prose_names_runtime_paths_to_check():
    """Non-vacuity guard: an extraction that stops matching would leave the two
    checks below iterating over nothing."""
    assert len(_runtime_references()) >= 10, sorted(_runtime_references())


@pytest.mark.parametrize("ref", sorted(_runtime_references()), ids=lambda r: r)
def test_every_runtime_path_the_prose_opens_is_one_a_composed_install_ships(ref: str):
    """A skill telling an agent to run `${CLAUDE_PLUGIN_ROOT}/scripts/foo.py` needs two
    things to be true of a composed install: the file exists, and its directory is in
    the manifest's always-on `base` set. A runtime input that is not in `base` is
    dropped by any selection that does not happen to pull it in, leaving the
    instruction pointing at a path that is not there.

    The `base` set comes from the real manifest loader, so shrinking `base` fails here
    by naming the reference it orphaned — rather than needing a hand-written comment
    listing every reader to stay in step.
    """
    target = MANIFEST.plugin_root / ref
    assert target.exists(), (
        f"{sorted(_runtime_references()[ref])} tell an agent to open {ref!r}, which does "
        "not exist in the plugin"
    )
    covered = [entry for entry in MANIFEST.base if ref == entry or ref.startswith(f"{entry}/")]
    assert covered, (
        f"{sorted(_runtime_references()[ref])} open {ref!r} at runtime, but no `base` entry "
        f"in craft's manifest ships it — a selection that drops it leaves the path "
        f"unresolvable. base={MANIFEST.base}"
    )


def test_no_runtime_path_is_spelled_bare():
    """A bare `templates/plan.md` resolves only when the reader's cwd happens to be
    the plugin root, which is never guaranteed. Every such reference must go through
    `${CLAUDE_PLUGIN_ROOT}`."""
    offenders: list[str] = []
    for path in _shipped_prose():
        text = path.read_text(encoding="utf-8")
        for block in _FENCE.finditer(text):
            for match in _BARE_REF.finditer(block.group(1)):
                absolute = block.start(1) + match.start()
                if text[:absolute].endswith(_PLUGIN_ROOT_VAR):
                    continue
                line = text[:absolute].count("\n") + 1
                offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{line}: {match.group(0)}")
    assert not offenders, (
        "These runtime references are spelled bare and will not resolve unless the "
        f"reader's cwd is the plugin root — prefix each with {_PLUGIN_ROOT_VAR!r}: {offenders}"
    )


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
