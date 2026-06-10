"""Integrity-verified fetch layer for the trailhead management tool.

This module provides the security spine of ``trailhead install`` / ``update``:
given an ``InstallManifest`` entry, it either verifies a present local checkout
or clones a fresh copy, then verifies SHA integrity and GPG signature before
allowing any wiring to proceed.

Raw-output posture (S-7 / feedback_raw_data_display_transform)
--------------------------------------------------------------
All git subprocess output (stdout + stderr) is captured and treated as
**untrusted**.  A hostile remote can emit arbitrary text in git stderr.
Raw git output is NEVER echoed into user-facing error messages.  Only
named, trailhead-authored messages are surfaced.  Raw output is available
behind a verbose/debug path only (future flag; not surfaced yet).

Arg-list posture (S-3)
----------------------
Every git invocation is ``subprocess.run([...])`` with an explicit arg list.
``shell=True`` is never used.  The fetch source is passed as the last
positional after a ``--`` terminator to prevent git option injection:
    ["git", "clone", "--", <source>, <dest>]
    ["git", "-C", <repo>, "checkout", <sha>]

GPG hard-fail posture (S-1)
---------------------------
``git verify-commit --raw <sha>`` must exit 0 AND the VALIDSIG fingerprint in
the machine-readable output must end with the pinned key fingerprint.  A
nonzero exit — whether from an unsigned commit, a key not in the keyring, or
any other verification failure — is a **hard refusal**.  A commit signed by a
key that is in the keyring but does NOT match the pinned fingerprint is also a
**hard refusal**.  There is no TOFU, no soft warning, no skip.  The failure
message names the expected key fingerprint (74AEB40C93C4250A) and the
remediation step.

A future ``--accept-new-key`` TOFU escape hatch could be wired by routing the
GPG decision through ``verify_gpg`` with an ``accept_new_key`` parameter; all
decision logic lives there.  That hatch is NOT built now — hard-fail is the
only path this slice ships.

Atomicity
---------
Failed verification at ANY stage writes nothing to the final promoted dest.
Fresh clones go into a ``tmp`` staging dir under ``state_dir("trailhead")``;
only on full verification success is the staging dir promoted (renamed) to the
final dest.

Self-referential manifest SHA
------------------------------
The committed ``install_manifest.toml`` pins a SHA that cannot equal the live
repo's post-commit HEAD (circular).  Tests are against SYNTHETIC git repos.
The live manifest is reconciled at dogfood/release time (bumped to the release
SHA when an install is cut).  See docs/install-manifest.md.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from trailhead.manifest import RepoEntry
from trailhead.paths import state_dir


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Raised for any integrity-verification or fetch failure.

    Always cites the repo name + a named, actionable message.
    Never contains raw git stdout/stderr.
    """


# ---------------------------------------------------------------------------
# Internal: git subprocess helpers (all arg-list, never shell=True)
# ---------------------------------------------------------------------------

# Full 40-char fingerprint of the production trailhead signing key.
# The short-id (74AEB40C93C4250A) is used only in user-facing remediation text.
_EXPECTED_KEY_FPR = "3DA19E194A94145E166CC5BC74AEB40C93C4250A"
_EXPECTED_KEY_SHORT = "74AEB40C93C4250A"


