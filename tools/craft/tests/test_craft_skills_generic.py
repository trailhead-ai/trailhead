"""craft's shipped skills carry no app-flavored seam token — proved by running the gate.

The structural brain seams this module used to scan for a second time
(``mcp__brain__``, ``code/brain``) are now carried by
``scripts/leak-gate-seams.denylist`` and enforced by ``scripts/leak-gate`` over
every tool's shippable surface on every commit, through
``.pre-commit-config.yaml``. Re-implementing that scan here duplicated the gate's
matching logic in a form that only ran under pytest.

The app-flavored tokens below are a different case: they name a private product's
vendors, schema, and tooling, so unlike the structural seams they are identifying
and are deliberately NOT committed to the seams denylist. This module is the only
place they are enforced. It keeps them — assembled at runtime, never as a source
literal, so this file cannot trip the gate on itself — and hands them to the
**real** leak gate as a denylist. What runs is the enforcement path that ships.

Scope is ``skills/``, which is what these tokens were stripped out of. The rest of
the plugin is not clean under them and cannot be added without a judgment call
this module should not make: the deliberately broad build-tool token matches an
ordinary English word in one agent's prose, and the gate's own docstring spells a
token as its documented example. Both are false positives of tokens kept broad on
purpose, and the fix for either is to special-case the word, never to weaken the
token.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_CRAFT_ROOT = Path(__file__).parent.parent
SKILLS_DIR = _CRAFT_ROOT / "plugins" / "craft" / "skills"
_LEAK_GATE = _CRAFT_ROOT / "plugins" / "craft" / "scripts" / "leak_gate.py"

# ---------------------------------------------------------------------------
# App-flavored seam tokens.
#
# Each token is assembled from a character list so it never appears as a
# contiguous string literal in this source file. The self-check below verifies
# the invariant at collection time.
# ---------------------------------------------------------------------------

APP_SEAM_TOKENS: list[str] = [
    # analytics/flag vendor
    "".join(["p", "o", "s", "t", "h", "o", "g"]),
    # flag-provider skill slug
    "".join(
        [
            "i",
            "n",
            "s",
            "t",
            "r",
            "u",
            "m",
            "e",
            "n",
            "t",
            "-",
            "f",
            "e",
            "a",
            "t",
            "u",
            "r",
            "e",
            "-",
            "f",
            "l",
            "a",
            "g",
            "s",
        ]
    ),
    # observability vendor
    "".join(["d", "a", "s", "h", "0"]),
    # soak evidence allowlist script
    "".join(["e", "v", "i", "d", "e", "n", "c", "e", "_", "p", "a", "c", "k"]),
    # cost-history report (Cluster B)
    "".join(["p", "l", "a", "n", "-", "c", "o", "s", "t", "-", "h", "i", "s", "t", "o", "r", "y"]),
    # private DB schema name
    "".join(["p", "r", "o", "j", "e", "c", "t", "i", "o", "n", "s"]),
    # dotted metric namespace prefix
    "".join(["p", "l", "a", "t", "f", "o", "r", "m", "."]),
    # issue tracker vendor
    "".join(["a", "s", "a", "n", "a"]),
    # build/test CLI
    "".join(["m", "i", "x"]),
    # build/test CLI
    "".join(["n", "p", "m"]),
    # host-config cross-ref path (denylisted org name → runtime-built)
    "".join(["z", "e", "n", "i", "t", "h", "/", ".", "c", "l", "a", "u", "d", "e"]),
]

# ---------------------------------------------------------------------------
# Self-check: INVARIANT — no app-seam token appears as a contiguous source
# literal outside of the join-list expressions above. Verified at module-load
# (pytest collection) so an accidental edit is caught immediately. Keep each
# join on one canonical single-line `"".join([...])` so the strip regex finds it.
# ---------------------------------------------------------------------------
_OWN_SOURCE = Path(__file__).read_text()
_SOURCE_WITHOUT_JOINS = re.sub(r'"".join\(\[.*?\]\)', "", _OWN_SOURCE, flags=re.DOTALL)
for _tok in APP_SEAM_TOKENS:
    assert _tok not in _SOURCE_WITHOUT_JOINS, (
        f"INVARIANT VIOLATION: app-seam token {_tok!r} appears as a source "
        f"literal in {__file__} outside a join expression — this would trip the "
        f"leak gate if the denylist is extended to cover it. Use the join form."
    )


@pytest.fixture(scope="module")
def denylist(tmp_path_factory) -> Path:
    """The app-seam tokens as a denylist the real gate can load.

    Escaped, so a token carrying a regex metacharacter (the dotted metric
    namespace prefix does) matches the literal the skills were stripped of
    rather than a wider pattern.
    """
    path = tmp_path_factory.mktemp("leak-gate") / "app-seams.denylist"
    path.write_text("\n".join(re.escape(token) for token in APP_SEAM_TOKENS) + "\n")
    return path


def _gate(tree: Path, denylist: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_LEAK_GATE), str(tree), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )


def test_the_shipped_skills_carry_no_app_seam_token(denylist: Path):
    """The gate itself grades craft's skills, rather than this module re-deriving
    what a match is. A token that creeps back into a skill blocks here the same way
    it would block a commit."""
    result = _gate(SKILLS_DIR, denylist)
    assert result.returncode == 0, (
        "the leak gate found an app-flavored seam token on craft's shipped skills. "
        "Genericize: replace vendor/stack-specific names with provider-agnostic "
        f"phrasing.\n{result.stdout}{result.stderr}"
    )


@pytest.mark.parametrize("index", range(len(APP_SEAM_TOKENS)), ids=lambda i: f"token{i}")
def test_the_gate_catches_every_token_the_denylist_carries(index: int, denylist: Path, tmp_path):
    """Anti-vacuity, per token: a clean scan proves nothing unless the gate would
    actually have failed on each token it was handed. A denylist entry mangled by a
    bad escape, or a token silently reduced to the empty string by an edit to its
    join list, would otherwise leave the scan above passing on a token it can no
    longer match."""
    tree = tmp_path / f"seeded{index}"
    tree.mkdir()
    (tree / "SKILL.md").write_text(f"A skill that mentions {APP_SEAM_TOKENS[index]} in passing.\n")
    result = _gate(tree, denylist)
    assert result.returncode == 1, (
        f"the gate did not catch seeded app-seam token {index}:\n{result.stdout}{result.stderr}"
    )
