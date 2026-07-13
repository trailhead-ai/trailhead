"""The four legacy PR-lifecycle skills (open/update/monitor/merge) collapse into
one verb-dispatched `pull_request` skill.

Locks:
  - portage's on-disk-discovered skill inventory is exactly {pull_request} —
    the four legacy dirs are gone, not just renamed.
  - pull_request/SKILL.md documents all four verbs (create/update/monitor/merge)
    AND states the verb-parsing rule unambiguously, with a worked example —
    this is the first skill in the repo to consume $ARGUMENTS, so there is no
    sibling convention to lean on implicitly.
  - pull_request/SKILL.md carries a deprecation pointer so an agent that still
    reaches for the retired /portage:open|update|monitor|merge names finds the
    new syntax instead of a dead end.

Runtime verb-routing itself is model-interpreted prose, not something a static
test can execute — that gap is covered by the assumption-prover pass on the
harness's $ARGUMENTS passthrough, not by this file.
"""

from __future__ import annotations

from pathlib import Path

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PORTAGE_MANIFEST = _REPO_ROOT / "tools" / "portage" / "capabilities.toml"
_SKILLS_DIR = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage" / "skills"
_PULL_REQUEST_SKILL = _SKILLS_DIR / "pull_request" / "SKILL.md"


class TestSkillInventoryUnified:
    def test_portage_skill_inventory_is_pull_request_only(self):
        manifest = load_manifest(_PORTAGE_MANIFEST)
        assert set(manifest.skills) == {"pull_request"}, (
            f"expected portage's selectable skills to be exactly {{'pull_request'}}, "
            f"got {sorted(manifest.skills)}"
        )
        assert manifest.skills["pull_request"] == "skills/pull_request"

    def test_legacy_skill_dirs_are_absent(self):
        for name in ("open", "update", "monitor", "merge"):
            offender = _SKILLS_DIR / name
            assert not offender.exists(), f"legacy skill dir {offender} must be deleted"


class TestPullRequestSkillDocumentsAllVerbs:
    def test_skill_file_exists(self):
        assert _PULL_REQUEST_SKILL.is_file(), (
            f"expected {_PULL_REQUEST_SKILL} to exist"
        )

    def test_documents_all_four_verbs(self):
        text = _PULL_REQUEST_SKILL.read_text()
        for verb in ("create", "update", "monitor", "merge"):
            assert verb in text, f"pull_request/SKILL.md must document the {verb!r} verb"


class TestPullRequestSkillVerbParsingRule:
    def test_documents_arguments_variable(self):
        text = _PULL_REQUEST_SKILL.read_text()
        assert "$ARGUMENTS" in text, (
            "pull_request/SKILL.md must state that verb parsing reads $ARGUMENTS "
            "— this is the first skill in the repo to use it, so it can't lean "
            "on an implicit sibling convention"
        )

    def test_documents_worked_example_table(self):
        text = _PULL_REQUEST_SKILL.read_text()
        assert "/portage:pull_request create 123" in text, (
            "pull_request/SKILL.md must include a worked example mapping "
            "'/portage:pull_request create 123' to verb=create, rest=123"
        )
        assert "verb=" in text and "rest=" in text, (
            "the worked example must name the parsed verb and rest explicitly"
        )


class TestPullRequestSkillDeprecationPointer:
    def test_documents_retired_names(self):
        text = _PULL_REQUEST_SKILL.read_text()
        for retired in (
            "/portage:open",
            "/portage:update",
            "/portage:monitor",
            "/portage:merge",
        ):
            assert retired in text, (
                f"pull_request/SKILL.md must point agents still reaching for "
                f"{retired!r} at the new /portage:pull_request <verb> syntax"
            )
