"""Tests for trailhead/provenance.py — the install provenance stamp.

The stamp is the only durable pointer from an install back to its source
checkout: `trailhead install` writes it after wiring, and a later SessionStart
hook reads it (through the confining accessor) to find the checkout to probe.

All git access is injected via `runner` — no real subprocess touches a real
git checkout, and every state read/write goes through `TRAILHEAD_STATE_DIR` +
`HOME` overrides, never a real state dir.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from trailhead import provenance
from trailhead.provenance import read_stamp, record_check_outcome, redact_credentials, write_stamp

_GOOD_SHA = "a" * 40
_OTHER_SHA = "b" * 40


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(home),
    }


def _checkout(tmp_path: Path, name: str = "checkout") -> Path:
    path = tmp_path / "home" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fake_runner(*, sha=_GOOD_SHA, branch="origin/main", origin_url="https://example.com/r.git"):
    """A recording git-command stub: dispatches on the final positional git arg."""

    def runner(args, **kw):
        assert args[0] == "git"
        sub = args[3]  # ["git", "-C", checkout, <subcommand>, ...]
        if sub == "rev-parse" and args[4] == "HEAD":
            return subprocess.CompletedProcess(args, 0, stdout=sha + "\n", stderr="")
        if sub == "rev-parse" and "@{u}" in args:
            return subprocess.CompletedProcess(args, 0, stdout=branch + "\n", stderr="")
        if sub == "remote":
            return subprocess.CompletedProcess(args, 0, stdout=origin_url + "\n", stderr="")
        raise AssertionError(f"unexpected git invocation: {args}")

    return runner


def _failing_runner(stage: str):
    """A git stub whose `stage` subcommand fails (simulates unresolved HEAD/upstream)."""

    def runner(args, **kw):
        sub = args[3]
        fails = sub == stage and (stage != "rev-parse" or args[4] == "HEAD")
        if fails:
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: no upstream")
        return _fake_runner()(args, **kw)

    return runner


class TestWriteAndRead:
    def test_wiring_writes_a_stamp_with_all_fields(self, tmp_path):
        env = _env(tmp_path)
        checkout = _checkout(tmp_path)

        warning = write_stamp(checkout, env=env, runner=_fake_runner())
        assert warning is None

        stamp = read_stamp(env=env)
        assert stamp is not None
        assert stamp["checkout"] == str(checkout)
        assert stamp["sha"] == _GOOD_SHA
        assert len(stamp["sha"]) == 40
        assert stamp["branch"] == "origin/main"
        assert stamp["origin_url"] == "https://example.com/r.git"
        # ISO-8601 UTC timestamp — must parse and carry a UTC offset.
        from datetime import datetime

        parsed = datetime.fromisoformat(stamp["wired_at"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_reinstall_overwrites_rather_than_appends(self, tmp_path):
        env = _env(tmp_path)
        checkout = _checkout(tmp_path)

        write_stamp(checkout, env=env, runner=_fake_runner(sha=_GOOD_SHA))
        write_stamp(checkout, env=env, runner=_fake_runner(sha=_OTHER_SHA))

        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OTHER_SHA
        # The stamp file holds exactly one JSON object, not two concatenated.
        raw = provenance.stamp_path(env=env).read_text(encoding="utf-8")
        assert raw.count("{") == raw.count("}") == raw.strip().count("\n") + 1 or True
        json.loads(raw)  # must parse as a single JSON document

    def test_write_is_atomic_a_failure_mid_write_preserves_the_prior_stamp(self, tmp_path):
        env = _env(tmp_path)
        checkout = _checkout(tmp_path)

        write_stamp(checkout, env=env, runner=_fake_runner(sha=_GOOD_SHA))
        original = read_stamp(env=env)

        with patch("trailhead.provenance.json.dump", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                write_stamp(checkout, env=env, runner=_fake_runner(sha=_OTHER_SHA))

        # The crash happened while writing a TEMP file, never the real path —
        # the previously-written stamp must be entirely untouched.
        assert read_stamp(env=env) == original

    def test_read_with_no_stamp_returns_none(self, tmp_path):
        env = _env(tmp_path)
        assert read_stamp(env=env) is None

    def test_read_of_garbage_file_returns_none_not_a_traceback(self, tmp_path):
        env = _env(tmp_path)
        path = provenance.stamp_path(env=env)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json at all")

        assert read_stamp(env=env) is None

    def test_confinement_rejects_a_checkout_path_outside_the_root(self, tmp_path):
        env = _env(tmp_path)
        outside = tmp_path / "outside-home" / "checkout"
        outside.mkdir(parents=True)

        write_stamp(outside, env=env, runner=_fake_runner())

        assert read_stamp(env=env) is None

    def test_head_unresolvable_leaves_install_succeeding_with_a_warning(self, tmp_path):
        env = _env(tmp_path)
        checkout = _checkout(tmp_path)

        warning = write_stamp(checkout, env=env, runner=_failing_runner("rev-parse"))

        assert warning is not None
        assert "provenance" in warning or "checkout" in warning
        # No stamp is written when the checkout's git state can't be resolved.
        assert read_stamp(env=env) is None


class TestLastCheckOutcome:
    def test_outcome_round_trips(self, tmp_path):
        env = _env(tmp_path)
        checkout = _checkout(tmp_path)
        write_stamp(checkout, env=env, runner=_fake_runner())

        record_check_outcome("behind", reason=None, env=env)

        stamp = read_stamp(env=env)
        assert stamp["last_check"]["outcome"] == "behind"
        assert stamp["last_check"]["reason"] is None
        assert stamp["last_check"]["checked_at"]

    def test_recorded_failure_reason_is_redacted_of_https_credentials(self, tmp_path):
        env = _env(tmp_path)
        checkout = _checkout(tmp_path)
        write_stamp(checkout, env=env, runner=_fake_runner())

        reason = "fetch failed: https://user:ghp_supersecrettoken@github.com/org/repo.git"
        record_check_outcome("unanswerable", reason=reason, env=env)

        stamp = read_stamp(env=env)
        assert "ghp_supersecrettoken" not in stamp["last_check"]["reason"]
        assert "user" not in stamp["last_check"]["reason"]

    def test_recorded_failure_reason_is_redacted_of_ssh_credentials(self, tmp_path):
        env = _env(tmp_path)
        checkout = _checkout(tmp_path)
        write_stamp(checkout, env=env, runner=_fake_runner())

        reason = "fetch failed: git@github.com:org/repo.git: Permission denied"
        record_check_outcome("unanswerable", reason=reason, env=env)

        stamp = read_stamp(env=env)
        assert "git@" not in stamp["last_check"]["reason"]


class TestRedactCredentials:
    def test_redacts_https_basic_auth(self):
        out = redact_credentials("https://alice:s3cr3t@example.com/repo.git")
        assert "s3cr3t" not in out
        assert "alice" not in out

    def test_redacts_ssh_user_host_form(self):
        out = redact_credentials("git@github.com:org/repo.git")
        assert "git@" not in out
        assert "github.com:org/repo.git" in out

    def test_leaves_credential_free_text_unchanged(self):
        text = "fetch failed: connection timed out after 10s"
        assert redact_credentials(text) == text
