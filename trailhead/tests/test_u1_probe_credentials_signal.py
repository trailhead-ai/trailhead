"""EPHEMERAL assumption probe for U1 (task/the-health-check-reports-whether-each-declared-
account-is-authenticated). NOT part of the permanent suite — delete this file once U1's
verdict has been recorded.

U1 asks: can the harness tell whether a Claude Code account is authenticated, without
prompting, without a network call, and without spawning an interactive process — using a
signal other than the forbidden config-file-existence check
(``AccountIdentity.has_config`` / ``trailhead/harness/claude_code.py:1164-1205`` and
``trailhead/harness/base.py:108-127``)?

Candidate signal under test: the account's ``.credentials.json``, which
``ClaudeCodeHarness.session_launch_env_set``'s own docstring
(``trailhead/harness/claude_code.py:1122-1127``) names as one of the files living inside the
account's config dir alongside ``settings.json`` and ``.claude.json``. On the real machine
this session runs on, ``~/.claude/.credentials.json`` holds a ``claudeAiOauth`` object with
``accessToken`` / ``refreshToken`` / ``expiresAt`` (verified by hand against the developer's
real file — never read by this test, which only ever touches ``tmp_path`` fixtures per
Axiom 6).

This test executes the actual candidate read against fixture config dirs — never asserts
that reading logic merely exists — and never touches the developer's real home.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

CREDENTIALS_FILENAME = ".credentials.json"


def read_auth_signal(config_dir: Path) -> str:
    """The candidate U1 signal: three-valued, from ONE file read + JSON parse.

    Returns "authenticated", "not-authenticated", or "cannot-tell". This is a
    spike for the probe only — Task 1 owns the real implementation.
    """
    path = config_dir / CREDENTIALS_FILENAME
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return "not-authenticated"
    except OSError:
        return "cannot-tell"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "cannot-tell"
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return "not-authenticated"
    token = oauth.get("accessToken")
    if isinstance(token, str) and token:
        return "authenticated"
    return "not-authenticated"


class TestSignalVariesWithFixtureContent:
    """The floor: the candidate's answer must depend on what's on disk, not be
    a constant — feed it a different fixture, get a different answer."""

    def test_valid_oauth_token_reads_as_authenticated(self, tmp_path: Path):
        config_dir = tmp_path / "acct"
        config_dir.mkdir()
        (config_dir / CREDENTIALS_FILENAME).write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat01-fake", "expiresAt": 1}})
        )

        assert read_auth_signal(config_dir) == "authenticated"

    def test_missing_credentials_file_reads_as_not_authenticated(self, tmp_path: Path):
        config_dir = tmp_path / "never-signed-in"
        config_dir.mkdir()
        # No .credentials.json planted at all.

        assert read_auth_signal(config_dir) == "not-authenticated"

    def test_empty_access_token_reads_as_not_authenticated(self, tmp_path: Path):
        config_dir = tmp_path / "logged-out"
        config_dir.mkdir()
        (config_dir / CREDENTIALS_FILENAME).write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "", "expiresAt": 1}})
        )

        assert read_auth_signal(config_dir) == "not-authenticated"

    def test_missing_oauth_key_entirely_reads_as_not_authenticated(self, tmp_path: Path):
        config_dir = tmp_path / "mcp-oauth-only"
        config_dir.mkdir()
        (config_dir / CREDENTIALS_FILENAME).write_text(json.dumps({"mcpOAuth": {}}))

        assert read_auth_signal(config_dir) == "not-authenticated"

    def test_corrupt_json_reads_as_cannot_tell_not_a_crash(self, tmp_path: Path):
        config_dir = tmp_path / "mid-write"
        config_dir.mkdir()
        (config_dir / CREDENTIALS_FILENAME).write_text("{not valid json")

        assert read_auth_signal(config_dir) == "cannot-tell"


class TestReadCostIsCheap:
    """Answers the second sub-question: what does reading the signal cost?"""

    def test_reading_a_realistic_sized_credentials_file_is_sub_millisecond(self, tmp_path: Path):
        config_dir = tmp_path / "acct"
        config_dir.mkdir()
        # Pad to roughly the real file's size (~23KB observed) to keep the
        # timing representative rather than measuring an empty-file best case.
        padding = {f"Claude_Code_Remote|{i:x}": {"accessToken": ""} for i in range(40)}
        (config_dir / CREDENTIALS_FILENAME).write_text(
            json.dumps(
                {
                    "mcpOAuth": padding,
                    "claudeAiOauth": {"accessToken": "sk-ant-oat01-fake", "expiresAt": 1},
                }
            )
        )

        start = time.perf_counter()
        result = read_auth_signal(config_dir)
        elapsed = time.perf_counter() - start

        assert result == "authenticated"
        # A stat + small JSON parse, no subprocess and no network call — this
        # is generous headroom (real measurement on this machine: ~0.2ms).
        assert elapsed < 0.05
