"""Parsing for the `--pr` token the `portage sidecar write` subcommand accepts.

A ``--pr`` value is a ``<repo>:<pr_number>:<url>:<branch>`` token. The url may
itself contain colons (e.g. ``https://…``), so the fields are split off the
first two by position and the branch off the last, leaving the middle as the
url. Kept as a pure function (no ``trailhead`` import) so it is unit-testable
independent of the CLI and the VCS provider.
"""

from __future__ import annotations


def parse_pr_token(token: str) -> dict[str, str]:
    """Parse a ``<repo>:<pr_number>:<url>:<branch>`` token into a PR dict.

    Raises ValueError with a legible message on any malformed token; the CLI
    surfaces that on stderr and exits 2.
    """
    head, _, rest = token.partition(":")
    if not rest:
        raise ValueError(
            f"sidecar write: --pr must be <repo>:<pr_number>:<url>:<branch>, got: {token!r}"
        )
    pr_number_part, _, url_and_branch = rest.partition(":")
    if not url_and_branch or ":" not in url_and_branch:
        raise ValueError(
            f"sidecar write: --pr must be <repo>:<pr_number>:<url>:<branch>, got: {token!r}"
        )
    url, _, branch = url_and_branch.rpartition(":")
    if not url or not branch:
        raise ValueError(
            f"sidecar write: --pr must be <repo>:<pr_number>:<url>:<branch>, got: {token!r}"
        )
    return {"repo": head, "pr_number": pr_number_part, "url": url, "branch": branch}
