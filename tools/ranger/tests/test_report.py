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
- That invocation is rendered at column 0, byte-for-byte paste-able out of
  the raw report — an indented heredoc terminator never closes the heredoc —
  and names the sweep's elected vault, because the operator's paste is a
  write and an unvaulted `record update` routes by a cwd-blind scan.
- A body whose unresolved section carries no parseable `**Question:**` still
  renders a line (a fixed placeholder naming the record), never an error.
- The question scan stops at the next `## ` heading, exactly like the
  classifier's section bounds — a `**Question:**` in a later section is not
  the unresolved section's question.
- A near-miss signal renders the expected-format hint line; absent otherwise.
- A failed line carries the fixed auto-retry sentence.
- Report and state files are created 0600 by the creating syscall itself,
  never briefly at the process umask and chmod-ed afterwards.
- finish() appends a footer naming the report's own absolute path.
"""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime, timedelta, timezone
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


def test_start_cleans_up_a_leftover_temp_file_from_a_crashed_write(tmp_path):
    """A kill between `mkstemp` and `os.replace` leaks a `.*.tmp` file that
    nothing else in this module ever revisits — `start` sweeps the group's
    reports dir for orphaned temp files older than the sweep it is about to
    start, so they don't accumulate forever."""
    env = _env(tmp_path)
    reports_dir = tmp_path / "state" / "reports" / "mygroup"
    reports_dir.mkdir(parents=True)
    orphan = reports_dir / ".20200101T000000000000Z.md.deadbeef.tmp"
    orphan.write_text("half-written", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    os.utime(orphan, (old, old))

    report.start("mygroup", "myvault", 0, env=env)

    assert not orphan.exists()


def test_start_leaves_an_unrelated_file_alone(tmp_path):
    env = _env(tmp_path)
    reports_dir = tmp_path / "state" / "reports" / "mygroup"
    reports_dir.mkdir(parents=True)
    keep = reports_dir / "not-a-tmp-file.md"
    keep.write_text("a real report", encoding="utf-8")

    report.start("mygroup", "myvault", 0, env=env)

    assert keep.exists()


def test_start_creates_report_and_state_files_0600(tmp_path):
    env = _env(tmp_path)

    report_path = report.start("mygroup", "myvault", 0, env=env)

    assert _mode(report_path) == 0o600
    state_path = report_path.with_suffix(".state.json")
    assert state_path.exists()
    assert _mode(state_path) == 0o600


def test_report_files_are_0600_from_the_creating_syscall_not_a_later_chmod(tmp_path, monkeypatch):
    """The mode must come from the open(2) call, not a fix-up afterwards.

    A report created at the process umask and chmod-ed a moment later is a
    file whose scrubbed-but-still-sensitive question text was briefly
    readable by every other user on the box. Neutralizing chmod is what makes
    that window observable: with the fix-up gone, only the creating syscall's
    mode remains.
    """
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)
    previous_umask = os.umask(0o000)
    try:
        report_path = report.start("mygroup", "myvault", 0, env=_env(tmp_path))
        report.append_promoted(report_path, "task/a")
    finally:
        os.umask(previous_umask)

    assert _mode(report_path) == 0o600
    assert _mode(report_path.with_suffix(".state.json")) == 0o600


def test_a_write_that_fails_mid_flight_leaves_the_previous_report_intact(tmp_path, monkeypatch):
    """Every re-render lands by rename, so a failed write is a no-op.

    The report is a partial durable artifact: an operator reads it after a
    sweep dies, and everything already recorded must still be there. Rendering
    the whole file over itself in place would mean a crash inside the write
    window truncates it and destroys every line recorded before this one.
    Renaming a fully-written temp file over it makes that window unobservable
    — the reader sees either the old file or the new one, never a half of one.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 2, env=env)
    report.append_promoted(report_path, "task/a")
    state_path = report_path.with_suffix(".state.json")
    report_before = report_path.read_text()
    state_before = state_path.read_text()

    def crash(*args, **kwargs):
        raise OSError("simulated crash inside the write window")

    monkeypatch.setattr(report.os, "replace", crash)
    with pytest.raises(OSError):
        report.append_promoted(report_path, "task/b")
    monkeypatch.undo()

    assert report_path.read_text() == report_before
    assert state_path.read_text() == state_before
    assert sorted(p.name for p in report_path.parent.iterdir()) == sorted(
        [report_path.name, state_path.name, report.outcomes_dir(report_path).name]
    ), "a failed write must leave no temp file behind"


def test_rewrites_keep_both_files_0600(tmp_path):
    """The rename must carry the temp file's own 0600, not the process umask."""
    env = _env(tmp_path)
    previous_umask = os.umask(0o000)
    try:
        report_path = report.start("mygroup", "myvault", 1, env=env)
        report.append_promoted(report_path, "task/a")
        report.finish(report_path)
    finally:
        os.umask(previous_umask)

    assert _mode(report_path) == 0o600
    assert _mode(report_path.with_suffix(".state.json")) == 0o600


