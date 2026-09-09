"""Tests for the maturity declaration writer.

The writer is the inverse of `maturity_resolve.py`: given an
agent-instruction file's path and one vocabulary word, it appends a
`## Project Maturity` section and keeps the write only when re-running the
real resolver against the composed bytes reads back `level: <word>` /
`reason: declared`. Any other outcome refuses, leaving the file exactly as
it was, and exits 2 with a stable `reason-code:` on stderr.

Stdout on success (exit 0):

    declared: <level>

Exit codes:
  0 → written, and the round-trip through `maturity_resolve.py` confirmed
  2 → refused, file unchanged — reason-code on stderr:
      invalid-level, path-is-directory, invalid-utf8-file,
      already-declared, self-check-failed, write-failed
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
DECLARE = SCRIPTS_DIR / "maturity_declare.py"
RESOLVER = SCRIPTS_DIR / "maturity_resolve.py"

sys.path.insert(0, str(SCRIPTS_DIR))

MARKER = "TOTALLY-DISTINCTIVE-FIXTURE-MARKER-4471"

NO_SECTION_AT_ALL = f"""\
# Some Repo

## Other Section

Nothing about maturity here. {MARKER}
"""

ALREADY_DECLARED_EARLY = f"""\
# Some Repo

## Project Maturity

early — {MARKER}
"""

FENCED_HEADING_ONLY = f"""\
# Some Repo

Here is an example of the section, for illustration:

```markdown
## Project Maturity

production
```

No real declaration above — just documentation. {MARKER}
"""

UNCLOSED_FENCE = f"""\
# Some Repo

## Other Section

