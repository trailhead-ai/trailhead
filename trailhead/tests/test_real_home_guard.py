"""The suite-wide guard rail against a test reaching the developer's real home.

`_redirect_claude_dir` pins `TRAILHEAD_CLAUDE_DIR`, which protects everything
resolving through `_claude_dir`. It gives `claude_config_file` nothing: that
resolver ignores the seam by design, so an `env` of `None` — or one lacking
`HOME` — walks straight to `Path.home()` and names the operator's real
`~/.claude.json`, an OAuth-secret-bearing file. `_forbid_real_home` turns that
fall-through into a loud failure (Axiom 6) instead of a quiet write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailhead.harness import claude_config_file


class TestTheGuardFires:
    def test_resolving_the_config_file_without_an_injected_home_fails_loudly(self):
        with pytest.raises(AssertionError) as exc_info:
            claude_config_file(None)
        assert "HOME" in str(exc_info.value)

    def test_an_env_without_home_fails_loudly_too(self):
        with pytest.raises(AssertionError):
            claude_config_file({"SOMETHING_ELSE": "x"})

    def test_the_claude_dir_resolver_is_covered_as_well(self):
        from trailhead.harness.claude_code import _claude_dir

        with pytest.raises(AssertionError):
            _claude_dir({})


class TestTheGuardDoesNotGetInTheWay:
    def test_an_injected_home_resolves_normally(self, tmp_path):
        assert claude_config_file({"HOME": str(tmp_path)}) == tmp_path / ".claude.json"

    @pytest.mark.real_home
    def test_the_marker_opts_a_test_back_in(self):
        assert Path.home().is_absolute()