def test_unparseable_state_is_a_named_error_and_never_a_silent_reset(tmp_path):
    """Corrupt state refuses by name; it does not start a fresh state file.

    Resetting would un-dedupe the sweep: `appended_task_ids` is the only
    record of what has already been written, so a fresh state re-appends every
    task the coordinator records from there on, and the operator reads a
    report that double-counts its own buckets.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 2, env=env)
    report.append_promoted(report_path, "task/a")
    state_path = report_path.with_suffix(".state.json")
    state_path.write_text("{ truncated", encoding="utf-8")

    with pytest.raises(report.ReportError) as exc_info:
        report.append_promoted(report_path, "task/b")

    message = str(exc_info.value)
    assert str(state_path) in message
    assert "unreadable JSON" in message
    assert "start a new sweep" in message, "the refusal must carry a remediation"
    assert "lock" in message, (
        "finish() raises this before it ever reaches lock release, so the remediation must "
        "say the vault lock is still held rather than leaving the operator wedged"
    )
    assert state_path.read_text() == "{ truncated", "corrupt state must never be overwritten"
    assert "task/a" in report_path.read_text()


def test_append_promoted_is_idempotent_per_task_id(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_promoted(report_path, "task/a")
    report.append_promoted(report_path, "task/a")

    text = report_path.read_text()
    assert text.count("task/a") == 1


def test_appends_survive_simulated_process_restart(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 2, env=env)
    report.append_promoted(report_path, "task/a")

    # Simulate a fresh process: nothing in-memory carries over, only the
    # files on disk. Re-append the same id (must stay a no-op) and a new one.
    report.append_promoted(report_path, "task/a")
    report.append_promoted(report_path, "task/b")

    text = report_path.read_text()
    assert text.count("task/a") == 1
    assert "task/b" in text


def test_simulated_crash_leaves_parseable_partial_report(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    report.append_promoted(report_path, "task/a")
    # No finish() call — simulates a crash mid-sweep.

    text = report_path.read_text()
    assert "task/a" in text
    for bucket in report.BUCKETS:
        assert f"## {report._BUCKET_HEADINGS[bucket]}" in text
    assert "Report written to" not in text


def test_finish_appends_footer_naming_absolute_report_path(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 0, env=env)

    report.finish(report_path)

    text = report_path.read_text()
    assert str(report_path.resolve()) in text


def test_escalated_question_redacted_and_answer_command_is_appliable(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    secret = "STRIPE_SECRET_KEY=sk_live_abcdefghijklmnopqrstuvwx"
    body = _unresolved_body(f"Which vendor key should replace {secret}?")

    report.append_escalated(report_path, "task/creds", body)

    text = report_path.read_text()
    assert secret not in text
    assert "[REDACTED]" in text
    assert "Which vendor key should replace" in text
    assert "lore record update task/creds --vault myvault --diff" in text

    # The state file is the durable record of what was written — the raw
    # secret must never land there either.
    state_text = report_path.with_suffix(".state.json").read_text()
    assert secret not in state_text

    # The embedded diff must actually apply against the ORIGINAL (unscrubbed)
    # record body via lore's own unified-diff applier, inserting the answer
    # line inside the unresolved section. Lifted verbatim — no per-line
    # stripping — because that is exactly what the operator's paste does; a
    # test that re-indents the text it extracted proves nothing about the
    # report it was extracted from.
    diff_start = text.index("--- a/body")
    diff_end = text.index("EOF", diff_start)
    diff_text = text[diff_start:diff_end]
    new_body, rejected = lore_store.apply_unified_diff(body, diff_text)
    assert rejected == []
    assert "**Answer:**" in new_body
    unresolved_section = new_body.split("## Refine — unresolved", 1)[1]
    assert "**Answer:**" in unresolved_section


def test_answer_command_block_is_paste_able_at_column_zero(tmp_path):
    """Every line of the invocation, terminator included, starts at column 0.

    The block is copied out of the raw markdown, not out of a renderer. A
    two-space indent on `EOF` means the shell never sees the terminator and
    the operator's paste hangs waiting for input; an indent on the diff lines
    corrupts the hunk before it is ever applied.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_escalated(report_path, "task/paste", _unresolved_body("Which one?"))

    text = report_path.read_text()
    command_start = text.index("lore record update task/paste --vault myvault --diff")
    command_end = text.index("EOF", command_start) + len("EOF")
    block = text[command_start:command_end]
    offenders = [line for line in block.splitlines() if line != line.lstrip()]
    assert not offenders, f"the answer command must be paste-able as-is; indented: {offenders}"
    assert "\nEOF" in text, "the heredoc terminator must sit alone at column 0"