```markdown
this fence never closes {MARKER}
"""

NOT_UTF8 = b"\xff\xfe\x00 not valid utf-8"

LEVELS = ("prototype", "early", "production")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(DECLARE), *args],
        capture_output=True,
    )


def _run_resolver(text_bytes: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESOLVER)],
        input=text_bytes,
        capture_output=True,
    )


def _stdout(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode("utf-8")


def _stderr(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode("utf-8")


def _reason_code(result: subprocess.CompletedProcess) -> str:
    for line in _stderr(result).splitlines():
        if "reason-code:" in line:
            return line.split("reason-code:", 1)[1].strip()
    return ""


def _resolver_level_and_reason(text_bytes: bytes) -> tuple[str, str]:
    result = _run_resolver(text_bytes)
    assert result.returncode == 0, _stderr(result)
    lines = _stdout(result).splitlines()
    got = dict(line.split(": ", 1) for line in lines if ": " in line)
    return got["level"], got["reason"]


# ---------------------------------------------------------------------------
# Round-trip: writing into a file with no declaration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", LEVELS)
def test_round_trip_into_file_with_no_declaration(tmp_path, level):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    result = _run(str(target), level)

    assert result.returncode == 0, _stderr(result)
    written = target.read_bytes()
    got_level, got_reason = _resolver_level_and_reason(written)
    assert got_level == level
    assert got_reason == "declared"


@pytest.mark.parametrize("level", LEVELS)
def test_round_trip_creates_file_that_does_not_exist(tmp_path, level):
    target = tmp_path / "CLAUDE.md"
    assert not target.exists()

    result = _run(str(target), level)

    assert result.returncode == 0, _stderr(result)
    assert target.exists()
    got_level, got_reason = _resolver_level_and_reason(target.read_bytes())
    assert got_level == level
    assert got_reason == "declared"


@pytest.mark.parametrize("level", LEVELS)
def test_round_trip_when_last_byte_is_not_a_newline(tmp_path, level):
    target = tmp_path / "CLAUDE.md"
    body_no_trailing_newline = NO_SECTION_AT_ALL.rstrip("\n")
    assert not body_no_trailing_newline.endswith("\n")
    target.write_text(body_no_trailing_newline, encoding="utf-8")

    result = _run(str(target), level)

    assert result.returncode == 0, _stderr(result)
    got_level, got_reason = _resolver_level_and_reason(target.read_bytes())
    assert got_level == level
    assert got_reason == "declared"


# ---------------------------------------------------------------------------
# Existing unfenced heading refuses
# ---------------------------------------------------------------------------


def test_existing_unfenced_heading_refuses_and_leaves_file_unchanged(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(ALREADY_DECLARED_EARLY, encoding="utf-8")
    original = target.read_bytes()

    # Request a DIFFERENT level than the one already declared, so the
    # unchanged-bytes assertion cannot pass by coincidence.
    result = _run(str(target), "production")

    assert result.returncode == 2
    assert _reason_code(result) == "already-declared"
    assert target.read_bytes() == original


# ---------------------------------------------------------------------------
# Fenced heading is not an existing declaration
# ---------------------------------------------------------------------------


def test_fenced_heading_is_not_treated_as_existing_declaration(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(FENCED_HEADING_ONLY, encoding="utf-8")

    result = _run(str(target), "early")

    assert result.returncode == 0, _stderr(result)
    got_level, got_reason = _resolver_level_and_reason(target.read_bytes())
    assert got_level == "early"
    assert got_reason == "declared"


# ---------------------------------------------------------------------------
# Level outside the closed vocabulary
# ---------------------------------------------------------------------------


def test_level_outside_closed_vocabulary_refuses_and_leaves_file_unchanged(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    original = target.read_bytes()

    result = _run(str(target), "banana")

    assert result.returncode == 2
    assert _reason_code(result) == "invalid-level"
    assert target.read_bytes() == original


# ---------------------------------------------------------------------------
# Path naming a directory, and non-UTF-8 bytes
# ---------------------------------------------------------------------------


def test_directory_path_refuses_and_creates_nothing(tmp_path):
    target = tmp_path / "a_directory"
    target.mkdir()

    result = _run(str(target), "prototype")

    assert result.returncode == 2
    assert _reason_code(result) == "path-is-directory"
    assert list(tmp_path.iterdir()) == [target]
    assert target.is_dir()


def test_non_utf8_file_refuses_and_leaves_file_unchanged(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_bytes(NOT_UTF8)
    original = target.read_bytes()

    result = _run(str(target), "prototype")

    assert result.returncode == 2
    assert _reason_code(result) == "invalid-utf8-file"
    assert target.read_bytes() == original


# ---------------------------------------------------------------------------
# Compare-and-swap, proven with a real interleaving
# ---------------------------------------------------------------------------


def test_compare_and_swap_refuses_a_real_concurrent_write(tmp_path):
    from maturity_declare import lock_path_for  # noqa: PLC0415

    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    lock_path = lock_path_for(target)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)

    result_holder: dict[str, subprocess.CompletedProcess] = {}

    def run_writer_a():
        result_holder["a"] = _run(str(target), "prototype")

    thread = threading.Thread(target=run_writer_a)
    thread.start()

    # Give writer A a real chance to complete its initial read and self-check
    # and block on the lock before we simulate writer B completing a full
    # declaration underneath it.
    time.sleep(0.3)
    assert thread.is_alive(), "writer A finished before we could interleave — test is not real"

    winning_content = "# Some Repo\n\n## Project Maturity\n\nearly\n"
    target.write_text(winning_content, encoding="utf-8")

    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)

    thread.join(timeout=10)
    assert not thread.is_alive(), "writer A never woke up after the lock was released"

    result_a = result_holder["a"]
    assert result_a.returncode == 2
    assert _reason_code(result_a) == "concurrent-modification"
    assert MARKER not in _stdout(result_a)
    assert MARKER not in _stderr(result_a)

    final_bytes = target.read_bytes()
    assert final_bytes == winning_content.encode("utf-8")
    got_level, got_reason = _resolver_level_and_reason(final_bytes)
    assert got_level == "early"
    assert got_reason == "declared"


# ---------------------------------------------------------------------------
# Failing os.replace is caught, not an unhandled traceback
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="uses BSD chflags(UF_IMMUTABLE) to make os.replace genuinely fail",
)
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses immutable-flag enforcement, so this test would pass vacuously",
)
def test_replace_failure_is_caught_and_original_is_intact(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    original = target.read_bytes()

    os.chflags(str(target), stat.UF_IMMUTABLE)
    try:
        result = _run(str(target), "prototype")

        assert result.returncode == 2
        assert _reason_code(result) == "write-failed"
        assert "Traceback" not in _stderr(result)
        assert MARKER not in _stdout(result)
        assert MARKER not in _stderr(result)
        assert target.read_bytes() == original
    finally:
        os.chflags(str(target), 0)


# ---------------------------------------------------------------------------
# Self-check reverts, proven with a positive control
# ---------------------------------------------------------------------------


def test_self_check_revert_fires_on_genuine_round_trip_failure(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(UNCLOSED_FENCE, encoding="utf-8")
    original = target.read_bytes()

    # Sanity: confirm the fixture itself really does swallow an appended
    # heading, so this is a genuine positive control rather than a fixture
    # that happens to pass for an unrelated reason.
    hypothetically_appended = UNCLOSED_FENCE + "\n## Project Maturity\n\nprototype\n"
    sanity_level, sanity_reason = _resolver_level_and_reason(
        hypothetically_appended.encode("utf-8")
    )
    assert sanity_reason != "declared", "fixture does not actually swallow the heading"

    result = _run(str(target), "prototype")

    assert result.returncode == 2
    assert _reason_code(result) == "self-check-failed"
    assert target.read_bytes() == original


# ---------------------------------------------------------------------------
# Never echoes file content
# ---------------------------------------------------------------------------


def _refusal_cases(tmp_path):
    already = tmp_path / "already.md"
    already.write_text(ALREADY_DECLARED_EARLY, encoding="utf-8")

    bad_level = tmp_path / "bad_level.md"
    bad_level.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    directory = tmp_path / "a_directory_marker"
    directory.mkdir()

    not_utf8 = tmp_path / "not_utf8.md"
    not_utf8.write_bytes(NOT_UTF8)

    unclosed = tmp_path / "unclosed.md"
    unclosed.write_text(UNCLOSED_FENCE, encoding="utf-8")

    return [
        (str(already), "production"),
        (str(bad_level), "banana"),
        (str(directory), "prototype"),
        (str(not_utf8), "prototype"),
        (str(unclosed), "prototype"),
    ]


def test_no_refusal_path_echoes_file_content(tmp_path):
    # The compare-and-swap race (already-declared) and the os.replace
    # write-failure (write-failed) are the two remaining refusal paths in
    # the reason-code vocabulary. Each already has a dedicated test that
    # drives it with a real interleaving / a real replace failure —
    # test_compare_and_swap_refuses_a_real_concurrent_write and
    # test_replace_failure_is_caught_and_original_is_intact — so the
    # marker-absence assertion for those two lives there instead of being
    # re-driven through a synthetic case here.
    for path_str, level in _refusal_cases(tmp_path):
        result = _run(path_str, level)
        assert result.returncode == 2, f"expected refusal for {path_str}"
        assert MARKER not in _stdout(result)
        assert MARKER not in _stderr(result)


# ---------------------------------------------------------------------------
# Mode preservation across the atomic replace
# ---------------------------------------------------------------------------


def test_declare_preserves_an_existing_files_mode(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    target.chmod(0o644)

    result = _run(str(target), "early")

    assert result.returncode == 0, _stderr(result)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_declare_preserves_an_unusual_existing_mode_not_just_0644(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    target.chmod(0o664)

    result = _run(str(target), "early")

    assert result.returncode == 0, _stderr(result)
    assert stat.S_IMODE(target.stat().st_mode) == 0o664


def test_declare_creating_a_new_file_gets_the_normal_default_mode(tmp_path):
    control = tmp_path / "a_normal_new_file"
    control.write_text("x", encoding="utf-8")
    expected_mode = stat.S_IMODE(control.stat().st_mode)

    target = tmp_path / "CLAUDE.md"
    assert not target.exists()

    result = _run(str(target), "early")

    assert result.returncode == 0, _stderr(result)
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    assert stat.S_IMODE(target.stat().st_mode) != 0o600


# ---------------------------------------------------------------------------
# No litter left behind on a successful declaration
# ---------------------------------------------------------------------------


def test_successful_declaration_leaves_no_other_file_in_the_directory(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    result = _run(str(target), "early")

    assert result.returncode == 0, _stderr(result)
    assert list(tmp_path.iterdir()) == [target]


# ---------------------------------------------------------------------------
# PermissionError paths funnel into the fail-closed path, not a traceback
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses mode bits, so chmod(0o000) leaves this test "
    "unable to observe its own subject and it would pass vacuously",
)
def test_existing_unreadable_file_refuses_without_a_traceback(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    original = target.read_bytes()
    target.chmod(0o000)
    try:
        result = _run(str(target), "early")

        assert result.returncode == 2
        assert "Traceback" not in _stderr(result)
        assert _reason_code(result) != ""
    finally:
        target.chmod(0o644)
    assert target.read_bytes() == original


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory write-permission enforcement, so this "
    "test would pass vacuously",
)
def test_read_only_parent_directory_refuses_without_a_traceback(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    original = target.read_bytes()
    tmp_path.chmod(0o555)
    try:
        result = _run(str(target), "early")

        assert result.returncode == 2
        assert "Traceback" not in _stderr(result)
        assert _reason_code(result) != ""
    finally:
        tmp_path.chmod(0o755)
    assert target.read_bytes() == original


# ---------------------------------------------------------------------------
# The concurrent-modification race gets its own reason-code
# ---------------------------------------------------------------------------


def test_concurrent_modification_race_uses_its_own_reason_code_not_already_declared(
    tmp_path,
):
    from maturity_declare import lock_path_for  # noqa: PLC0415

    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    lock_path = lock_path_for(target)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)

    result_holder: dict[str, subprocess.CompletedProcess] = {}

    def run_writer_a():
        result_holder["a"] = _run(str(target), "prototype")

    thread = threading.Thread(target=run_writer_a)
    thread.start()

    time.sleep(0.3)
    assert thread.is_alive(), "writer A finished before we could interleave — test is not real"

    target.write_text("# Some Repo\n\n## Project Maturity\n\nearly\n", encoding="utf-8")

    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)

    thread.join(timeout=10)
    assert not thread.is_alive(), "writer A never woke up after the lock was released"

    result_a = result_holder["a"]
    assert result_a.returncode == 2
    assert _reason_code(result_a) == "concurrent-modification"
    assert _reason_code(result_a) != "already-declared"


# ---------------------------------------------------------------------------
# Usage error exits 2 with a reason-code, per the documented contract
# ---------------------------------------------------------------------------


def test_usage_error_exits_2_with_a_reason_code():
    result = subprocess.run([str(DECLARE)], capture_output=True)

    assert result.returncode == 2
    assert _reason_code(result) == "usage-error"


# ---------------------------------------------------------------------------
# Writing through a symlinked agent-instruction file
# ---------------------------------------------------------------------------


def test_declare_refuses_a_symlinked_target_and_leaves_the_real_target_untouched(tmp_path):
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    sibling = other_repo / "REAL_CLAUDE.md"
    sibling.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    original_sibling = sibling.read_bytes()

    repo = tmp_path / "repo"
    repo.mkdir()
    link = repo / "CLAUDE.md"
    link.symlink_to(sibling)

    result = _run(str(link), "early")

    assert result.returncode == 2
    assert _reason_code(result) == "target-is-symlink"
    assert link.is_symlink(), "the symlink itself must be left exactly as it was"
    assert os.readlink(str(link)) == str(sibling)
    assert sibling.read_bytes() == original_sibling, (
        "a different repository's agent-instruction file must never be modified"
    )


# ---------------------------------------------------------------------------
# Mode 755, bare shebang invocation
# ---------------------------------------------------------------------------


def test_script_runs_bare_via_its_own_documented_invocation(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    result = subprocess.run(
        [str(DECLARE), str(target), "prototype"],
        capture_output=True,
    )

    assert result.returncode == 0, (
        f"could not execute directly: exit {result.returncode}, "
        f"stderr={result.stderr.decode('utf-8', 'replace')[:200]}"
    )


# ---------------------------------------------------------------------------
# Success token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", LEVELS)
def test_success_token_on_stdout_nothing_on_stderr(tmp_path, level):
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    result = _run(str(target), level)

    assert result.returncode == 0
    assert _stdout(result) == f"declared: {level}\n"
    assert _stderr(result) == ""


# ---------------------------------------------------------------------------
# A permission change landing inside the locked section (between the
# initial read and the compare-and-swap re-read) is caught, not an
# unhandled traceback leaking the absolute path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses mode bits, so chmod(0o000) leaves this test "
    "unable to observe its own subject and it would pass vacuously",
)
def test_permission_change_inside_the_locked_section_refuses_without_a_traceback(tmp_path):
    from maturity_declare import lock_path_for  # noqa: PLC0415

    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")
    original = target.read_bytes()

    lock_path = lock_path_for(target)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)

    result_holder: dict[str, subprocess.CompletedProcess] = {}

    def run_writer_a():
        result_holder["a"] = _run(str(target), "prototype")

    thread = threading.Thread(target=run_writer_a)
    thread.start()

    # Give writer A a real chance to complete its initial read and self-check
    # (both happen before the lock is even attempted) and block on the lock,
    # before we revoke read permission on the target out from under it — the
    # exact race window between the initial read and the compare-and-swap
    # re-read that happens once the lock is acquired.
    time.sleep(0.3)
    assert thread.is_alive(), "writer A finished before we could interleave — test is not real"

    target.chmod(0o000)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

        thread.join(timeout=10)
        assert not thread.is_alive(), "writer A never woke up after the lock was released"

        result_a = result_holder["a"]
        assert result_a.returncode == 2
        assert "Traceback" not in _stderr(result_a)
        assert _reason_code(result_a) == "write-failed"
        assert MARKER not in _stdout(result_a)
        assert MARKER not in _stderr(result_a)
        assert str(target) not in _stderr(result_a)
    finally:
        target.chmod(0o644)
    assert target.read_bytes() == original
