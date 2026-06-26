"""Provider-agnostic VCS interface (the seam portage/landing build on).

A ``Provider`` exposes four namespaced surfaces:

- ``repos`` — camp-manifest-driven active-repo detection.
- ``pr``    — open / status / evaluate / merge a pull/merge request.
- ``ci``    — CI checks read + poll-to-actionable.
- ``deploy``— workflow-run + deployment status + log interrogation.

All four surfaces are declared here so the interface shape is stable and a
second backend maps onto the same method set; ``GitHubProvider`` implements
them against GitHub.

The interface is GitHub-agnostic by construction: a second backend (GitLab via
``glab``/REST) maps onto the same method set. See ``trailhead/docs/vcs-provider.md``
for the per-method GitLab mapping and the documented extension point.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence


class ReposSurface(ABC):
    """Active-repo detection from the camp central manifest."""

    @abstractmethod
    def detect(self, manifest_path: str) -> list[dict]:
        """Return active repos from camp's manifest.json.

        Consumes the manifest as the membership source of truth — does not
        own or reimplement membership. Returns one dict per member with active
        work: ``{repo, path, branch, ahead, dirty}``.
        """


class PRSurface(ABC):
    """Open, inspect, evaluate, and merge pull/merge requests."""

    @abstractmethod
    def open(self, sidecar_path: Path | str, prs: list[dict[str, str]]) -> None:
        """Record opened/stacked PRs to the prs.json sidecar (atomic, 0o600)."""

    @abstractmethod
    def read_sidecar(self, sidecar_path: Path | str) -> dict[str, Any]:
        """Read the prs.json sidecar back (the read half of open()'s write).

        Symmetric seam: ``open()`` records, ``read_sidecar()`` reads. Promoted
        onto the ABC so portage's monitor reads the sidecar through the typed
        surface, keeping the read/write halves symmetric.
        """

    @abstractmethod
    def status(
        self,
        repo_path: str,
        pr_number: str,
        *,
        since: str | None = None,
        review_bot_login: str | None = None,
    ) -> dict[str, Any]:
        """Fetch live PR status (mergeability/draft/failing checks)."""

    @abstractmethod
    def evaluate(
        self,
        status: dict[str, Any],
        *,
        review_bot_login: str | None = None,
        fail_count: int = 0,
    ) -> dict[str, Any]:
        """Classify a status dict into a recommended action."""

    @abstractmethod
    def merge(
        self,
        pr_pairs: Sequence[Any],
        manifest_path: str,
        *,
        toml_path: str | None = None,
    ) -> dict[str, Any]:
        """Merge PRs in dependency order with the safety gate."""


class CISurface(ABC):
    """CI checks read + poll-to-actionable."""

    @abstractmethod
    def checks(
        self,
        repo_path: str,
        pr_number: str,
        *,
        since: str | None = None,
        review_bot_login: str | None = None,
    ) -> dict[str, Any]:
        """Read CI checks for a PR (failing checks + failure annotations)."""

    @abstractmethod
    def wait(
        self,
        pr_pairs: Sequence[tuple[str, str]],
        *,
        since: str | None = None,
        timeout: int = 1800,
        interval: int = 30,
        review_bot_login: str | None = None,
    ) -> dict[str, Any]:
        """Poll until at least one PR reaches an actionable state, or timeout."""


class DeploySurface(ABC):
    """Workflow-run + deployment status + log interrogation.

    Implemented by ``GitHubProvider``; the interface is provider-agnostic so
    a second backend (GitLab via ``glab``/REST) maps onto the same method set.
    """

    @abstractmethod
    def workflow_runs(self, repo_path: str, **kwargs: Any) -> list[dict]:
        """List GitHub-Actions workflow runs."""

    @abstractmethod
    def status(self, repo_path: str, **kwargs: Any) -> list[dict]:
        """List deployment statuses."""

    @abstractmethod
    def logs(self, repo_path: str, *, job_id: str, **kwargs: Any) -> list[dict]:
        """Fetch failure annotations for a check run — the doctor signal."""


class Provider(ABC):
    """Provider-agnostic VCS backend with four namespaced surfaces."""

    repos: ReposSurface
    pr: PRSurface
    ci: CISurface
    deploy: DeploySurface