def test_answer_command_targets_the_reports_own_elected_vault(tmp_path):
    """The pasted answer is a write, and writes route by vault or by luck.

    Without `--vault`, `lore record update` locates the record by the same
    cwd-blind first-match scan across configured vaults that the sweep's own
    reads and writes were fixed to stop relying on — so on a cross-vault name
    collision the operator's answer lands in a different group's record, and
    the task they meant to unblock stays blocked. The vault comes from the
    report's state, so it is the vault this sweep actually drained.
    """
    env = _env(tmp_path)
    report_path = report.start("othergroup", "vault-b", 1, env=env)

    report.append_escalated(report_path, "task/a", _unresolved_body("Which one?"))

    text = report_path.read_text()
    assert "lore record update task/a --vault vault-b --diff <<'EOF'" in text


def test_near_miss_line_present_when_signalled(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body("Is this answered?")

    report.append_escalated(report_path, "task/near", body, near_miss=True)

    text = report_path.read_text()
    assert "answer detected but not recognized" in text
    assert "**Answer:**" in text  # part of the expected-format hint text


def test_near_miss_line_absent_without_signal(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body("Is this answered?")

    report.append_escalated(report_path, "task/nonear", body)

    text = report_path.read_text()
    assert "answer detected but not recognized" not in text


def test_blocked_still_waiting_carries_question_and_answer_command(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body("What should unblock this?")

    report.append_blocked_still_waiting(report_path, "task/blocked", body)

    text = report_path.read_text()
    assert "What should unblock this?" in text
    assert "lore record update task/blocked --vault myvault --diff" in text


def test_blocked_answered_carries_no_question(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_blocked_answered(report_path, "task/answered")

    text = report_path.read_text()
    assert "task/answered" in text


def test_routed_line_names_target(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_routed(report_path, "task/route", "/craft:plan")

    text = report_path.read_text()
    assert "task/route" in text
    assert "/craft:plan" in text


@pytest.mark.parametrize("target", ["/craft:plan", "/craft:brainstorm"])
def test_routed_command_block_is_paste_able_at_column_zero(tmp_path, target):
    """Mirrors test_answer_command_block_is_paste_able_at_column_zero.

    A routed entry whose target is exactly `/craft:plan` or `/craft:brainstorm`
    gets a fenced code block, at parity with the escalated bucket's answer
    command, so the operator can copy the fully-formed slash command straight
    out of the raw markdown.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_routed(report_path, "task/route", target)

    text = report_path.read_text()
    command = f"{target} task/route"
    assert command in text
    lines = text.splitlines()
    offenders = [line for line in lines if line.strip() == command and line != command]
    assert not offenders, f"the routed command must render at column 0; indented: {offenders}"
    fence_start = text.index("```\n" + command)
    assert text[fence_start + len("```\n" + command + "\n"):].startswith("```\n")


def test_routed_non_standard_target_renders_flat_bullet_with_no_fence(tmp_path):
    """A `ROUTED` target other than `/craft:plan`/`/craft:brainstorm` — e.g. the
    parent record — keeps today's flat bullet unchanged (fail-closed): no
    fenced block, since there's no fixed command syntax to build one from.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_routed(report_path, "task/route", "task/some-parent")

    text = report_path.read_text()
    assert "- `task/route` — routed to task/some-parent\n" in text
    assert "```" not in text


def test_skipped_line_carries_reason(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_skipped(report_path, "task/skip", "not a standalone leaf")

    text = report_path.read_text()
    assert "task/skip" in text
    assert "not a standalone leaf" in text


def test_failed_line_carries_auto_retry_sentence(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_failed(report_path, "task/fail", "dispatch timed out")

    text = report_path.read_text()
    assert "task/fail" in text
    assert "dispatch timed out" in text
    assert "will be retried automatically next sweep" in text


# ---------------------------------------------------------------------------
# Every string written into any bucket line is scrubbed — not just the
# escalated/blocked-still-waiting question text. `reason`/`target` text
# originates from the dispatched agent's free-text return line, authored
# over untrusted record bodies, so it is exactly as credential-shaped a
# source as the question text already scrubbed above.
# ---------------------------------------------------------------------------

_COMPOUND_SECRET = "STRIPE_SECRET_KEY=sk_live_abcdefghijklmnopqrstuvwx"


def test_skipped_reason_scrubbed_of_credentials(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_skipped(report_path, "task/skip", f"blocked on {_COMPOUND_SECRET}")

    text = report_path.read_text()
    assert _COMPOUND_SECRET not in text
    assert "[REDACTED]" in text
    state_text = report_path.with_suffix(".state.json").read_text()
    assert _COMPOUND_SECRET not in state_text


def test_failed_reason_scrubbed_of_credentials(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_failed(report_path, "task/fail", f"dispatch used {_COMPOUND_SECRET}")

    text = report_path.read_text()
    assert _COMPOUND_SECRET not in text
    assert "[REDACTED]" in text
    state_text = report_path.with_suffix(".state.json").read_text()
    assert _COMPOUND_SECRET not in state_text


def test_routed_target_scrubbed_of_credentials(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_routed(report_path, "task/route", f"/craft:plan {_COMPOUND_SECRET}")

    text = report_path.read_text()
    assert _COMPOUND_SECRET not in text
    assert "[REDACTED]" in text
    state_text = report_path.with_suffix(".state.json").read_text()
    assert _COMPOUND_SECRET not in state_text


# ---------------------------------------------------------------------------
# The scrub must land on the untrusted field it is meant to catch, not on the
# fully-rendered line — a long unhyphenated task id or the answer command's
# own fixed syntax can accidentally look like the high-entropy base64
# pattern, and scrubbing the whole line eats trusted text along with it.
# ---------------------------------------------------------------------------

_LONG_TASK_NAME = "task/" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"  # 32 alnum chars, no hyphens


def test_long_task_id_renders_intact_in_escalated_line_and_answer_command(tmp_path):
    """A task id long enough to look like base64 must survive the scrub.

    The scrub exists to catch untrusted question/reason/target text, not the
    trusted task id or the fixed `lore record update ... --diff` syntax — both
    of those must render byte-for-byte, or the operator is handed a bucket
    line naming `[REDACTED]` and an answer command that can't be run.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body(f"Should we rotate {_COMPOUND_SECRET}?")

    report.append_escalated(report_path, _LONG_TASK_NAME, body)

    text = report_path.read_text()
    assert f"- `{_LONG_TASK_NAME}`" in text, "the task id must render intact, not [REDACTED]"
    assert (
        f"lore record update {_LONG_TASK_NAME} --vault myvault --diff" in text
    ), "the answer command must stay runnable, not [REDACTED]"
    assert _COMPOUND_SECRET not in text, "a real seeded credential in the question must still redact"
    assert "[REDACTED]" in text


def test_long_task_id_renders_intact_in_skipped_line(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_skipped(report_path, _LONG_TASK_NAME, f"blocked on {_COMPOUND_SECRET}")

    text = report_path.read_text()
    assert f"- `{_LONG_TASK_NAME}`" in text
    assert _COMPOUND_SECRET not in text
    assert "[REDACTED]" in text


def test_extract_question_raises_on_missing_section():
    with pytest.raises(report.QuestionExtractionError):
        report.extract_question("# task\n\nno unresolved section here\n")


# ---------------------------------------------------------------------------
# An unextractable question degrades to a placeholder, never to an error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "# task\n\n## Refine — unresolved\n\nSomebody wrote prose instead of a question.\n",
        "# task\n\nno unresolved section at all\n",
    ],
    ids=["section-without-question", "no-section"],
)
def test_unextractable_question_renders_a_placeholder_line(tmp_path, body):
    """A record the extractor cannot parse must not take the sweep down with it.

    The sweep's contract is that an outcome which doesn't parse is never
    fatal; a malformed record is that same case one layer down. The report
    still gets a line naming the record so the operator has a handle on it.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)

    report.append_escalated(report_path, "task/unparseable", body)

    text = report_path.read_text()
    assert "- `task/unparseable`" in text
    assert "question could not be extracted — open the record" in text
    assert "lore record update" not in text, (
        "there is no insertion line to build an answer command around"
    )


def test_unextractable_question_still_carries_the_near_miss_hint(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = "# task\n\n## Refine — unresolved\n\nAnswer: no question line above me.\n"

    report.append_escalated(report_path, "task/unparseable", body, near_miss=True)

    text = report_path.read_text()
    assert "question could not be extracted — open the record" in text
    assert "answer detected but not recognized" in text


# ---------------------------------------------------------------------------
# The question scan is bounded by the section, exactly like the classifier's
# ---------------------------------------------------------------------------


_LATER_SECTION = "## Notes\n\n**Question:** a question in an unrelated later section\n"


def test_question_scan_stops_at_the_next_section_heading(tmp_path):
    """A `**Question:**` after the section's closing heading is not its question.

    The extractor and the classifier must agree on where the section ends: the
    line number the answer command inserts at has to land *inside* the section,
    or the answer the operator pastes can never satisfy the section-bounded
    answered predicate.
    """
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = _unresolved_body("the real question?") + "\n" + _LATER_SECTION

    report.append_escalated(report_path, "task/bounded", body)

    text = report_path.read_text()
    assert "the real question?" in text
    assert "unrelated later section" not in text

    diff_start = text.index("--- a/body")
    diff_end = text.index("EOF", diff_start)
    new_body, rejected = lore_store.apply_unified_diff(body, text[diff_start:diff_end])
    assert rejected == []
    section = new_body.split("## Refine — unresolved", 1)[1].split("\n## ", 1)[0]
    assert "**Answer:**" in section, "the answer must land inside the unresolved section"


def test_question_only_in_a_later_section_is_treated_as_missing(tmp_path):
    env = _env(tmp_path)
    report_path = report.start("mygroup", "myvault", 1, env=env)
    body = "# task\n\n## Refine — unresolved\n\nNo question here.\n\n" + _LATER_SECTION

    report.append_escalated(report_path, "task/later", body)

    text = report_path.read_text()
    assert "question could not be extracted — open the record" in text
    assert "unrelated later section" not in text


@pytest.mark.parametrize("bad_group", ["../x", "a/b", ""])
def test_start_refuses_bad_group_before_filesystem_access(tmp_path, bad_group):
    env = _env(tmp_path)

    with pytest.raises(report.ReportError):
        report.start(bad_group, "myvault", 0, env=env)

    assert not (tmp_path / "state").exists()


# ---------------------------------------------------------------------------
# Shell-safe allowlist — the group name is a path segment in the reports
# directory, and shares its confinement definition with the vault-name check
# used before lock paths, so it is held to the same allowlist.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_group",
    [
        "prod`touch pwn`", "prod$(id)", "prod;id", "prod name", "prod'quote", "prod\nline",
        "prod\n",
    ],
    ids=[
        "backtick", "dollar-paren", "semicolon", "space", "quote", "newline", "trailing-newline",
    ],
)
def test_start_refuses_shell_metacharacters_in_group(tmp_path, bad_group):
    env = _env(tmp_path)

    with pytest.raises(report.ReportError):
        report.start(bad_group, "myvault", 0, env=env)

    assert not (tmp_path / "state").exists()
