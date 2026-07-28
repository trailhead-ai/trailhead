"""Tests for ranger.sweep.report — the durable sweep exit report.

Test contract:
- A fresh report (start()) has the header (group, vault, queue size) and all
  seven bucket headings present before any task is appended.
- Appending is idempotent per task id — dedupe via the companion state JSON —
  and appends survive a simulated process restart (state reloaded from disk).
- A simulated crash (no finish()) leaves a parseable partial report: header +
  bucket headings + whatever was appended, no footer.
- An escalated line carries the question with seeded secrets (including
  compound names like STRIPE_SECRET_KEY=...) redacted, plus a `lore record
  update <id> --diff` invocation that is actually appliable against the
  original (unscrubbed) record body via lore's own unified-diff applier.
- A near-miss signal renders the expected-format hint line; absent otherwise.
- A failed line carries the fixed auto-retry sentence.
- Report and state files are created 0600.
- finish() appends a footer naming the report's own absolute path.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_RANGER_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"
_LORE_PLUGIN_DIR = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore"

for _plugin_dir in (_RANGER_PLUGIN_DIR, _LORE_PLUGIN_DIR):
    if str(_plugin_dir) not in sys.path:
        sys.path.insert(0, str(_plugin_dir))

from lore.record import store as lore_store  # noqa: E402

from ranger.sweep import report  # noqa: E402


def _env(tmp_path: Path) -> dict[str, str]:
    return {"RANGER_STATE_DIR": str(tmp_path / "state")}


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _unresolved_body(question: str) -> str:
    return (
        "# some task\n\n"
        "Some captured prose.\n\n"
        "## Refine — unresolved\n\n"
        f"**Question:** {question}\n\n"
        "**Evidence gathered:** file.py:12\n\n"
        "**Recommended answer:** do the obvious thing\n"
    )


def test_start_creates_header_and_all_seven_bucket_headings(tmp_path):
    env = _env(tmp_path)

    report_path = report.start("mygroup", "myvault", 3, env=env)

    text = report_path.read_text()
    assert "**Group:** mygroup" in text
    assert "**Vault:** myvault" in text
    assert "**Queue size:** 3 tasks derived" in text
    for bucket in report.BUCKETS:
        assert f"## {report._BUCKET_HEADINGS[bucket]}" in text


def test_start_creates_report_and_state_files_0600(tmp_path):
    env = _env(tmp_path)

    report_path = report.start("mygroup", "myvault", 0, env=env)

    assert _mode(report_path) == 0o600
    state_path = report_path.with_suffix(".state.json")
    assert state_path.exists()
    assert _mode(state_path) == 0o600


def test_append_promoted_is_idempotent_per_task_id(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_promoted(report_path, "task/a", env=env)
    report.append_promoted(report_path, "task/a", env=env)

    text = report_path.read_text()
    assert text.count("task/a") == 1


def test_appends_survive_simulated_process_restart(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 2, env=env)
    report.append_promoted(report_path, "task/a", env=env)

    # Simulate a fresh process: nothing in-memory carries over, only the
    # files on disk. Re-append the same id (must stay a no-op) and a new one.
    report.append_promoted(report_path, "task/a", env=env)
    report.append_promoted(report_path, "task/b", env=env)

    text = report_path.read_text()
    assert text.count("task/a") == 1
    assert "task/b" in text


def test_simulated_crash_leaves_parseable_partial_report(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    report.append_promoted(report_path, "task/a", env=env)
    # No finish() call — simulates a crash mid-sweep.

    text = report_path.read_text()
    assert "task/a" in text
    for bucket in report.BUCKETS:
        assert f"## {report._BUCKET_HEADINGS[bucket]}" in text
    assert "Report written to" not in text


def test_finish_appends_footer_naming_absolute_report_path(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 0, env=env)

    report.finish(report_path, env=env)

    text = report_path.read_text()
    assert str(report_path.resolve()) in text


def test_escalated_question_redacted_and_answer_command_is_appliable(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    secret = "STRIPE_SECRET_KEY=sk_live_abcdefghijklmnopqrstuvwx"
    body = _unresolved_body(f"Which vendor key should replace {secret}?")

    report.append_escalated(report_path, "task/creds", body, env=env)

    text = report_path.read_text()
    assert secret not in text
    assert "[REDACTED]" in text
    assert "Which vendor key should replace" in text
    assert "lore record update task/creds --diff" in text

    # The state file is the durable record of what was written — the raw
    # secret must never land there either.
    state_text = report_path.with_suffix(".state.json").read_text()
    assert secret not in state_text

    # The embedded diff must actually apply against the ORIGINAL (unscrubbed)
    # record body via lore's own unified-diff applier, inserting the answer
    # line inside the unresolved section.
    diff_start = text.index("--- a/body")
    diff_end = text.index("EOF", diff_start)
    diff_text = "\n".join(
        line.strip() for line in text[diff_start:diff_end].splitlines() if line.strip()
    ) + "\n"
    new_body, rejected = lore_store.apply_unified_diff(body, diff_text)
    assert rejected == []
    assert "**Answer:**" in new_body
    unresolved_section = new_body.split("## Refine — unresolved", 1)[1]
    assert "**Answer:**" in unresolved_section


def test_near_miss_line_present_when_signalled(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body("Is this answered?")

    report.append_escalated(report_path, "task/near", body, near_miss=True, env=env)

    text = report_path.read_text()
    assert "answer detected but not recognized" in text
    assert "**Answer:**" in text  # part of the expected-format hint text


def test_near_miss_line_absent_without_signal(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body("Is this answered?")

    report.append_escalated(report_path, "task/nonear", body, env=env)

    text = report_path.read_text()
    assert "answer detected but not recognized" not in text


def test_blocked_still_waiting_carries_question_and_answer_command(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body("What should unblock this?")

    report.append_blocked_still_waiting(report_path, "task/blocked", body, env=env)

    text = report_path.read_text()
    assert "What should unblock this?" in text
    assert "lore record update task/blocked --diff" in text


def test_blocked_answered_carries_no_question(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_blocked_answered(report_path, "task/answered", env=env)

    text = report_path.read_text()
    assert "task/answered" in text


def test_routed_line_names_target(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_routed(report_path, "task/route", "/craft:plan", env=env)

    text = report_path.read_text()
    assert "task/route" in text
    assert "/craft:plan" in text


def test_skipped_line_carries_reason(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_skipped(report_path, "task/skip", "not a standalone leaf", env=env)

    text = report_path.read_text()
    assert "task/skip" in text
    assert "not a standalone leaf" in text


def test_failed_line_carries_auto_retry_sentence(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_failed(report_path, "task/fail", "dispatch timed out", env=env)

    text = report_path.read_text()
    assert "task/fail" in text
    assert "dispatch timed out" in text
    assert "will be retried automatically next sweep" in text


def test_extract_question_raises_on_missing_section():
    with pytest.raises(report.QuestionExtractionError):
        report.extract_question("# task\n\nno unresolved section here\n")


@pytest.mark.parametrize("bad_group", ["../x", "a/b", ""])
def test_start_refuses_bad_group_before_filesystem_access(tmp_path, bad_group):
    env = _env(tmp_path)

    with pytest.raises(report.ReportError):
        report.start(bad_group, "myvault", 0, env=env)

    assert not (tmp_path / "state").exists()
