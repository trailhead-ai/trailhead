"""Tests for the trailhead.vcs provider interface + registry factory.

Contract:
  - get_provider("github") returns a GitHubProvider.
  - get_provider() defaults to github.
  - get_provider("gitlab") (or any unregistered name) raises a legible error
    that names the documented extension point.
  - The injectable runner threads through get_provider(name, runner=...).
  - The Provider interface exposes namespaced surfaces repos/pr/ci, and the ABC
    machinery refuses a surface that leaves any of them unimplemented.
"""

from __future__ import annotations

import pytest

from trailhead.vcs import get_provider
from trailhead.vcs.github import GitHubProvider
from trailhead.vcs.interface import CISurface, PRSurface, ReposSurface


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


class TestSurfacesAreEnforcedByTheABC:
    """Every interface method is implemented — enforced at construction, not asserted.

    ``_GitHubRepos`` / ``_GitHubPR`` / ``_GitHubCI`` subclass the ``ReposSurface`` /
    ``PRSurface`` / ``CISurface`` ABCs, so Python refuses to instantiate any of them
    while an ``@abstractmethod`` is unimplemented. ``get_provider("github")`` builds
    all three, which means ``TestGetProvider`` above already proves the whole method
    set is present — and proves it more strongly than a per-method ``hasattr`` sweep,
    which passes on any attribute of any type and needs a hand-typed method list that
    silently rots as the interface grows.

    The test below is the evidence for that claim: it shows the refusal actually
    happens, so the coverage the sweep used to provide is not merely assumed.
    """

    def test_an_incomplete_surface_cannot_be_instantiated(self) -> None:
        class PartialPR(PRSurface):
            """Implements nothing — stands in for a backend that missed a method."""

        with pytest.raises(TypeError) as exc_info:
            PartialPR()
        assert "abstract" in str(exc_info.value).lower()

    def test_the_real_provider_builds_all_three_surfaces(self) -> None:
        provider = get_provider("github")
        assert isinstance(provider.repos, ReposSurface)
        assert isinstance(provider.pr, PRSurface)
        assert isinstance(provider.ci, CISurface)
