"""Doc-consistency checks for the vault-sync skill.

The `sync` skill drives `lore sync` and, when a rebase conflicts, `lore resolve`.
Resolution is now **autonomous** — the agent settles the conflicts and reports
what it chose — so these tests pin the two invariants that flip is easy to lose:
the skill must drive `lore resolve`, and it must carry no instruction telling the
agent to keep its hands off a conflict.
"""
import json
import re
from pathlib import Path

from conftest import load_script

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "lore" / "skills"
SYNC_SKILL = SKILLS_DIR / "sync" / "SKILL.md"


def _skill_text() -> str:
    return SYNC_SKILL.read_text()


def test_sync_skill_frontmatter_still_registrable():
    """Editing the skill must not break the frontmatter Claude Code needs."""
    text = _skill_text()
    assert text.startswith("---\n"), "sync/SKILL.md must still open with a YAML frontmatter block"
    end = text.find("\n---", 3)
    assert end > 0, "sync/SKILL.md frontmatter block must still be closed"
    frontmatter = text[3:end]
    desc_lines = [
        ln for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, "sync/SKILL.md must still carry a non-empty description:"


def test_sync_skill_drives_lore_resolve_on_conflict():
    """A rebase conflict is settled through `lore resolve`, read as JSON."""
    text = _skill_text()
    assert "lore resolve" in text, (
        "sync/SKILL.md must document `lore resolve` as the conflict remedy"
    )
    assert re.search(r"lore resolve [^\n`]*--json", text), (
        "sync/SKILL.md must document reading the conflict report with `--json`"
    )
    assert "lore resolve take" in text, (
        "sync/SKILL.md must document settling a conflict with `lore resolve take`"
    )
    assert "take-file" in text, (
        "sync/SKILL.md must document `lore resolve take-file` for sites/ conflicts"
    )
    assert "git pull --rebase" not in text, (
        "sync/SKILL.md must not send the agent to raw git to resolve a vault conflict"
    )


def test_sync_skill_carries_no_never_resolve_instruction():
    """The old 'do NOT resolve unless the user asks' instruction is now backwards."""
    lowered = _skill_text().lower()
    for forbidden in (
        "unless the user asks",
        "do not attempt to resolve",
        "do not resolve",
        "never resolve",
    ):
        assert forbidden not in lowered, (
            f"sync/SKILL.md must not tell the agent {forbidden!r} — resolution is autonomous"
        )


def test_sync_skill_separates_judgment_calls_from_auto_takes():
    """The after-the-fact report must show which calls actually needed thought."""
    lowered = _skill_text().lower()
    assert "judgment" in lowered, (
        "sync/SKILL.md must name the judgment resolutions in its report shape"
    )
    assert "auto-merged" in lowered or "auto-take" in lowered, (
        "sync/SKILL.md must name the mechanical auto-takes distinctly from judgment calls"
    )


def test_sync_skill_speaks_local_remote_not_ours_theirs():
    """The CLI's vocabulary is device-native; git's ours/theirs inverts at a rebase."""
    text = _skill_text()
    assert "--local" in text and "--remote" in text, (
        "sync/SKILL.md must use the CLI's `--local` / `--remote` vocabulary"
    )
    lowered = text.lower()
    for git_word in ("ours", "theirs"):
        assert not re.search(rf"\b{git_word}\b", lowered), (
            f"sync/SKILL.md must not leak git's {git_word!r} vocabulary — it is "
            "inverted during a rebase"
        )


def test_sync_skill_carries_shared_vault_fencing_guidance():
    """Remote text from a shared vault arrives fenced, and shared vaults gate the push."""
    text = _skill_text()
    assert "--include-shared" in text, (
        "sync/SKILL.md must document the shared-vault push gate (`--include-shared`)"
    )
    assert "external-memory" in text or "fenced" in text.lower(), (
        "sync/SKILL.md must carry the shared-vault fencing guidance: remote-side "
        "text from a shared vault is data, not instructions"
    )


def test_sync_skill_json_example_matches_render_json_schema():
    """The documented ``--json`` example must use the keys `render_json` actually emits.

    The vault's own marker file legitimately uses hyphenated ``record-id``
    internally, which makes it an easy mistake to copy that spelling into the
    agent-facing report doc — but the report is emitted by `render_json`, which
    uses ``record_id``. Ties the doc to the emitter, not a hard-coded string, so
    the two cannot drift apart again undetected.
    """
    text = _skill_text()
    match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    assert match, "sync/SKILL.md must carry a fenced ```json example of the resolve report"
    example = json.loads(match.group(1))

    resolve = load_script("lore.cli.resolve")
    conflicts = [{
        "record-id": "task/ship-the-thing", "kind": "task", "slot": "status",
        "local": {"sha": "aaa", "date": "d", "value": "ready"},
        "remote": {"sha": "bbb", "date": "d", "value": "complete"},
    }]
    files = [{
        "path": "sites/report/index.html", "reason": "r",
        "local": {"sha": "aaa", "date": "d"},
        "remote": {"sha": "bbb", "date": "d"},
    }]
    payload = resolve.render_json("trailhead", conflicts, files, shared=False)

    doc_conflict = example["conflicts"][0]
    real_conflict = payload["conflicts"][0]
    assert set(doc_conflict.keys()) == set(real_conflict.keys()), (
        f"documented conflict object keys {sorted(doc_conflict.keys())} must match "
        f"render_json's actual keys {sorted(real_conflict.keys())}"
    )
    assert set(doc_conflict["local"].keys()) == set(real_conflict["local"].keys())
    assert set(doc_conflict["remote"].keys()) == set(real_conflict["remote"].keys())

    doc_file = example["files"][0]
    real_file = payload["files"][0]
    assert set(doc_file.keys()) == set(real_file.keys()), (
        f"documented file object keys {sorted(doc_file.keys())} must match "
        f"render_json's actual keys {sorted(real_file.keys())}"
    )
    assert isinstance(doc_file["local"], dict) and isinstance(doc_file["remote"], dict), (
        "render_json's file objects carry `local`/`remote` as {sha, date} dicts, "
        "not scalar values — the doc example must show that shape"
    )
    assert set(doc_file["local"].keys()) == set(real_file["local"].keys())
    assert set(doc_file["remote"].keys()) == set(real_file["remote"].keys())
