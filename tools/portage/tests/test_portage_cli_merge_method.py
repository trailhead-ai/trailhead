"""`portage merge`'s merge_method gate — configurable strategy, fail-closed.

``trailhead.vcs.github`` reads ``merge_method`` from the ``[release]`` block of
the group TOML (mirroring the ``auto_merge``/``merge_order`` reads). An
unrecognized value refuses — before any ``gh``/``git`` subprocess call —
raising ``MergeMethodInvalidError``. The ``merge`` subcommand surfaces that
refusal as a clean exit 2 with the message on stderr, the same contract it has
for ``AutoMergeDisabledError``/``MergeOrderRequiredError``/``MergeConfigError``.

These tests exercise the real ``GitHubProvider`` (an injected spy runner, no
network) through ``dispatch.main(["merge", ...])``, so both halves of the gate
— the read in ``trailhead/vcs/github.py`` and the CLI's except-tuple in
``portage.cli.pr`` — are proven together.
"""

from __future__ import annotations

# `_run_merge` drives the real GitHubProvider through `dispatch.main(["merge", ...])`
# against a spy runner; it is defined next to the auto_merge gate's tests and shared
# here rather than restated, so both gates are exercised through one harness.
from test_portage_cli_auto_merge import _run_merge


class TestMergeMethodInvalidExitsClean:
    def test_invalid_merge_method_exits_2_with_message_on_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        rc, spy, out = _run_merge(
            tmp_path,
            monkeypatch,
            capsys,
            '[release]\nauto_merge = true\nmerge_method = "sqush"\n',
        )
        assert rc == 2
        assert not spy.merge_attempted()
        assert "sqush" in out.err


class TestMergeMethodValidValuesProceed:
    def test_squash_configured_merges(self, tmp_path, monkeypatch, capsys):
        rc, spy, out = _run_merge(
            tmp_path,
            monkeypatch,
            capsys,
            '[release]\nauto_merge = true\nmerge_method = "squash"\n',
        )
        assert rc == 0
        assert spy.merge_attempted()
        merge_call = next(c for c in spy.calls if "pr" in c and "merge" in c)
        assert "--squash" in merge_call


class TestAutoMergeGateFiresFirst:
    def test_auto_merge_unset_and_merge_method_invalid_refuses_as_auto_merge(
        self, tmp_path, monkeypatch, capsys
    ):
        """Pins gate ordering: with auto_merge unset AND merge_method invalid,
        the refusal is the auto_merge one, not the merge_method one — the new
        check cannot displace the fail-closed auto_merge gate."""
        rc, spy, out = _run_merge(
            tmp_path,
            monkeypatch,
            capsys,
            '[release]\nmerge_method = "sqush"\n',
        )
        assert rc == 2
        assert not spy.merge_attempted()
        assert "[release] auto_merge = true" in out.err
        assert "sqush" not in out.err
