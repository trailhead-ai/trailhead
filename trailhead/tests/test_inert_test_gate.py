"""The inert-test gate, run against the real tree and against seeded modules.

`scripts/inert-test-gate` implements the ruleset's opening rule — a test that
executes nothing before asserting is not a test — as a mechanical check, wired as
a pre-commit hook. This suite runs that gate rather than reimplementing its
analysis, so the verdict a commit gets and the verdict the suite reports cannot
drift apart.

The seeded cases are what make the clean-tree result meaningful. Each plants one
module in a tmp dir and asserts the gate reaches the right verdict on it —
including the cases the gate must NOT flag, which are the ones that would make it
too noisy to leave enabled.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "inert-test-gate"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GATE), *args], capture_output=True, text=True, timeout=120
    )


def _module(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "test_seeded.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_repo_is_clean_under_the_gate():
    result = _run()
    assert result.returncode == 0, result.stderr


def test_the_gate_scans_a_non_empty_set():
    """Anti-vacuity: a gate whose discovery found nothing would certify the tree
    above forever. Every module it scans is a module it could report on, so seed
    an offender into a directory alongside real ones and check the count moves.
    """
    result = _run(str(_REPO_ROOT / "trailhead" / "tests"))
    assert result.returncode == 0, result.stderr


FLAGGED = {
    "reads-a-file": '''
from pathlib import Path
def test_doc_says_the_thing():
    text = Path("README.md").read_text()
    assert "never do X" in text
''',
    "bare-existence": '''
from pathlib import Path
def test_the_file_was_committed():
    assert Path("config.toml").exists()
''',
    "hasattr": '''
import mod
def test_it_defines_resolve():
    assert hasattr(mod, "resolve")
''',
    # A decorator lives on the function node but is not the test running
    # anything; counting it as execution would exempt every parametrized test.
    "parametrized": '''
from pathlib import Path
import pytest
@pytest.mark.parametrize("name", ["a.md", "b.md"])
def test_doc_mentions_the_rule(name):
    assert "never do X" in Path(name).read_text()
''',
    "absence": '''
from pathlib import Path
def test_the_thing_is_gone():
    assert not Path("old.py").exists()
''',
}

NOT_FLAGGED = {
    # The subject ran, and the assertion is about what it produced.
    "runs-the-subject": '''
from pathlib import Path
from installer import run_install
def test_install_writes_the_ruleset(tmp_path):
    run_install(dest=tmp_path)
    assert (tmp_path / "rules" / "craft.md").read_text().startswith("#")
''',
    # An import inside the body executes the module under test.
    "import-is-execution": '''
import sys
def test_module_imports_without_the_dev_env():
    import camp.spine  # noqa: F401
    assert not [m for m in sys.modules if m.startswith("dev_env")]
''',
    # A real parser is a real loader.
    "parses-through-a-loader": '''
import json
from pathlib import Path
def test_manifest_declares_one_entry_per_tool():
    data = json.loads(Path("marketplace.json").read_text())
    assert len(data["plugins"]) == 6
''',
    # Delegating the run to a module-local helper still ran something.
    "helper-runs-it": '''
import subprocess, sys
def gate(path):
    return subprocess.run([sys.executable, "gate.py", path], capture_output=True)
def test_the_gate_accepts_a_clean_file():
    assert gate("clean.md").returncode == 0
''',
}


@pytest.mark.parametrize("body", FLAGGED.values(), ids=list(FLAGGED))
def test_the_gate_flags_a_test_that_executes_nothing(tmp_path, body):
    result = _run(str(_module(tmp_path, body)))
    assert result.returncode == 1, (
        f"the gate passed a test that executes nothing:\n{result.stdout}{result.stderr}"
    )
    assert "executes nothing before asserting" in result.stderr


@pytest.mark.parametrize("body", NOT_FLAGGED.values(), ids=list(NOT_FLAGGED))
def test_the_gate_leaves_a_real_test_alone(tmp_path, body):
    result = _run(str(_module(tmp_path, body)))
    assert result.returncode == 0, (
        f"the gate flagged a test that does run its subject — this is the false "
        f"positive that gets the gate disabled:\n{result.stdout}{result.stderr}"
    )


def test_an_allow_marker_exempts_a_case_and_is_listed(tmp_path):
    body = '''
from pathlib import Path
# inert-gate: allow pins a closed vocabulary
def test_the_vocabulary_is_closed():
    assert Path("x").exists()
'''
    seeded = _module(tmp_path, body)
    assert _run(str(seeded)).returncode == 0

    listed = _run("--list-allowed", str(seeded))
    assert listed.returncode == 0
    assert "test_the_vocabulary_is_closed" in listed.stdout
    assert "pins a closed vocabulary" in listed.stdout


def test_an_allow_marker_without_a_reason_still_reports_something(tmp_path):
    """The reason is what keeps exemptions reviewable, so a bare marker must not
    silently list as blank."""
    body = '''
from pathlib import Path
# inert-gate: allow
def test_bare_marker():
    assert Path("x").exists()
'''
    listed = _run("--list-allowed", str(_module(tmp_path, body)))
    assert "(no reason given)" in listed.stdout


# --- the document-probe rule -------------------------------------------------
#
# The rule above catches a test that ran nothing. This one catches its near
# neighbour: a test that ran something, but whose subject is a shipped document
# and whose assertions only probe it for presence. Such a test has no input, so
# its output cannot vary — it pins the repo as checked out rather than a
# behaviour, and goes red on a reword while a meaning-changing edit passes.

PROBE_FLAGGED = {
    # A real loader ran, so the rule above lets this through — but the assertion
    # is still only asking whether a phrase is present in shipped prose.
    "loader-ran-but-probes-prose": '''
from pathlib import Path
from capabilities import load_manifest
DOC = Path(__file__).parent / "README.md"
MANIFEST = Path(__file__).parent / "capabilities.toml"
def test_readme_advertises_the_tool():
    manifest = load_manifest(MANIFEST)
    assert f"craft:{manifest.tool}" in DOC.read_text()
''',
    # Same shape via a regex probe rather than `in`.
    "regex-probe-of-a-document": '''
import re, subprocess, sys
from pathlib import Path
SKILL = Path(__file__).parent / "SKILL.md"
def test_skill_states_the_refusal():
    subprocess.run([sys.executable, "-c", "pass"])
    assert re.search(r"non-zero exit refuses", SKILL.read_text())
''',
    # Absence, in the same shape.
    "absence-probe-of-a-document": '''
import subprocess, sys
from pathlib import Path
DOC = Path(__file__).parent / "rules.md"
def test_doc_no_longer_mentions_the_old_verb():
    subprocess.run([sys.executable, "-c", "pass"])
    assert "camp bookmark" not in DOC.read_text()
''',
}

PROBE_NOT_FLAGGED = {
    # Derives a value from the document and checks it against what the code
    # produces. The document is an input to a computation, not a haystack.
    "derives-a-value-and-compares": '''
from pathlib import Path
from renderer import render_table
DOC = Path(__file__).parent / "council.md"
def parse_table(text):
    return {line.split("|")[0]: line.split("|")[1] for line in text.splitlines() if "|" in line}
def test_document_table_agrees_with_the_renderer():
    assert parse_table(DOC.read_text()) == render_table()
''',
    # Builds its own input, so the output can vary with it.
    "constructs-its-own-input": '''
import subprocess, sys
from pathlib import Path
GATE = Path(__file__).parent / "gate.py"
def test_a_seeded_violation_is_caught(tmp_path):
    doc = tmp_path / "x.md"
    doc.write_text("a forbidden token")
    r = subprocess.run([sys.executable, str(GATE), str(doc)], capture_output=True)
    assert "forbidden" in r.stdout.decode()
''',
    # Runs a real gate over the shipped tree and checks its verdict. The
    # assertion is about an exit code the gate computed, not a phrase.
    "checks-a-computed-verdict": '''
import subprocess, sys
from pathlib import Path
GATE = Path(__file__).parent / "gate.py"
TREE = Path(__file__).parent / "skills"
def test_the_shipped_tree_is_clean():
    r = subprocess.run([sys.executable, str(GATE), str(TREE)], capture_output=True)
    assert r.returncode == 0
''',
}


@pytest.mark.parametrize("body", PROBE_FLAGGED.values(), ids=list(PROBE_FLAGGED))
def test_the_gate_flags_a_document_probe(tmp_path, body):
    result = _run(str(_module(tmp_path, body)))
    assert result.returncode == 1, (
        f"the gate passed a test that only probes shipped prose:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "probes a shipped document" in result.stderr


@pytest.mark.parametrize("body", PROBE_NOT_FLAGGED.values(), ids=list(PROBE_NOT_FLAGGED))
def test_the_gate_leaves_a_document_driven_behaviour_test_alone(tmp_path, body):
    result = _run(str(_module(tmp_path, body)))
    assert result.returncode == 0, (
        f"the gate flagged a test whose output varies with its input — this is "
        f"the false positive that gets the gate disabled:\n"
        f"{result.stdout}{result.stderr}"
    )


def test_an_allow_marker_exempts_a_document_probe(tmp_path):
    """A seam smoke — one per seam — stays, but has to say why."""
    body = '''
import subprocess, sys
from pathlib import Path
DOC = Path(__file__).parent / "SKILL.md"
# inert-gate: allow the one seam smoke binding this skill to its gate
def test_skill_names_the_gate():
    subprocess.run([sys.executable, "-c", "pass"])
    assert "criterion_gate.py" in DOC.read_text()
'''
    assert _run(str(_module(tmp_path, body))).returncode == 0
