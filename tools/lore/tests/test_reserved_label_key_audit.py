"""Repo-wide audit: no shipped skill/agent doc instructs writing a reserved
``--label`` key.

A ``labels`` sidecar key that collides with a record kind name or a KQL field
name shadows a first-class record concept — `lore record model.KINDS` and
`lore.search.kql.VALID_FIELDS` are the reserved set. A ``related-<suffix>``
key is reserved too: it reads like a relation but is stored as an unindexed
label, silently diverging from the real ``related`` edge graph.

This is a *mechanical* guard against that pattern recurring in agent-facing
docs — the shape of instruction that once told craft's ``plan``/``polish``
skills to write ``related-subsystems`` (a phantom field with no dedicated
flag), which an agent translated into a ``--label`` write that collided with
the reserved-key convention. Scope is every shipped ``SKILL.md`` and agent
definition under ``tools/`` — the always-loaded instructions an agent acts on,
not this test suite itself.

Three shapes are scanned per line, each catching how a doc can name a label
key: an explicit ``--label KEY=VALUE`` flag example; a backtick-quoted
``related-<suffix>`` mention (the shape the stale craft docs used); and a
YAML-style ``key:`` line at the start of a line (the shape a frontmatter-style
template block uses). A key is a violation if the write-time guard would refuse
it — the audit asks ``record/model.py`` itself rather than restating its rule,
so the two can never drift.

``related-spec`` is allowlisted: it names the ``related`` edge-graph field
(written via ``--related spec=<name>``, a different flag entirely), not a
``labels`` map key — a legitimate, pre-existing pattern outside this audit's
target and this slice's scope.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
TOOLS_ROOT = TESTS_DIR.parent.parent
REPO_ROOT = TOOLS_ROOT.parent

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402

_LABEL_FLAG_RE = re.compile(r"--label\s+([^\s=]+)=")
_BACKTICK_RELATED_RE = re.compile(r"`(related-[A-Za-z][\w-]*):?`")
_YAML_KEY_RE = re.compile(r"^\s*([A-Za-z][\w./-]*):\s")

# (relative path, key) pairs that are legitimate related-* surfaces unrelated
# to the `labels` map — see module docstring.
_ALLOWLIST = {
    ("tools/craft/plugins/craft/skills/plan/SKILL.md", "related-spec"),
    ("tools/craft/plugins/craft/skills/polish/SKILL.md", "related-spec"),
    ("tools/craft/plugins/craft/skills/distill/SKILL.md", "related-spec"),
}


def _scanned_files():
    yield from TOOLS_ROOT.glob("*/plugins/*/skills/**/SKILL.md")
    yield from TOOLS_ROOT.glob("*/plugins/*/skills/_shared/*.md")
    yield from TOOLS_ROOT.glob("*/plugins/*/agents/*.md")


def _scan() -> list[str]:
    is_reserved = load_script("lore.record.model")._is_reserved_label_key

    violations: list[str] = []
    for path in sorted(_scanned_files()):
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            keys: set[str] = set()
            keys.update(m.group(1) for m in _LABEL_FLAG_RE.finditer(line))
            keys.update(m.group(1) for m in _BACKTICK_RELATED_RE.finditer(line))
            m = _YAML_KEY_RE.match(line)
            if m:
                keys.add(m.group(1))
            for key in keys:
                if not is_reserved(key):
                    continue
                if (rel, key) in _ALLOWLIST:
                    continue
                violations.append(f"{rel}:{lineno}: {key!r} — {line.strip()}")
    return violations


def test_no_shipped_skill_or_agent_instructs_reserved_label_key():
    violations = _scan()
    assert not violations, (
        "Shipped skill/agent doc(s) instruct writing a --label key that "
        "collides with a record kind, a KQL field, or a related-* shape — "
        "point at a namespaced key (e.g. 'craft/subsystems') instead:\n  "
        + "\n  ".join(violations)
    )
