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

import re
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


class TestExemptObservation:
    """An exempt outcome still passes, but per the active lesson `a-classification-
    that-routes-a-site-around-the-predicate-makes-that-site-unmeasurable`, it must
    now carry an observation of the deliverable rather than emit a verdict about
    nothing. When `--command` is supplied alongside `--exempt`, the grader reports
    the shape it actually found; when it is not, the grader says plainly that
    nothing was observed and why."""

    def test_exempt_with_command_embedded_mid_sentence_reports_embedded(self):
        deliverable = (
            f"Record: [{RECORD}](http://x/{RECORD})\n\n"
            f"Next, run {COMMAND} to continue.\n"
        )
        result = run(deliverable, "--record", RECORD, "--command", COMMAND, "--exempt")
        lines = result.stdout.splitlines()
        assert "exempt-observation: embedded" in lines
        # the exempt verdict itself is unchanged by the observation
        assert "next-command: exempt" in lines

    def test_exempt_with_command_on_its_own_line_reports_own_line(self):
        # Same command text as above, alone on its own line instead of
        # embedded mid-sentence — the only thing that varies.
        deliverable = f"Record: [{RECORD}](http://x/{RECORD})\n\n{COMMAND}\n"
        result = run(deliverable, "--record", RECORD, "--command", COMMAND, "--exempt")
        lines = result.stdout.splitlines()
        assert "exempt-observation: own-line" in lines
        assert "next-command: exempt" in lines

    def test_exempt_without_command_reports_no_observation_and_why(self):
        deliverable = f"[{RECORD}](http://x/{RECORD})\n\nSome closing prose.\n"
        result = run(deliverable, "--record", RECORD, "--exempt")
        lines = result.stdout.splitlines()
        observation_lines = [line for line in lines if line.startswith("exempt-observation:")]
        assert len(observation_lines) == 1
        assert "not-observed" in observation_lines[0]
        assert "--command" in observation_lines[0]
        # the exempt verdict, record-link verdict, and exit code stay exactly
        # what they were before --command was accepted alongside --exempt
        assert "next-command: exempt" in lines
        assert "record-link: link" in lines
        assert result.returncode == 0

    def test_exempt_verdict_and_exit_code_unaffected_by_command_shape(self):
        # Same record-linked deliverable, exempt either way; whether the
        # supplied command is found own-line or embedded must not move the
        # exempt verdict or the exit code — only the new observation line.
        own_line = f"[{RECORD}](http://x/{RECORD})\n\n{COMMAND}\n"
        embedded = f"[{RECORD}](http://x/{RECORD})\n\nRun {COMMAND} now.\n"
        for deliverable in (own_line, embedded):
            result = run(deliverable, "--record", RECORD, "--command", COMMAND, "--exempt")
            assert "next-command: exempt" in result.stdout.splitlines()
            assert result.returncode == 0


class TestObservationVariesWithTheDeliverable:
    """Council amendment: every verdict branch must read the deliverable before
    returning. Proven behaviorally — for every branch capable of observing
    something, changing only the deliverable's content changes what that branch
    reports. `next_command_verdict`'s `if exempt: return "exempt"` used to be the
    one branch that returned without ever looking at the text; this pins that the
    exempt-with-command branch no longer does."""

    def test_every_observable_branch_reports_differently_for_a_different_deliverable(self):
        own_line_command_text = f"[{RECORD}](http://x/{RECORD})\n\n{COMMAND}\n"
        embedded_command_text = f"[{RECORD}](http://x/{RECORD})\n\nRun {COMMAND} now.\n"
        linked_record_text = f"[{RECORD}](http://x/{RECORD})\n\n{COMMAND}\n"
        bare_record_text = f"{RECORD}\n\n{COMMAND}\n"

        cases = [
            # (args, deliverable A, deliverable B, prefix of the line that must differ)
            (
                ["--record", RECORD, "--command", COMMAND],
                own_line_command_text,
                embedded_command_text,
                "next-command:",
            ),
            (
                ["--record", RECORD, "--command", COMMAND, "--exempt"],
                own_line_command_text,
                embedded_command_text,
                "exempt-observation:",
            ),
            (
                ["--record", RECORD, "--command", COMMAND],
                linked_record_text,
                bare_record_text,
                "record-link:",
            ),
        ]

        for args, deliverable_a, deliverable_b, prefix in cases:
            result_a = run(deliverable_a, *args)
            result_b = run(deliverable_b, *args)
            [line_a] = [l for l in result_a.stdout.splitlines() if l.startswith(prefix)]
            [line_b] = [l for l in result_b.stdout.splitlines() if l.startswith(prefix)]
            assert line_a != line_b, f"{prefix} did not vary with the deliverable"


