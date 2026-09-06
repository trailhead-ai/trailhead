"""No craft skill outside execute/drive reaches the credential-pattern scrub
or the untrusted-value rule by naming `execute.md`.

`execute/SKILL.md` and `drive/SKILL.md` legitimately read `_shared/execute.md`
end to end — they run its build-loop controller, so a mention of the filename
there is not a leftover security-rule citation. Every other skill's citation of
the credential-pattern scrub or the untrusted-value rule must repoint to
`_shared/security.md`, the document those rules now live in on their own.

This suite derives the site set empirically, by scanning every
`plugins/craft/skills/*/SKILL.md`, never from a hardcoded count or list of
citing filenames — a scan that matched nothing would report clean while
proving nothing, so a non-vacuity guard covers both the whole-corpus scan and
the migrated subset it checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SKILLS = REPO_ROOT / "plugins" / "craft" / "skills"

# execute and drive genuinely read `_shared/execute.md` end to end to run its
# build-loop controller; every other skill's mention of the filename would be
# a security-rule citation this migration must have repointed.
_LEGITIMATE_EXECUTE_MD_READERS = {"execute", "drive"}


def _skill_md_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def _migrated_skill_md_files() -> list[Path]:
    return [p for p in _skill_md_files() if p.parent.name not in _LEGITIMATE_EXECUTE_MD_READERS]


def test_skill_md_corpus_is_non_empty():
    """Non-vacuity guard on the whole-corpus scan below."""
    assert _skill_md_files(), f"no SKILL.md found under {SKILLS}"


def test_migrated_skill_set_is_non_empty():
    """Non-vacuity guard on the migrated-subset scan: excluding execute/drive
    must still leave skills to check, or the parametrized test below passes
    on an empty set and proves nothing."""
    assert _migrated_skill_md_files(), (
        f"expected at least one skill besides {sorted(_LEGITIMATE_EXECUTE_MD_READERS)} under {SKILLS}"
    )


@pytest.mark.parametrize("path", _migrated_skill_md_files(), ids=lambda p: p.parent.name)
def test_migrated_skill_does_not_cite_execute_md(path):
    text = path.read_text(encoding="utf-8")
    assert "execute.md" not in text, (
        f"{path.parent.name}/SKILL.md still names `execute.md` — its security-rule "
        "citations must repoint to `_shared/security.md`"
    )
