"""Regression guard: no portage skill or agent dispatches a phantom target.

`code-simplifier` was never a real subagent — repo-wide grep turns up only the
one dispatch site that named it. `skills/review/code-reviewer.md` resolves
inside the craft plugin, not portage, so a portage doc pointing at that path
is a cross-plugin leak that breaks the moment craft isn't installed alongside
portage. Both are silent failure modes: the dispatch instruction reads fine
but resolves to nothing at runtime.

This scans every skill/agent doc portage ships and asserts neither phantom
target is referenced anywhere.
"""

from __future__ import annotations

from pathlib import Path

PORTAGE_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "portage"


def _portage_docs() -> list[Path]:
    docs: list[Path] = []
    for subdir in ("skills", "agents"):
        docs.extend((PORTAGE_PLUGIN_ROOT / subdir).rglob("*.md"))
    return docs


def test_no_portage_doc_references_code_simplifier():
    offenders = [
        doc for doc in _portage_docs() if "code-simplifier" in doc.read_text()
    ]

    assert offenders == [], (
        "code-simplifier is not a real subagent; these docs still dispatch it: "
        f"{offenders}"
    )


def test_no_portage_doc_references_craft_review_path():
    offenders = [
        doc for doc in _portage_docs() if "skills/review/" in doc.read_text()
    ]

    assert offenders == [], (
        "skills/review/ resolves inside craft, not portage; these docs still "
        f"reference it: {offenders}"
    )
