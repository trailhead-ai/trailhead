"""merge_prs.py's auto_merge gate — fail-closed default.

`trailhead.vcs.github._merge_prs` reads `auto_merge` from the `[release]` block
of the group TOML, mirroring the existing `merge_order` read. When the key is
absent or false it refuses to merge — before any `gh`/`git` subprocess call —
raising `AutoMergeDisabledError`. `merge_prs.py` (the thin CLI) surfaces that
refusal as a clean exit 2 with the message on stderr, the same contract it
already has for `MergeOrderRequiredError`/`MergeConfigError`.

These tests exercise the real `GitHubProvider` (an injected spy runner, no
network) through the `merge_prs.py` CLI entry point, so both halves of the
gate — the read in `trailhead/vcs/github.py` and the CLI's except-tuple in
`merge_prs.py` — are proven together.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _script_loader import load_script

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
        if "merge" in cmd_str and "--merge" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def merge_attempted(self) -> bool:
        return any("merge" in c and "--merge" in c for c in self.calls)


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


def _run_merge_prs(tmp_path, monkeypatch, capsys, toml_content: str | None):
    wt = tmp_path / "wt" / "api"
    wt.mkdir(parents=True)
    manifest = _make_manifest(tmp_path, wt)
    spy = _SpyRunner()
    mod = load_script("merge_prs")
    monkeypatch.setattr(mod, "get_provider", lambda *a, **k: GitHubProvider(runner=spy))

    argv = ["--manifest", str(manifest)]
    if toml_content is not None:
        toml = _write_toml(tmp_path, toml_content)
        argv += ["--toml", str(toml)]
    argv += [f"{wt}:1:api"]

    rc = mod.main(argv)
    return rc, spy, capsys.readouterr()


class TestAutoMergeDefaultRefuses:
    def test_no_auto_merge_key_refuses_with_nonzero_exit(self, tmp_path, monkeypatch, capsys):
        rc, spy, out = _run_merge_prs(tmp_path, monkeypatch, capsys, "[release]\n")
        assert rc == 2
        assert not spy.merge_attempted()

    def test_no_toml_at_all_refuses(self, tmp_path, monkeypatch, capsys):
        rc, spy, out = _run_merge_prs(tmp_path, monkeypatch, capsys, None)
        assert rc == 2
        assert not spy.merge_attempted()


class TestAutoMergeTrueProceeds:
    def test_auto_merge_true_merges(self, tmp_path, monkeypatch, capsys):
        rc, spy, out = _run_merge_prs(tmp_path, monkeypatch, capsys, "[release]\nauto_merge = true\n")
        assert rc == 0
        assert spy.merge_attempted()
        result = json.loads(out.out)
        assert any("1" in k for k in result["merged"])


class TestAutoMergeExplicitFalseRefuses:
    def test_explicit_false_refuses_same_as_default(self, tmp_path, monkeypatch, capsys):
        rc, spy, out = _run_merge_prs(tmp_path, monkeypatch, capsys, "[release]\nauto_merge = false\n")
        assert rc == 2
        assert not spy.merge_attempted()


class TestRefusalMessageNamesRemediation:
    def test_stderr_names_the_release_auto_merge_true_remediation(self, tmp_path, monkeypatch, capsys):
        rc, spy, out = _run_merge_prs(tmp_path, monkeypatch, capsys, "[release]\n")
        assert rc == 2
        assert "[release] auto_merge = true" in out.err

    def test_monitor_md_stop_report_names_the_same_remediation(self):
        monitor_md = (
            Path(__file__).resolve().parents[1] / "plugins" / "portage" / "agents" / "monitor.md"
        )
        text = monitor_md.read_text(encoding="utf-8")
        assert "[release] auto_merge = true" in text
