"""Tests for the ritual deliverable grader.

Two independent verdicts over one captured ritual deliverable, for AC6 and
AC7 of `spec/record-mentions-in-agent-output-are-reachable`:

- record-link — did the deliverable render the record it acted on as a
  markdown link, or as a bare identifier?
- next-command — is the single next command alone on its own line, embedded
  mid-sentence, or absent because the outcome is one of AC7's exempt
  branching/by-design-zero-command cases?

Exit-code contract:
  0 → both predicates pass (record-link == link, next-command in
      {own-line, exempt})
  1 → at least one predicate fails
  2 → could not grade — fail-closed: empty stdin, non-UTF-8 stdin, or
      --command omitted without --exempt
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GRADER = REPO_ROOT / "plugins" / "craft" / "scripts" / "ritual_deliverable_grader.py"

RECORD = "task/the-quarterly-audit-trail-slice"
COMMAND = "/craft:plan task/the-quarterly-audit-trail-slice"


def run(text: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GRADER), *args],
        input=text,
        capture_output=True,
        text=True,
    )


class TestRecordLinkPredicate:
    def test_record_named_as_markdown_link_passes(self):
        deliverable = (
            f"The new parent task is [{RECORD}](http://127.0.0.1:7313/records/"
            f"fieldnotes/{RECORD}).\n\n{COMMAND}\n"
        )
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "record-link: link" in result.stdout.splitlines()

    def test_same_identifier_as_bare_text_fails(self):
        # Same identifier as above, but typed plainly rather than inside
        # link syntax — the only thing that varies between this case and
        # the one above.
        deliverable = f"The new parent task is {RECORD}.\n\n{COMMAND}\n"
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "record-link: bare" in result.stdout.splitlines()

    def test_link_to_a_different_record_fails(self):
        other = "task/some-other-slice"
        deliverable = (
            f"See [{other}](http://127.0.0.1:7313/records/fieldnotes/{other}) "
            f"for context. The new parent task is {RECORD}.\n\n{COMMAND}\n"
        )
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "record-link: bare" in result.stdout.splitlines()


class TestNextCommandPredicate:
    def test_command_alone_on_its_own_line_passes(self):
        deliverable = f"Record: [{RECORD}](http://x/{RECORD})\n\n{COMMAND}\n"
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "next-command: own-line" in result.stdout.splitlines()

    def test_same_command_embedded_mid_sentence_fails(self):
        # Same command text as above, embedded in a sentence instead of
        # standing alone on its own line — the only thing that varies.
        deliverable = (
            f"Record: [{RECORD}](http://x/{RECORD})\n\n"
            f"Next, run {COMMAND} to continue.\n"
        )
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "next-command: embedded" in result.stdout.splitlines()

    def test_branching_outcome_is_exempt_even_with_no_command_present(self):
        deliverable = (
            f"Record: [{RECORD}](http://x/{RECORD})\n\n"
            "Any Critical undisposed — handoff withheld, return to disposition "
            "gathering.\n"
        )
        result = run(deliverable, "--record", RECORD, "--exempt")
        assert "next-command: exempt" in result.stdout.splitlines()

    def test_command_missing_entirely_is_not_exempt_without_the_flag(self):
        deliverable = f"Record: [{RECORD}](http://x/{RECORD})\n\nNo command here.\n"
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "next-command: absent" in result.stdout.splitlines()

    def test_command_wrapped_in_one_pair_of_backticks_alone_on_its_own_line_passes(self):
        # A captured deliverable renders the handoff command as an inline
        # code span, e.g. `` `/craft:gauntlet spec/x` `` — the line still
        # carries only that one command once its wrapping backticks are
        # stripped, so it counts as own-line, same as a bare line would.
        deliverable = f"Record: [{RECORD}](http://x/{RECORD})\n\n`{COMMAND}`\n"
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "next-command: own-line" in result.stdout.splitlines()

    def test_same_backtick_wrapped_command_embedded_mid_sentence_fails(self):
        # Same backtick-wrapped command text as above, embedded in a
        # sentence instead of standing alone on its own line — the only
        # thing that varies.
        deliverable = (
            f"Record: [{RECORD}](http://x/{RECORD})\n\n"
            f"Next, run `{COMMAND}` to continue.\n"
        )
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert "next-command: embedded" in result.stdout.splitlines()


class TestExitCodes:
    def test_both_predicates_passing_exits_zero(self):
        deliverable = f"[{RECORD}](http://x/{RECORD})\n\n{COMMAND}\n"
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert result.returncode == 0

    def test_a_failing_predicate_exits_one(self):
        deliverable = f"{RECORD}\n\n{COMMAND}\n"
        result = run(deliverable, "--record", RECORD, "--command", COMMAND)
        assert result.returncode == 1

    def test_exempt_outcome_with_record_linked_exits_zero(self):
        # next-command is exempt (an always-passing verdict) and record-link
        # is `link`, so the exit code must be 0 — half of the exit-code
        # contract that the --exempt path exercises and the stdout-only
        # assertions elsewhere in this file never pin.
        deliverable = f"[{RECORD}](http://x/{RECORD})\n\nSome closing prose.\n"
        result = run(deliverable, "--record", RECORD, "--exempt")
        assert result.returncode == 0

    def test_exempt_outcome_with_record_bare_still_exits_one(self):
        # Same deliverable shape as above, but the record is bare instead
        # of linked — the only thing that varies — so record-link fails
        # even though next-command stays exempt, and the combined exit
        # code must still be 1.
        deliverable = f"{RECORD}\n\nSome closing prose.\n"
        result = run(deliverable, "--record", RECORD, "--exempt")
        assert result.returncode == 1

    def test_empty_stdin_exits_two_with_reason(self):
        result = run("", "--record", RECORD, "--command", COMMAND)
        assert result.returncode == 2
        assert "reason-code: empty-stdin" in result.stderr

    def test_non_utf8_stdin_exits_two_with_reason(self):
        result = subprocess.run(
            [sys.executable, str(GRADER), "--record", RECORD, "--command", COMMAND],
            input=b"\xff\xfe not valid utf-8",
            capture_output=True,
        )
        assert result.returncode == 2
        assert b"reason-code: invalid-utf8-stdin" in result.stderr

    def test_missing_command_without_exempt_exits_two_with_reason(self):
        deliverable = f"[{RECORD}](http://x/{RECORD})\n"
        result = run(deliverable, "--record", RECORD)
        assert result.returncode == 2
        assert "reason-code: missing-command" in result.stderr
