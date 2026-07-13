"""ASSUMPTION PROBE (ephemeral — delete once Slice D lands its real test file,
tools/portage/tests/test_merge_prs_auto_merge.py).

Resolves the Slice-D unknown: can the merge path read an ``auto_merge`` boolean
from the ``[release]`` block of ``--toml`` (mirroring how ``_load_merge_order``
already reads ``merge_order`` from that same block in
``trailhead/vcs/github.py``), refuse to merge — no ``gh`` call issued — when the
key is absent or false, and can ``merge_prs.py``'s exit code (the existing
signal ``agents/monitor.md`` already documents relying on for the
``merge_order`` gate) carry that refusal?

This file does NOT implement the feature. Everything under "spike" mirrors the
real production functions closely enough to prove the mechanism, without
touching ``trailhead/vcs/github.py`` or ``merge_prs.py`` (out of scope for a
probe).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from _script_loader import load_script

# ---------------------------------------------------------------------------
# Part A — can auto_merge be read from [release] via tomllib, the same way
# _load_merge_order reads merge_order (trailhead/vcs/github.py:416-432)?
# ---------------------------------------------------------------------------


def _spike_load_auto_merge(toml_path: str | None) -> bool:
    """Mirrors _load_merge_order's exact read pattern (same file, same
    [release] table, same tomllib.loads call) — only the key name and return
    type differ. Copied structure, not copied code: proves the same
    tomllib/[release] plumbing already used for merge_order extends cleanly to
    a second key in the same block.
    """
    if not toml_path:
        return False
    p = Path(toml_path)
    if not p.is_file():
        return False
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return False
    release = raw.get("release")
    if not isinstance(release, dict):
        return False
    return release.get("auto_merge") is True


class TestReadAutoMergeFromReleaseBlock:
    def test_absent_key_reads_false(self, tmp_path):
        toml = tmp_path / "group.toml"
        toml.write_text('[release]\nmerge_order = ["api"]\n')
        assert _spike_load_auto_merge(str(toml)) is False

    def test_explicit_false_reads_false(self, tmp_path):
        toml = tmp_path / "group.toml"
        toml.write_text("[release]\nauto_merge = false\n")
        assert _spike_load_auto_merge(str(toml)) is False

    def test_explicit_true_reads_true(self, tmp_path):
        toml = tmp_path / "group.toml"
        toml.write_text("[release]\nauto_merge = true\n")
        assert _spike_load_auto_merge(str(toml)) is True

    def test_no_toml_path_reads_false(self):
        assert _spike_load_auto_merge(None) is False

    def test_coexists_with_merge_order_in_same_block(self, tmp_path):
        """auto_merge and merge_order must be independently readable from the
        same [release] table — proves this isn't an either/or key."""
        from trailhead.vcs.github import _load_merge_order

        toml = tmp_path / "group.toml"
        toml.write_text('[release]\nmerge_order = ["api", "web"]\nauto_merge = true\n')
        assert _spike_load_auto_merge(str(toml)) is True
        assert _load_merge_order(str(toml)) == ["api", "web"]


# ---------------------------------------------------------------------------
# Part B — does the refusal gate structurally block any subprocess call
# (not just the final `gh pr merge`) before it ever reaches _do_merge?
# ---------------------------------------------------------------------------


class _AutoMergeDisabledError(Exception):
    """Stand-in for the named error Slice D would add to
    trailhead/vcs/github.py, alongside MergeOrderRequiredError/MergeConfigError."""


_REMEDIATION_MESSAGE = (
    "refusing to merge — auto_merge is unset/false — "
    "add [release] auto_merge = true to the group TOML to merge automatically."
)


