"""Tests for the trailhead.vcs provider interface + registry factory.

Contract:
  - get_provider("github") returns a GitHubProvider.
  - get_provider() defaults to github.
  - get_provider("gitlab") (or any unregistered name) raises a legible error
    that names the documented extension point.
  - The injectable runner threads through get_provider(name, runner=...).
  - The Provider interface exposes namespaced surfaces repos/pr/ci and declares
    a deploy surface that is NOT implemented in Slice 1 (NotImplemented).
  - vcs-provider.md exists and names every repos/pr/ci interface method.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from trailhead.vcs import get_provider
from trailhead.vcs.github import GitHubProvider


# ---------------------------------------------------------------------------
# Registry factory
# ---------------------------------------------------------------------------


class TestGetProvider:
    def test_github_returns_github_provider(self) -> None:
        provider = get_provider("github")
        assert isinstance(provider, GitHubProvider)

    def test_default_is_github(self) -> None:
        provider = get_provider()
        assert isinstance(provider, GitHubProvider)

    def test_unknown_name_raises_legible_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            get_provider("gitlab")
        msg = str(exc_info.value)
        assert "gitlab" in msg
        # names the documented extension point
        assert "vcs-provider.md" in msg
        # names the registered provider(s) so the fix is obvious
        assert "github" in msg

    def test_arbitrary_unregistered_name_raises(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            get_provider("bitbucket")
        assert "bitbucket" in str(exc_info.value)

    def test_runner_threads_through(self) -> None:
        """A stub runner passed to get_provider is used by provider calls."""
        calls: list[list[str]] = []

        def stub(cmd, **kwargs):
            import subprocess
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        # Any pr/ci/repos call must route through the injected stub.
        try:
            provider.pr.status("some/path", "1")
        except Exception:
            pass
        assert calls, "the injected runner stub was never called"


# ---------------------------------------------------------------------------
# Interface shape
# ---------------------------------------------------------------------------


class TestProviderShape:
    def test_namespaced_surfaces_present(self) -> None:
        provider = get_provider("github")
        assert hasattr(provider, "repos")
        assert hasattr(provider, "pr")
        assert hasattr(provider, "ci")
        assert hasattr(provider, "deploy")

    def test_repos_methods(self) -> None:
        provider = get_provider("github")
        assert hasattr(provider.repos, "detect")

    def test_pr_methods(self) -> None:
        provider = get_provider("github")
        for m in ("open", "status", "evaluate", "merge"):
            assert hasattr(provider.pr, m), f"pr.{m} missing"

    def test_ci_methods(self) -> None:
        provider = get_provider("github")
        for m in ("checks", "wait"):
            assert hasattr(provider.ci, m), f"ci.{m} missing"

    def test_deploy_surface_unimplemented_in_slice_1(self) -> None:
        """deploy is declared but its methods are not implemented this slice."""
        provider = get_provider("github")
        with pytest.raises(NotImplementedError):
            provider.deploy.status("some/path")


# ---------------------------------------------------------------------------
# Doc test: vcs-provider.md names every repos/pr/ci method
# ---------------------------------------------------------------------------


_DOC_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "vcs-provider.md"
)

# The repos/pr/ci surface — every method here must appear in the doc.
_INTERFACE_METHODS = [
    "repos.detect",
    "pr.open",
    "pr.status",
    "pr.evaluate",
    "pr.merge",
    "ci.checks",
    "ci.wait",
]


class TestVcsProviderDoc:
    def test_doc_exists(self) -> None:
        assert _DOC_PATH.is_file(), f"missing {_DOC_PATH}"

    def test_doc_names_every_interface_method(self) -> None:
        text = _DOC_PATH.read_text(encoding="utf-8")
        missing = [m for m in _INTERFACE_METHODS if m not in text]
        assert not missing, (
            f"vcs-provider.md does not map these interface methods: {missing}"
        )

    def test_doc_documents_gitlab_mapping(self) -> None:
        """The doc proves the seam isn't GitHub-only by naming GitLab equivalents."""
        text = _DOC_PATH.read_text(encoding="utf-8").lower()
        assert "gitlab" in text
        assert "glab" in text

    def test_doc_documents_provider_selection(self) -> None:
        text = _DOC_PATH.read_text(encoding="utf-8")
        assert "get_provider" in text
        assert "github" in text.lower()
