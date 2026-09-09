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

import json
import subprocess
from pathlib import Path

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import dispatch
from portage.cli import pr as pr_cli

from trailhead.vcs.github import GitHubProvider


class _SpyRunner:
    """Records every subprocess call; answers just enough gh/git to let a
    merge proceed when the gate lets it through."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        cmd_str = " ".join(cmd)
        if "config" in cmd_str and "user.email" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
        if "view" in cmd_str and "--json" in cmd_str:
            payload = {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "isDraft": False,
                "headRefName": "feat",
            }
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        if "pr" in cmd_str and "merge" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def merge_attempted(self) -> bool:
        return any("pr" in c and "merge" in c for c in self.calls)


def _make_manifest(tmp_path: Path, wt: Path) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "members": [{"name": "api", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
            }
        )
    )
    return p


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "group.toml"
    p.write_text(content, encoding="utf-8")
    return p


def _run_merge(tmp_path, monkeypatch, capsys, toml_content: str | None):
    wt = tmp_path / "wt" / "api"
    wt.mkdir(parents=True)
    manifest = _make_manifest(tmp_path, wt)
    spy = _SpyRunner()
    monkeypatch.setattr(pr_cli, "get_provider", lambda *a, **k: GitHubProvider(runner=spy))

    argv = ["merge", "--manifest", str(manifest)]
    if toml_content is not None:
        toml = _write_toml(tmp_path, toml_content)
        argv += ["--toml", str(toml)]
    argv += [f"{wt}:1:api"]

    rc = dispatch.main(argv)
    return rc, spy, capsys.readouterr()


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
