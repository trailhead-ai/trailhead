"""Provider-agnostic VCS library for trailhead (repos/pr/ci).

``get_provider(name="github")`` is the registry-backed factory. It defaults to
GitHub and raises a legible error for any unregistered name, naming the
documented extension point (``trailhead/docs/vcs-provider.md``).

Adding a second backend (e.g. GitLab via ``glab``/REST) is a two-step change:
implement a ``Provider`` subclass, then add one ``name -> class`` entry to
``_PROVIDERS`` below — the single source of truth for provider selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trailhead.vcs import runner as _runner_module

if TYPE_CHECKING:
    from trailhead.vcs.interface import Provider


# Single source of truth for provider selection. Keys are the names accepted by
# get_provider(); values are the import path "module:ClassName" of the backend.
_PROVIDERS: dict[str, str] = {
    "github": "trailhead.vcs.github:GitHubProvider",
}

_DEFAULT_PROVIDER = "github"

# The documented extension point named in the unknown-provider error.
_EXTENSION_POINT_DOC = "trailhead/docs/vcs-provider.md"


def get_provider(name: str = _DEFAULT_PROVIDER, *, runner=None) -> "Provider":
    """Return a VCS Provider for ``name`` (default: github).

    Args:
        name:   Registered provider name. Defaults to "github".
        runner: Optional injectable runner (tests pass a stub to avoid network).

    Returns:
        A Provider instance with repos/pr/ci surfaces.

    Raises:
        ValueError: If ``name`` is not a registered provider. The message names
            the registered providers and the documented extension point so the
            fix is obvious.
    """
    target = _PROVIDERS.get(name)
    if target is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"unknown VCS provider {name!r} — registered providers: {known}. "
            f"To add a backend, implement a Provider subclass and register it "
            f"in trailhead/vcs/__init__.py (_PROVIDERS); see the extension point "
            f"documented in {_EXTENSION_POINT_DOC}."
        )

    module_path, _, class_name = target.partition(":")
    import importlib

    module = importlib.import_module(module_path)
    provider_cls = getattr(module, class_name)
    return provider_cls(runner=runner)


runner = _runner_module  # re-export so ``from trailhead.vcs import runner`` works for callers

__all__ = ["get_provider", "runner"]