class TestRegressionAgainstOriginalMeasurement:
    """Re-grading every capture committed under the eval case's `runs/` directory
    with the changed grader must reproduce the original measurement's record-link
    and next-command verdicts, and exit code, exactly — the fix is additive only.
    Compares the changed grader's output against the pre-change grader (read from
    its git blob at the base commit, never edited in place) run over the same
    captures, so nothing here is a transcribed table."""

    BASE_SHA = "0336687c"

    RITUAL_GRADER_ARGS = {
        "brainstorm": [
            "--record", "spec/warehouse-picking-batches",
            "--command", "/craft:gauntlet spec/warehouse-picking-batches",
        ],
        "gauntlet": [
            "--record", "spec/permit-renewal-workflow",
            "--command", "/craft:slice spec/permit-renewal-workflow",
        ],
        "plan": [
            "--record", "task/the-ledger-reconciliation-slice",
            "--command", "/craft:execute task/the-ledger-reconciliation-slice",
        ],
        "plan-treatment": [
            "--record", "task/the-ledger-reconciliation-slice",
            "--command", "/craft:execute task/the-ledger-reconciliation-slice",
        ],
        "execute": ["--record", "task/the-ledger-reconciliation-slice", "--exempt"],
        "review": [
            "--record", "spec/dock-scheduling-windows",
            "--command", "/craft:distill spec/dock-scheduling-windows",
        ],
        "review-treatment": [
            "--record", "spec/dock-scheduling-windows",
            "--command", "/craft:distill spec/dock-scheduling-windows",
        ],
        "distill": [
            "--record", "adr/dock-scheduling-windows-use-fifo-slots",
            "--command", 'lore record show adr/dock-scheduling-windows-use-fifo-slots',
        ],
        "execute-measurable": [
            "--record", "task/the-ledger-reconciliation-slice",
            "--command", "/portage:pull_request",
        ],
        "review-loop-open": [
            "--record", "spec/dock-scheduling-windows",
            "--command", "/craft:slice spec/dock-scheduling-windows",
        ],
        "slice": [
            "--record", "task/the-berth-allocation-slice",
            "--command", "/craft:plan task/the-berth-allocation-slice",
        ],
    }

    EVAL_RUNS_DIR = (
        REPO_ROOT / "plugins" / "craft" / "evals"
        / "ritual-deliverable-names-its-record" / "runs"
    )

    RITUAL_NAME_RE = re.compile(r"^(.+)-\d+\.txt$")

    def _pre_change_grader(self, tmp_path: Path) -> Path:
        git_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=REPO_ROOT, capture_output=True, text=True, check=True,
            ).stdout.strip()
        )
        rel_path = GRADER.relative_to(git_root)
        blob = subprocess.run(
            ["git", "show", f"{self.BASE_SHA}:{rel_path.as_posix()}"],
            cwd=git_root, capture_output=True, text=True, check=True,
        ).stdout
        pre_change = tmp_path / "pre_change_grader.py"
        pre_change.write_text(blob)
        return pre_change

    def _verdict_lines_and_exit(self, grader: Path, args: list[str], stdin_bytes: bytes):
        result = subprocess.run(
            [sys.executable, str(grader), *args],
            input=stdin_bytes,
            capture_output=True,
        )
        stdout = result.stdout.decode("utf-8")
        verdict_lines = [
            line for line in stdout.splitlines()
            if line.startswith("record-link:") or line.startswith("next-command:")
        ]
        return verdict_lines, result.returncode

    def test_regrading_every_committed_capture_reproduces_the_original_verdicts(self, tmp_path):
        pre_change_grader = self._pre_change_grader(tmp_path)

        captures = sorted(
            p for p in self.EVAL_RUNS_DIR.glob("*.txt") if not p.name.endswith(".stderr.txt")
        )
        assert len(captures) == 45, (
            f"expected 45 committed captures under runs/, found {len(captures)} — "
            "the regression pin's own enumeration disagrees with the task's premise"
        )

        checked = 0
        for capture in captures:
            match = self.RITUAL_NAME_RE.match(capture.name)
            assert match, f"capture filename {capture.name!r} did not match the expected shape"
            ritual = match.group(1)
            args = self.RITUAL_GRADER_ARGS[ritual]
            stdin_bytes = capture.read_bytes()

            old_lines, old_exit = self._verdict_lines_and_exit(
                pre_change_grader, args, stdin_bytes
            )
            new_lines, new_exit = self._verdict_lines_and_exit(GRADER, args, stdin_bytes)

            assert new_lines == old_lines, (
                f"{capture.name}: record-link/next-command verdict moved — "
                f"was {old_lines}, now {new_lines}"
            )
            assert new_exit == old_exit, (
                f"{capture.name}: exit code moved — was {old_exit}, now {new_exit}"
            )
            checked += 1

        assert checked == 45
