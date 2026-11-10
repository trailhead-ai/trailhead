"""`portage await-outcome` — wait on monitor's outcome file, print the parsed outcome.

Runs the real CLI through ``dispatch.main`` against files under ``tmp_path``.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import dispatch

PARSED, PENDING, MALFORMED, UNREADABLE = 0, 10, 11, 12


@pytest.fixture
def outdir(tmp_path):
    d = tmp_path / "out"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o700)
    return d


def _run(capsys, path, timeout="2", interval="0.05"):
    rc = dispatch.main(
        ["await-outcome", "--path", str(path), "--timeout", timeout, "--interval", interval]
    )
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.mark.parametrize(
    "line, printed",
    [
        ("READY all green\n", "READY all green"),
        ("MERGED\n", "MERGED"),
        ("BLOCKED three fix cycles\n", "BLOCKED three fix cycles"),
        ("STOPPED operator reason\n", "STOPPED operator reason"),
    ],
)
def test_prints_the_parsed_outcome_of_a_file_already_holding_a_line(outdir, capsys, line, printed):
    f = outdir / "outcome"
    f.write_text(line)
    rc, out, _ = _run(capsys, f)
    assert (rc, out.strip()) == (PARSED, printed)


def test_reads_a_line_written_partway_through_the_wait(outdir, capsys):
    f = outdir / "outcome"
    t = threading.Timer(0.4, lambda: f.write_text("READY late\n"))
    t.start()
    started = time.monotonic()
    rc, out, _ = _run(capsys, f, timeout="5")
    t.join()
    assert (rc, out.strip()) == (PARSED, "READY late")
    assert time.monotonic() - started < 4


@pytest.mark.parametrize("seed", [None, ""])
def test_pending_when_the_file_stays_absent_or_empty(outdir, capsys, seed):
    f = outdir / "outcome"
    if seed is not None:
        f.write_text(seed)
    started = time.monotonic()
    rc, out, _ = _run(capsys, f, timeout="1", interval="0.1")
    elapsed = time.monotonic() - started
    assert (rc, out.strip()) == (PENDING, "PENDING")
    assert 0.9 <= elapsed < 1 + 0.1 + 0.5


@pytest.mark.parametrize("line", ["READY\n", "DONE all good\n", "STOPPED: operator\n"])
def test_malformed_line_exits_malformed_with_a_token_free_reason(outdir, capsys, line):
    f = outdir / "outcome"
    f.write_text(line)
    rc, out, err = _run(capsys, f)
    assert rc == MALFORMED
    assert out == ""
    assert err.startswith("portage: ")
    assert not {"READY", "MERGED", "BLOCKED", "STOPPED", "DONE"} & set(err.replace(":", " ").split())


@pytest.mark.parametrize("timeout", ["571", "601", "-1"])
def test_timeout_above_the_cap_is_refused_before_any_wait(outdir, capsys, timeout):
    started = time.monotonic()
    rc, out, err = _run(capsys, outdir / "outcome", timeout=timeout)
    assert rc == 2
    assert out == ""
    assert err.startswith("portage: ")
    assert time.monotonic() - started < 1


def test_timeout_at_the_cap_is_accepted(outdir, capsys):
    f = outdir / "outcome"
    f.write_text("MERGED\n")
    rc, out, _ = _run(capsys, f, timeout="570")
    assert (rc, out.strip()) == (PARSED, "MERGED")


def test_refuses_a_symlinked_path_without_reading_it(outdir, capsys):
    target = outdir / "real"
    target.write_text("READY from the target\n")
    link = outdir / "outcome"
    link.symlink_to(target)
    rc, out, err = _run(capsys, link)
    assert rc == UNREADABLE
    assert "READY" not in out
    assert err.startswith("portage: ")


def test_refuses_a_non_regular_file(outdir, capsys):
    d = outdir / "outcome"
    d.mkdir()
    rc, out, err = _run(capsys, d)
    assert (rc, out) == (UNREADABLE, "")
    assert err.startswith("portage: ")


def test_refuses_a_group_writable_parent_without_reading(outdir, capsys):
    f = outdir / "outcome"
    f.write_text("READY planted\n")
    os.chmod(outdir, 0o770)
    rc, out, err = _run(capsys, f)
    assert rc == UNREADABLE
    assert "READY" not in out
    assert err.startswith("portage: ")


def test_refuses_a_parent_not_owned_by_the_user(outdir, capsys, monkeypatch):
    f = outdir / "outcome"
    f.write_text("READY planted\n")
    monkeypatch.setattr(os, "getuid", lambda: os.stat(outdir).st_uid + 1)
    rc, out, _ = _run(capsys, f)
    assert rc == UNREADABLE
    assert "READY" not in out


def test_writes_nothing(outdir, capsys):
    f = outdir / "outcome"
    f.write_text("READY all green\n")
    before = (sorted(p.name for p in outdir.iterdir()), f.read_text(), f.stat().st_mtime_ns)
    _run(capsys, f)
    assert (sorted(p.name for p in outdir.iterdir()), f.read_text(), f.stat().st_mtime_ns) == before


def test_a_line_without_its_newline_is_not_parsed_until_it_is_complete(outdir, capsys):
    f = outdir / "outcome"
    f.write_text("READY all gr")

    def finish():
        f.write_text("READY all green\n")

    t = threading.Timer(0.4, finish)
    t.start()
    rc, out, _ = _run(capsys, f, timeout="5")
    t.join()
    assert (rc, out.strip()) == (PARSED, "READY all green")


def test_a_line_that_never_gets_its_newline_stays_pending(outdir, capsys):
    f = outdir / "outcome"
    f.write_text("READY all green")
    rc, out, _ = _run(capsys, f, timeout="1", interval="0.1")
    assert (rc, out.strip()) == (PENDING, "PENDING")