class _SpyRunner:
    """Records every subprocess invocation the merge path would make (git
    config user.email, gh pr view, gh pr merge, git push --delete). Placing
    the auto_merge gate before the loop — the same site as the existing
    merge_order gate at github.py:536 — means a spy runner sees zero calls."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        raise AssertionError(f"no subprocess call expected during refusal, got: {cmd}")


def _spike_merge_gate(toml_path: str | None, runner) -> None:
    """Models where the auto_merge check sits in _merge_prs: after
    merge_order is loaded, before author_email resolution / the merge loop
    (github.py's gate is at line 536, immediately after `_load_merge_order`
    at line 533) — i.e. before ANY runner.run call, not just before the
    final `gh pr merge`."""
    if not _spike_load_auto_merge(toml_path):
        raise _AutoMergeDisabledError(_REMEDIATION_MESSAGE)
    # else: proceed to _resolve_author_email(runner), the merge loop, etc.


class TestRefusalHappensBeforeAnySubprocessCall:
    def test_absent_auto_merge_refuses_with_zero_subprocess_calls(self, tmp_path):
        toml = tmp_path / "group.toml"
        toml.write_text("[release]\n")
        spy = _SpyRunner()
        with pytest.raises(_AutoMergeDisabledError, match="auto_merge"):
            _spike_merge_gate(str(toml), spy)
        assert spy.calls == []

    def test_explicit_false_refuses_with_zero_subprocess_calls(self, tmp_path):
        toml = tmp_path / "group.toml"
        toml.write_text("[release]\nauto_merge = false\n")
        spy = _SpyRunner()
        with pytest.raises(_AutoMergeDisabledError):
            _spike_merge_gate(str(toml), spy)
        assert spy.calls == []

    def test_true_proceeds_past_the_gate(self, tmp_path):
        toml = tmp_path / "group.toml"
        toml.write_text("[release]\nauto_merge = true\n")
        spy = _SpyRunner()
        _spike_merge_gate(str(toml), spy)  # no raise
        assert spy.calls == []  # gate itself makes no calls either way


# ---------------------------------------------------------------------------
# Part C — merge_prs.py's exit-code contract: does a refusal raised from
# provider.pr.merge() actually reach the CLI as a clean nonzero exit today,
# and is that the signal monitor.md already relies on?
# ---------------------------------------------------------------------------


class _FakePRRefusing:
    """provider.pr stub that raises the way the real _merge_prs would once
    Slice D's gate is added — proves the CLI-level plumbing independent of
    the github.py internals."""

    def __init__(self, exc):
        self._exc = exc
        self.calls: list[str] = []

    def merge(self, pr_pairs, manifest_path, *, toml_path=None):
        self.calls.append("merge")
        raise self._exc


class _FakeProviderRefusing:
    def __init__(self, exc):
        self.pr = _FakePRRefusing(exc)


def _make_manifest(tmp_path: Path) -> Path:
    import json

    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema_version": 1, "members": []}))
    return p


class TestMergePrsExitCodeCarriesRefusal:
    def test_reusing_an_already_caught_exception_needs_no_cli_change(self, tmp_path, monkeypatch, capsys):
        """If Slice D raises RuntimeError (already in merge_prs.py's except
        tuple at scripts/merge_prs.py:70-76) for the auto_merge refusal, the
        CLI already exits 2 with the message on stderr and prints nothing to
        stdout — zero changes needed to merge_prs.py itself."""
        provider = _FakeProviderRefusing(RuntimeError(_REMEDIATION_MESSAGE))
        mod = load_script("merge_prs")
        monkeypatch.setattr(mod, "get_provider", lambda *a, **k: provider)
        manifest = _make_manifest(tmp_path)

        rc = mod.main(["--manifest", str(manifest), "--toml", "unused.toml", f"{tmp_path}:1:api"])

        assert rc == 2
        err = capsys.readouterr()
        assert "auto_merge" in err.err
        assert "add [release] auto_merge = true" in err.err
        assert err.out == ""  # no merge JSON printed — nothing was merged
        assert provider.pr.calls == ["merge"]

    def test_a_brand_new_named_error_class_is_NOT_caught_today(self, tmp_path, monkeypatch):
        """The house convention (MergeOrderRequiredError, MergeConfigError —
        one class per condition) suggests Slice D adds AutoMergeDisabledError
        rather than reusing RuntimeError. If it does, merge_prs.py's
        import list (scripts/merge_prs.py:32-38) and except tuple (:70-76)
        MUST be extended — today this exception is unhandled and propagates
        as a raw traceback, not a clean exit 2. This is the concrete diff
        Slice D owes merge_prs.py beyond trailhead/vcs/github.py."""
        provider = _FakeProviderRefusing(_AutoMergeDisabledError(_REMEDIATION_MESSAGE))
        mod = load_script("merge_prs")
        monkeypatch.setattr(mod, "get_provider", lambda *a, **k: provider)
        manifest = _make_manifest(tmp_path)

        with pytest.raises(_AutoMergeDisabledError):
            mod.main(["--manifest", str(manifest), "--toml", "unused.toml", f"{tmp_path}:1:api"])


# ---------------------------------------------------------------------------
# Part D — does monitor.md's prose already document relying on merge_prs.py's
# exit code as the refusal signal (precedent for the auto_merge gate reusing
# the same contract), or would Slice D be introducing a new interpretation
# pattern from scratch?
# ---------------------------------------------------------------------------


class TestMonitorAlreadyDocumentsExitCodeReliance:
    def test_monitor_md_names_the_merge_order_exit_code_pattern(self):
        monitor_md = (
            Path(__file__).resolve().parents[1]
            / "plugins"
            / "portage"
            / "agents"
            / "monitor.md"
        )
        text = monitor_md.read_text(encoding="utf-8")
        assert "merge_order" in text
        assert "honor that exit code" in text
        assert "relies on that exit code, not JSON" in text