def _run_git(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a git command with an explicit arg list; capture all output.

    Never uses shell=True.  env is merged with os.environ when provided
    (allows GNUPGHOME override for hermetic tests).
    """
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=run_env,
    )


def _get_head_sha(repo_path: Path, *, env: dict[str, str] | None = None) -> str | None:
    """Return the current HEAD SHA (40 chars) of repo_path, or None on failure."""
    result = _run_git(["git", "-C", str(repo_path), "rev-parse", "HEAD"], env=env)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Public: verify an already-present local repo
# ---------------------------------------------------------------------------


def verify_present_repo(
    entry: RepoEntry,
    *,
    repo_path: Path,
    env: dict[str, str] | None = None,
) -> bool:
    """Verify a local checkout's HEAD matches the pinned rev.

    Args:
        entry:      The manifest entry describing the expected state.
        repo_path:  Absolute path to the local checkout.
        env:        Optional env overrides (for hermeticity in tests).

    Returns:
        True on success.

    Raises:
        FetchError: HEAD != pinned rev, or git rev-parse failed.
    """
    head_sha = _get_head_sha(repo_path, env=env)
    if head_sha is None:
        raise FetchError(
            f"trailhead: cannot read HEAD SHA for repo '{entry.name}' at {repo_path}"
        )

    if head_sha != entry.rev:
        raise FetchError(
            f"trailhead: version mismatch in '{entry.name}'\n"
            f"  expected: {entry.rev[:12]}\n"
            f"     found: {head_sha[:12]}\n"
            f"The local checkout is at a different version than the install manifest pins.\n"
            f"Run `git -C {repo_path} checkout {entry.rev}` to align it, then retry."
        )

    return True


# ---------------------------------------------------------------------------
# Public: GPG verification (S-1 hard-fail, pinned-key)
# ---------------------------------------------------------------------------


def verify_gpg(
    entry: RepoEntry,
    sha: str,
    *,
    repo_path: Path,
    env: dict[str, str] | None = None,
    expected_key_fpr: str | None = None,
) -> None:
    """Verify the pinned commit is GPG-signed by the expected key (S-1 hard-fail).

    Uses ``git verify-commit --raw <sha>`` and parses the machine-readable GPG
    status output.  Two conditions must both be true:

    1. The command exits 0 (signature is valid and the key is in the keyring).
    2. The ``VALIDSIG <fpr40>`` line's fingerprint ends with the pinned key ID.

    A commit signed by a key that is in the keyring but is NOT the pinned key
    is also refused — this closes the hollow-gate bug where any valid key would
    pass.

    Args:
        entry:            The manifest entry (used for named error messages).
        sha:              The 40-char SHA to verify (must equal entry.rev).
        repo_path:        Absolute path to the repo containing the commit.
        env:              Optional env overrides; GNUPGHOME can be overridden here
                          for hermetic tests.
        expected_key_fpr: The expected 40-char (or 16-char short) fingerprint.
                          Defaults to the production trailhead key
                          (_EXPECTED_KEY_FPR).  Pass a test-controlled ephemeral
                          fingerprint for hermetic testing.

    Raises:
        FetchError: The commit is unsigned, signed by an unimported key, signed
                    by the wrong key, or verification fails for any other reason.
                    Named message includes the expected key fingerprint and
                    remediation.  Raw git/gpg output is NOT included.
    """
    pinned_fpr = expected_key_fpr if expected_key_fpr is not None else _EXPECTED_KEY_FPR
    # The short id used in user-facing messages: last 16 chars of pinned fpr,
    # or the production short-id if using the production key.
    display_id = _EXPECTED_KEY_SHORT if pinned_fpr == _EXPECTED_KEY_FPR else pinned_fpr[-16:]

    result = _run_git(
        ["git", "-C", str(repo_path), "verify-commit", "--raw", sha],
        env=env,
    )
    if result.returncode != 0:
        raise FetchError(
            f"trailhead: commit {sha[:12]} in '{entry.name}' could not be GPG-verified.\n"
            f"Import the signing key ({display_id}) before installing:\n"
            f"  gpg --recv-keys {display_id}\n"
            f"See docs/install-manifest.md for the out-of-band trust anchor."
        )

    # Parse the VALIDSIG line from --raw output (written to stderr by gpg status-fd)
    # Format: [GNUPG:] VALIDSIG <fpr40> <date> <timestamp> ...
    validsig_fpr: str | None = None
    for line in result.stderr.splitlines():
        parts = line.split()
        if "[GNUPG:]" in parts:
            idx = parts.index("[GNUPG:]")
            status_parts = parts[idx + 1:]
        else:
            status_parts = parts
        if status_parts and status_parts[0] == "VALIDSIG" and len(status_parts) >= 2:
            validsig_fpr = status_parts[1]
            break

    if validsig_fpr is None or not validsig_fpr.upper().endswith(pinned_fpr.upper()):
        raise FetchError(
            f"trailhead: commit {sha[:12]} in '{entry.name}' was signed by an unexpected key.\n"
            f"Expected key ending with: {display_id}\n"
            f"Import the correct signing key ({display_id}) before installing:\n"
            f"  gpg --recv-keys {display_id}\n"
            f"See docs/install-manifest.md for the out-of-band trust anchor."
        )


# ---------------------------------------------------------------------------
# Public: fresh-clone case (clone → checkout → verify)
# ---------------------------------------------------------------------------


def clone_and_verify(
    entry: RepoEntry,
    *,
    dest_parent: Path,
    env: dict[str, str] | None = None,
    expected_key_fpr: str | None = None,
) -> Path:
    """Clone a fresh copy of the repo, check out the pinned SHA, and verify integrity.

    Clone → checkout SHA → verify HEAD == rev → verify GPG.
    Uses a staging dir under state_dir("trailhead") for atomicity: the staging
    dir is only promoted to the final dest after full verification passes.

    S-3 arg-list: the source is passed as the last positional after ``--``::

        ["git", "clone", "--", <source>, <staging_dir>]

    The checkout does NOT use ``--`` (which would treat the SHA as a pathspec)::

        ["git", "-C", <staging>, "checkout", <sha>]

    S-7: raw git output is captured but never surfaced in error messages.
    Atomicity: on any failure, the staging dir is cleaned up and the
    final dest is NOT created.

    Args:
        entry:            The manifest entry with source + rev.
        dest_parent:      Directory under which the final repo dir is placed.
        env:              Optional env overrides for hermetic tests.
        expected_key_fpr: Pinned key fingerprint (injected for tests; defaults
                          to the production trailhead key).

    Returns:
        Path to the promoted final dest directory.

    Raises:
        FetchError: On any clone, checkout, SHA-mismatch, or GPG failure.
                    Named, trailhead-authored messages only (S-7).
    """
    # Resolve state_dir for staging (honors TRAILHEAD_STATE_DIR env override)
    state_env: dict[str, str] | None = None
    if env:
        state_key = "TRAILHEAD_STATE_DIR"
        if state_key in env:
            state_env = {state_key: env[state_key]}

    try:
        staging_base = state_dir("trailhead", env=state_env)
    except Exception:
        staging_base = dest_parent

    staging_parent = staging_base / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Ensure mode is 0700 even when the directory already existed
    staging_parent.chmod(0o700)

    staging_dir = None
    try:
        # Clone into a temp dir under staging (S-3: source after --)
        staging_dir = Path(tempfile.mkdtemp(dir=str(staging_parent), prefix=f"{entry.name}-"))

        clone_result = _run_git(
            ["git", "clone", "--", entry.source, str(staging_dir)],
            env=env,
        )
        if clone_result.returncode != 0:
            raise FetchError(
                f"trailhead: cannot reach update source\n"
                f"  source: {entry.source}\n"
                f"Check your connection, or confirm the source with "
                f"`trailhead config registry`.\n"
                f"To use a local copy, set a file:// source."
            )

        # Checkout the pinned SHA (no -- : SHA is a commit, not a pathspec)
        checkout_result = _run_git(
            ["git", "-C", str(staging_dir), "checkout", entry.rev],
            env=env,
        )
        if checkout_result.returncode != 0:
            raise FetchError(
                f"trailhead: cannot checkout pinned revision {entry.rev[:12]} "
                f"in '{entry.name}' — revision not found in cloned repo.\n"
                f"Check that the manifest rev matches a commit in the source."
            )

        # Verify HEAD == pinned rev (integrity gate)
        head_sha = _get_head_sha(staging_dir, env=env)
        if head_sha != entry.rev:
            raise FetchError(
                f"trailhead: version mismatch in '{entry.name}' after clone\n"
                f"  expected: {entry.rev[:12]}\n"
                f"     found: {(head_sha or 'unknown')[:12]}\n"
                f"The cloned repo HEAD does not match the pinned manifest rev.\n"
                f"Run `git -C {staging_dir} checkout {entry.rev}` to align it, then retry."
            )

        # GPG verification (S-1 hard-fail, pinned-key)
        verify_gpg(entry, entry.rev, repo_path=staging_dir, env=env, expected_key_fpr=expected_key_fpr)

        # Verification passed — promote to final dest (atomic via os.replace where possible)
        final_dest = dest_parent / entry.name
        # Defense-in-depth: assert the final dest resolves inside dest_parent
        if not final_dest.resolve().is_relative_to(dest_parent.resolve()):
            raise FetchError(
                f"trailhead: repo name '{entry.name}' would place the checkout "
                f"outside the destination directory — rejected for security."
            )
        if final_dest.exists():
            shutil.rmtree(final_dest)
        shutil.move(str(staging_dir), str(final_dest))
        staging_dir = None  # transferred — don't clean up
        return final_dest

    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(
            f"trailhead: unexpected error during fetch of '{entry.name}': {exc}"
        ) from exc
    finally:
        # Clean up staging dir on any failure (atomicity — no half-fetched repo promoted)
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
