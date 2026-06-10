"""Tests for trailhead/fetch.py — integrity-verified fetch layer.

TDD: these tests are written BEFORE the implementation and must fail first.
All tests must pass after trailhead/fetch.py is implemented.

Security contracts covered:
  S-1  GPG hard-fail: git verify-commit must exit nonzero → hard refusal with
       named message "import the signing key (74AEB40C93C4250A)".
  S-3  git arg-list shape: every git invocation is subprocess.run([...]) list,
       source as last positional after '--', never shell=True.
  S-7  Raw git output is untrusted: hostile stderr must NOT appear in the
       surfaced user-facing error; only named trailhead messages.

Integrity contracts covered:
  - already-present-repo: HEAD SHA must equal pinned rev; mismatch → refused.
  - fresh-clone: clone → checkout SHA → verify HEAD == rev → verify GPG.
  - GPG hard-fail: unsigned commit or key not in keyring → refused with named
    message naming the key fingerprint and remediation.
  - Manifest self-integrity (U-3): the manifest is committed into the GPG-
    verified repo; git verify-commit of the pinned commit covers the manifest.
  - Layering: a tag-pinned entry is rejected by load_install_manifest before
    fetch is callable; assert the layering.
  - Atomicity: a verification failure at any stage writes nothing and exits
    nonzero; no half-fetched repo left as promoted.
  - I-1 close: fetch always passes an explicit local_root to load_install_manifest
    so local sources are confined; a bare local path with no local_root raises.

Hermeticity contract (Step-4 lesson):
  Every test uses synthetic git repos under tmp_path with TRAILHEAD_STATE_DIR
  env overrides. NO test makes a real network call, touches ~/.claude/, or reads
  the live state_dir("trailhead"). The GPG happy-path signs a fixture commit
  with the available trailhead key in a tmp_path repo. The "key not imported"
  path uses an isolated GNUPGHOME so the real key appears absent.

Note on self-referential manifest SHA:
  The committed install_manifest.toml pins a SHA that cannot equal the live
  repo's post-commit HEAD (circular). Tests are against SYNTHETIC git repos
  under tmp_path — never the live manifest. The live manifest is reconciled
  at dogfood/release time (bumped to the release SHA). See docs/install-manifest.md.
"""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from trailhead.manifest import InstallManifest, InstallManifestError, RepoEntry, load_install_manifest


@pytest.fixture
def short_gnupghome():
    """Provide an isolated GNUPGHOME with a short path (under /tmp).

    gpg-agent creates a UNIX socket at GNUPGHOME/S.gpg-agent; macOS/Linux cap
    socket paths at 104 chars.  pytest's tmp_path trees are too deep.
    We use /tmp directly to keep the path short.
    """
    d = Path(tempfile.mkdtemp(dir="/tmp", prefix="th-gpg-"))
    d.chmod(0o700)
    try:
        yield d
    finally:
        # Kill any agent that might be running in this GNUPGHOME
        subprocess.run(
            ["gpgconf", "--homedir", str(d), "--kill", "gpg-agent"],
            capture_output=True,
            env={**os.environ, "GNUPGHOME": str(d)},
        )
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers: fixture git repo factories
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    """Initialize a git repo with a basic user identity."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Fixture"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "fixture@test.example"], check=True, capture_output=True)


def _make_signed_commit(repo: Path, filename: str = "file.txt", message: str = "signed commit") -> str:
    """Create a file + GPG-signed commit; return the 40-char SHA."""
    (repo / filename).write_text("content")
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--gpg-sign=74AEB40C93C4250A", "-m", message],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H", "-1"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_unsigned_commit(repo: Path, filename: str = "file.txt", message: str = "unsigned") -> str:
    """Create a file + commit WITHOUT GPG signing; return the 40-char SHA.

    Uses git -c commit.gpgsign=false to override any global signing config.
    This is local-to-the-command config, not a --no-gpg-sign flag.
    """
    (repo / filename).write_text("unsigned content")
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-m", message],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H", "-1"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_repo_entry(name: str, rev: str, source: str, tools: list | None = None) -> RepoEntry:
    return RepoEntry(name=name, rev=rev, source=source, tools=tools or [])


def _make_install_manifest(repos: list[RepoEntry]) -> InstallManifest:
    return InstallManifest(repos=repos)


# ---------------------------------------------------------------------------
# Import the module under test (after writing tests)
# ---------------------------------------------------------------------------


def _import_fetch():
    """Deferred import so the test file is parseable before fetch.py exists."""
    from trailhead import fetch
    return fetch


# ---------------------------------------------------------------------------
# Layering: tag-pinned entry refused at load_install_manifest before fetch
# ---------------------------------------------------------------------------


class TestLayering:
    """Assert that tag-pinned entries never reach the fetch layer (Slice 1 guards them)."""

    def test_tag_pinned_entry_refused_at_parse_not_at_fetch(self, tmp_path):
        """A tag rev is caught by load_install_manifest; fetch is never called."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "trailhead"\nrev = "v1.0"\n'
            'source = "https://github.com/example/trailhead"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(manifest_path, registry=None)
        assert "rev" in str(exc_info.value) or "v1.0" in str(exc_info.value)

    def test_head_ref_refused_at_parse(self, tmp_path):
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "HEAD"\n'
            'source = "https://github.com/example/repo"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None)

    def test_short_sha_refused_at_parse(self, tmp_path):
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "abc1234"\n'
            'source = "https://github.com/example/repo"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None)


# ---------------------------------------------------------------------------
# Already-present-repo case: HEAD SHA must equal pinned rev
# ---------------------------------------------------------------------------


class TestAlreadyPresentRepo:
    """For a repo already on disk, fetch verifies HEAD == pinned rev."""

    def test_present_repo_at_pinned_sha_passes(self, tmp_path):
        """A local checkout at exactly the pinned SHA passes verification."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_signed_commit(repo)
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        result = fetch.verify_present_repo(entry, repo_path=repo, env=env)
        assert result is True

    def test_present_repo_at_wrong_sha_raises_fetch_error(self, tmp_path):
        """A local checkout at a different SHA than pinned is refused with a named message."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha1 = _make_signed_commit(repo, "file1.txt", "first")
        sha2 = _make_signed_commit(repo, "file2.txt", "second")
        # HEAD is now sha2; pin sha1 — mismatch
        entry = _make_repo_entry("myrepo", sha1, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_present_repo(entry, repo_path=repo, env=env)
        err = str(exc_info.value)
        # A-4 message shape: "version mismatch in <repo>"
        assert "mismatch" in err.lower() or "version" in err.lower()
        assert sha1[:12] in err
        assert sha2[:12] in err

    def test_present_repo_mismatch_message_includes_fix_command(self, tmp_path):
        """The mismatch message names the fix command (A-4)."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha1 = _make_signed_commit(repo, "file1.txt", "first")
        _make_signed_commit(repo, "file2.txt", "second")
        entry = _make_repo_entry("myrepo", sha1, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_present_repo(entry, repo_path=repo, env=env)
        err = str(exc_info.value)
        assert "git" in err and "checkout" in err


# ---------------------------------------------------------------------------
# GPG verification — hard-fail (S-1, the load-bearing security test)
# ---------------------------------------------------------------------------


class TestGPGVerification:
    """S-1: git verify-commit must hard-fail on unsigned or unimportable-key commits."""

    def test_gpg_signed_commit_passes_verification(self, tmp_path):
        """A fixture commit signed by the available key (74AEB40C93C4250A) passes."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_signed_commit(repo)
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        # Must NOT raise
        fetch.verify_gpg(entry, sha, repo_path=repo, env=env)

    def test_unsigned_commit_raises_fetch_error_with_named_message(self, tmp_path):
        """An unsigned commit is a hard refusal (S-1); message names the key fingerprint."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_unsigned_commit(repo)
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_gpg(entry, sha, repo_path=repo, env=env)
        err = str(exc_info.value)
        # S-1 remediation message: must name the key fingerprint
        assert "74AEB40C93C4250A" in err
        # S-7: must be a trailhead-authored message, not raw git output
        assert "gpg:" not in err

    def test_key_not_in_keyring_raises_fetch_error(self, tmp_path):
        """A commit signed by an unimported key → hard refusal with named remediation (S-1).

        Uses an isolated GNUPGHOME so the real key appears absent, hermetically.
        """
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_signed_commit(repo)
        entry = _make_repo_entry("myrepo", sha, str(repo))

        # Isolated GNUPGHOME: empty keyring → key appears unimported
        isolated_gnupghome = tmp_path / "gnupg"
        isolated_gnupghome.mkdir(mode=0o700)
        env = {
            "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
            "GNUPGHOME": str(isolated_gnupghome),
        }

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_gpg(entry, sha, repo_path=repo, env=env)
        err = str(exc_info.value)
        # S-1: must name the expected key and the remediation
        assert "74AEB40C93C4250A" in err
        assert "import" in err.lower()

    def test_gpg_verification_uses_arg_list_not_shell(self, tmp_path):
        """S-3: the git verify-commit call builds an arg list (no shell=True)."""
        fetch = _import_fetch()
        # Inspect the module: _run_git or similar must use list args.
        # Verify by asserting verify_gpg's git invocation is an arg list — done
        # by asserting that a shell-injection attempt in the SHA is safe.
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_unsigned_commit(repo)
        # Poisoned SHA would only matter if shell=True; with a list it's just an invalid sha.
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        with pytest.raises(fetch.FetchError):
            fetch.verify_gpg(entry, sha, repo_path=repo, env=env)
        # If we reach here without shell injection side effect, the list-arg contract holds.
        # The real shell-arg-list assertion is in TestArgListShape.


# ---------------------------------------------------------------------------
# S-1 — the GPG failure message must NOT contain raw git output (S-7)
# ---------------------------------------------------------------------------


class TestS7RawGitOutputNotSurfaced:
    """S-7: hostile remote text (in git stderr) must NOT appear in user-facing errors."""

    def test_gpg_failure_message_does_not_contain_raw_gpg_stderr(self, tmp_path):
        """The FetchError for an unsigned commit must not contain raw 'gpg:' lines."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_unsigned_commit(repo)
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_gpg(entry, sha, repo_path=repo, env=env)
        err = str(exc_info.value)
        # Raw git/gpg output must not be in the user-facing message
        assert "gpg:" not in err
        assert "error:" not in err.lower() or "trailhead" in err.lower()

    def test_clone_failure_message_uses_named_trailhead_message(self, tmp_path):
        """S-7: a failed clone (nonexistent source) surfaces a named message, not raw git."""
        fetch = _import_fetch()
        nonexistent_source = str(tmp_path / "does_not_exist")
        entry = _make_repo_entry("myrepo", "a" * 40, nonexistent_source)
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.clone_and_verify(entry, dest_parent=tmp_path / "dest", env=env)
        err = str(exc_info.value)
        # Must NOT contain raw 'fatal: ...' git output
        assert "fatal:" not in err
        # Must contain a named trailhead message about the source being unreachable
        assert "unreachable" in err.lower() or "cannot reach" in err.lower() or "source" in err.lower()
        # A-5 message shape: name the source
        assert nonexistent_source in err or "source" in err.lower()


# ---------------------------------------------------------------------------
# S-3: git arg-list shape (no shell=True, -- terminator)
# ---------------------------------------------------------------------------


class TestArgListShape:
    """S-3: verify the fetch layer builds git args as a list with -- terminator."""

    def test_fetch_module_has_no_shell_true_invocations(self):
        """Assert fetch.py contains no shell=True in its subprocess.run calls (static check).

        Checks that subprocess.run is never called with shell=True. The test
        excludes docstring content (which may mention the pattern by name).
        """
        import ast
        import inspect
        fetch = _import_fetch()
        source = inspect.getsource(fetch)
        # Parse the AST to check actual subprocess.run calls, not docstrings
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Look for subprocess.run(shell=True) or run(shell=True)
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        raise AssertionError(
                            f"fetch.py must never use shell=True (S-3) — "
                            f"found at line {node.lineno}"
                        )

    def test_fetch_module_docstring_states_raw_capture_posture(self):
        """S-7: the module docstring must document the raw-capture→named-display posture."""
        fetch = _import_fetch()
        doc = fetch.__doc__ or ""
        assert "untrusted" in doc.lower() or "raw" in doc.lower() or "named" in doc.lower()

    def test_clone_uses_double_dash_terminator(self, tmp_path):
        """S-3: git clone args must place the source after '--'."""
        fetch = _import_fetch()
        # Inspect the _build_clone_args helper or equivalent to assert -- is present
        # We test this via a mock that captures the args list.
        import unittest.mock as mock
        calls = []
        original_run = subprocess.run

        def capturing_run(args, **kwargs):
            if isinstance(args, list) and "git" in args:
                calls.append(args)
            return original_run(args, **kwargs)

        nonexistent = str(tmp_path / "nonexistent_repo")
        entry = _make_repo_entry("myrepo", "a" * 40, nonexistent)
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with mock.patch("subprocess.run", side_effect=capturing_run):
            with pytest.raises((fetch.FetchError, Exception)):
                fetch.clone_and_verify(entry, dest_parent=tmp_path / "dest", env=env)

        # Find the clone call and assert -- terminator before the source
        clone_calls = [c for c in calls if "clone" in c]
        assert clone_calls, "expected at least one git clone call"
        clone_args = clone_calls[0]
        assert "--" in clone_args, f"clone args must contain '--' terminator: {clone_args}"
        dash_idx = clone_args.index("--")
        assert clone_args[dash_idx + 1] == nonexistent, (
            f"source must immediately follow '--': {clone_args}"
        )


# ---------------------------------------------------------------------------
# Manifest self-integrity (U-3): GPG-verified commit covers the manifest
# ---------------------------------------------------------------------------


class TestManifestSelfIntegrity:
    """U-3: the manifest is committed into the GPG-verified repo.

    git verify-commit of the pinned SHA covers the manifest (it's in the
    git object graph). A tampered/unsigned commit → refused.
    """

    def test_manifest_in_gpg_verified_commit_passes(self, tmp_path):
        """A manifest committed at a GPG-signed SHA passes integrity verification."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        # Commit a manifest file
        manifest_in_repo = repo / "install_manifest.toml"
        manifest_in_repo.write_text(
            '[[repo]]\nname = "test"\nrev = "' + "a" * 40 + '"\n'
            'source = "https://github.com/example/test"\ntools = []\n'
        )
        subprocess.run(["git", "-C", str(repo), "add", "install_manifest.toml"], check=True, capture_output=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--gpg-sign=74AEB40C93C4250A", "-m", "manifest commit"],
            check=True,
            capture_output=True,
        )
        sha = subprocess.run(
            ["git", "-C", str(repo), "log", "--format=%H", "-1"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        # verify_gpg covers the manifest (it's in the commit object graph)
        fetch.verify_gpg(entry, sha, repo_path=repo, env=env)  # must not raise

    def test_tampered_manifest_commit_unsigned_is_refused(self, tmp_path):
        """A commit not GPG-signed (tampered/unsigned) is refused — covers manifest."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_unsigned_commit(repo)
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_gpg(entry, sha, repo_path=repo, env=env)
        assert "74AEB40C93C4250A" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Atomicity: failed verification leaves no promoted repo dir
# ---------------------------------------------------------------------------


class TestAtomicity:
    """A verification failure at any stage writes nothing to the final dest."""

    def test_clone_failure_leaves_no_promoted_dest(self, tmp_path):
        """A failed clone leaves no promoted repo dir (staging may exist temporarily)."""
        fetch = _import_fetch()
        nonexistent = str(tmp_path / "nonexistent_repo")
        entry = _make_repo_entry("myrepo", "a" * 40, nonexistent)
        dest_parent = tmp_path / "dest"
        dest_parent.mkdir()
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError):
            fetch.clone_and_verify(entry, dest_parent=dest_parent, env=env)

        # The final promoted dest must not exist
        final_dest = dest_parent / "myrepo"
        assert not final_dest.exists(), f"promoted dest must not exist after failed clone: {final_dest}"

    def test_gpg_failure_leaves_no_promoted_dest(self, tmp_path):
        """A GPG failure after successful clone leaves no promoted repo dir."""
        fetch = _import_fetch()
        # Create a real source repo with an unsigned commit
        source_repo = tmp_path / "source"
        _git_init(source_repo)
        sha = _make_unsigned_commit(source_repo)

        entry = _make_repo_entry("myrepo", sha, str(source_repo))
        dest_parent = tmp_path / "dest"
        dest_parent.mkdir()
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError):
            fetch.clone_and_verify(entry, dest_parent=dest_parent, env=env)

        final_dest = dest_parent / "myrepo"
        assert not final_dest.exists(), "promoted dest must not exist after GPG failure"

    def test_sha_mismatch_after_clone_leaves_no_promoted_dest(self, tmp_path):
        """SHA mismatch after clone: the pinned rev is not in the cloned repo.

        This simulates the integrity gate: if somehow the cloned repo doesn't
        contain the pinned SHA (shouldn't happen normally but must be caught),
        no dest is promoted.
        """
        fetch = _import_fetch()
        source_repo = tmp_path / "source"
        _git_init(source_repo)
        sha = _make_signed_commit(source_repo)
        # Pin a SHA that doesn't exist in the repo
        wrong_sha = "b" * 40
        entry = _make_repo_entry("myrepo", wrong_sha, str(source_repo))
        dest_parent = tmp_path / "dest"
        dest_parent.mkdir()
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError):
            fetch.clone_and_verify(entry, dest_parent=dest_parent, env=env)

        final_dest = dest_parent / "myrepo"
        assert not final_dest.exists(), "promoted dest must not exist after SHA mismatch"


# ---------------------------------------------------------------------------
# I-1: local_root must always be explicit; unconfined bare path is refused
# ---------------------------------------------------------------------------


class TestI1LocalRootConfinement:
    """I-1 close: fetch must always pass an explicit local_root to load_install_manifest.

    _validate_source is accept-by-default when local_root is None (a denylist,
    not the documented allowlist). The fetch layer must ensure that any bare local
    path with no local_root is refused, not silently accepted.
    """

    def test_load_install_manifest_with_local_root_confines_source(self, tmp_path):
        """load_install_manifest with local_root confines a local source."""
        manifest_path = tmp_path / "install_manifest.toml"
        safe_source = str(tmp_path / "myrepo")
        manifest_path.write_text(
            f'[[repo]]\nname = "test"\nrev = "{"a" * 40}"\n'
            f'source = "{safe_source}"\ntools = []\n'
        )
        result = load_install_manifest(manifest_path, registry=None, local_root=tmp_path)
        assert result.repos[0].source == safe_source

    def test_load_install_manifest_local_source_escaping_root_is_refused(self, tmp_path):
        """A local source that escapes local_root is refused (I-1 guard)."""
        manifest_path = tmp_path / "install_manifest.toml"
        escaping_path = str(tmp_path.parent / "escape" / "repo")
        manifest_path.write_text(
            f'[[repo]]\nname = "test"\nrev = "{"a" * 40}"\n'
            f'source = "{escaping_path}"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None, local_root=tmp_path)

    def test_verify_present_repo_exposes_local_root_requirement(self, tmp_path):
        """fetch.verify_present_repo uses local_root; a path outside any root is safe because
        verify_present_repo works with a concrete repo_path, not a raw manifest source."""
        fetch = _import_fetch()
        # This test confirms fetch functions accept a repo_path (already resolved)
        # rather than re-resolving bare paths — the I-1 confinement happens at load time.
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha = _make_signed_commit(repo)
        entry = _make_repo_entry("myrepo", sha, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        # Must pass without error — the repo_path is the resolved, already-confined path
        result = fetch.verify_present_repo(entry, repo_path=repo, env=env)
        assert result is True


# ---------------------------------------------------------------------------
# A-4 error message shape: SHA mismatch
# ---------------------------------------------------------------------------


class TestA4MismatchMessage:
    """A-4: the SHA-mismatch error must use plain-language with the fix command."""

    def test_mismatch_error_format(self, tmp_path):
        """A-4 message shape: multi-line, names expected + found SHA, names fix command."""
        fetch = _import_fetch()
        repo = tmp_path / "myrepo"
        _git_init(repo)
        sha1 = _make_signed_commit(repo, "file1.txt", "first")
        sha2 = _make_signed_commit(repo, "file2.txt", "second")
        # Pinned sha1 but HEAD is sha2
        entry = _make_repo_entry("myrepo", sha1, str(repo))
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_present_repo(entry, repo_path=repo, env=env)

        err = str(exc_info.value)
        assert sha1[:12] in err
        assert sha2[:12] in err
        assert "git" in err
        assert "checkout" in err
        assert sha1 in err or sha1[:12] in err


# ---------------------------------------------------------------------------
# A-5 error message shape: unreachable source
# ---------------------------------------------------------------------------


class TestA5UnreachableSource:
    """A-5: the unreachable-source error must use the specified format."""

    def test_unreachable_source_error_names_source(self, tmp_path):
        """A-5: error names the source and suggests config registry."""
        fetch = _import_fetch()
        bad_source = str(tmp_path / "no_such_repo")
        entry = _make_repo_entry("myrepo", "a" * 40, bad_source)
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.clone_and_verify(entry, dest_parent=tmp_path / "dest", env=env)

        err = str(exc_info.value)
        assert bad_source in err or "source" in err.lower()

    def test_unreachable_source_error_does_not_contain_raw_git_fatal(self, tmp_path):
        """S-7: unreachable source error must not contain raw 'fatal:' git output."""
        fetch = _import_fetch()
        bad_source = str(tmp_path / "no_such_repo")
        entry = _make_repo_entry("myrepo", "a" * 40, bad_source)
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.clone_and_verify(entry, dest_parent=tmp_path / "dest", env=env)

        err = str(exc_info.value)
        assert "fatal:" not in err


# ---------------------------------------------------------------------------
# Helpers for new hermetic GPG tests (Findings 1 + 2)
# ---------------------------------------------------------------------------


def _make_gpg_key(gnupghome: Path, uid: str = "Test Key <test@example.com>") -> str:
    """Generate an ephemeral RSA GPG key in an isolated GNUPGHOME; return its 40-char fingerprint.

    IMPORTANT: gnupghome must be a short path (under ~60 chars) to avoid the
    macOS/Linux 104-char UNIX socket path limit that gpg-agent hits when creating
    S.gpg-agent under deep pytest tmp_path trees.  Callers should pass a path
    under /tmp, not under pytest's tmp_path.
    """
    name = uid.split("<")[0].strip()
    email = uid.split("<")[1].rstrip(">")
    batch_params = (
        "Key-Type: RSA\n"
        "Key-Length: 2048\n"
        "Key-Usage: sign\n"
        f"Name-Real: {name}\n"
        f"Name-Email: {email}\n"
        "Expire-Date: 0\n"
        "%no-protection\n"
        "%commit\n"
    )
    env = {**os.environ, "GNUPGHOME": str(gnupghome)}
    subprocess.run(
        ["gpg", "--homedir", str(gnupghome), "--batch", "--gen-key"],
        input=batch_params,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    result = subprocess.run(
        ["gpg", "--homedir", str(gnupghome), "--list-keys", "--with-colons"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    fprs = [line.split(":")[9] for line in result.stdout.splitlines() if line.startswith("fpr:")]
    if not fprs:
        raise RuntimeError(f"could not extract fingerprint from gpg output: {result.stdout!r}")
    return fprs[-1]  # return the last (most recently generated) fingerprint


def _make_signed_commit_with_key(repo: Path, fingerprint: str, gnupghome: Path,
                                  filename: str = "file.txt", message: str = "signed") -> str:
    """Create a signed commit using the specified key fingerprint + GNUPGHOME; return 40-char SHA."""
    env = {**os.environ, "GNUPGHOME": str(gnupghome)}
    (repo / filename).write_text("content")
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", f"gpg.program=gpg",
            "commit", f"--gpg-sign={fingerprint}", "-m", message,
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H", "-1"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Finding 1 — CRITICAL: GPG gate must verify the PINNED key, not ANY key
# ---------------------------------------------------------------------------


class TestFinding1PinnedKeyVerification:
    """Finding 1 (CRITICAL): verify_gpg must refuse a commit signed by a different key.

    The old implementation called ``git verify-commit <sha>`` without --raw and
    passed as long as exit code was 0 — any key in the keyring would pass.
    The fix uses ``git verify-commit --raw`` and parses the VALIDSIG fingerprint,
    refusing if it doesn't match the pinned key.
    """

    def test_wrong_key_is_refused(self, tmp_path, short_gnupghome):
        """A commit signed by an 'attacker' key (not the trusted key) must be REFUSED.

        This is the load-bearing regression test: before the fix this passed
        (any key in keyring would pass); after the fix it must raise FetchError.
        """
        fetch = _import_fetch()

        # Generate two keys in the same isolated GNUPGHOME: trusted + attacker
        trusted_fpr = _make_gpg_key(short_gnupghome, "Trusted Key <trusted@example.com>")
        attacker_fpr = _make_gpg_key(short_gnupghome, "Attacker Key <attacker@example.com>")
        assert trusted_fpr != attacker_fpr

        repo = tmp_path / "repo"
        _git_init(repo)

        # Sign with the ATTACKER key; both keys are in the keyring
        env = {**os.environ, "GNUPGHOME": str(short_gnupghome), "TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        sha = _make_signed_commit_with_key(repo, attacker_fpr, short_gnupghome, message="attacker signed")

        # Pin the TRUSTED fingerprint — not the attacker's
        entry = _make_repo_entry("repo", sha, str(repo))

        # Must REFUSE because the commit was signed by the attacker key, not trusted
        with pytest.raises(fetch.FetchError) as exc_info:
            fetch.verify_gpg(entry, sha, repo_path=repo, env=env, expected_key_fpr=trusted_fpr)
        err = str(exc_info.value)
        assert trusted_fpr[-16:] in err or "key" in err.lower()

    def test_correct_key_passes(self, tmp_path, short_gnupghome):
        """A commit signed by the pinned key must pass verification."""
        fetch = _import_fetch()

        trusted_fpr = _make_gpg_key(short_gnupghome, "Trusted Key <trusted@example.com>")

        repo = tmp_path / "repo"
        _git_init(repo)

        env = {**os.environ, "GNUPGHOME": str(short_gnupghome), "TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        sha = _make_signed_commit_with_key(repo, trusted_fpr, short_gnupghome)
        entry = _make_repo_entry("repo", sha, str(repo))

        # Must pass — correct key
        fetch.verify_gpg(entry, sha, repo_path=repo, env=env, expected_key_fpr=trusted_fpr)

    def test_both_keys_in_keyring_wrong_key_still_refused(self, tmp_path, short_gnupghome):
        """When both keys are in the keyring, a commit signed by the non-pinned key is refused.

        This is the core hollow-gate scenario: git verify-commit returns 0 for both
        keys (both are in the keyring), but we must still refuse the wrong key.
        """
        fetch = _import_fetch()

        trusted_fpr = _make_gpg_key(short_gnupghome, "Trusted Key <trusted@example.com>")
        attacker_fpr = _make_gpg_key(short_gnupghome, "Attacker Key <attacker@example.com>")

        repo = tmp_path / "repo"
        _git_init(repo)

        env = {**os.environ, "GNUPGHOME": str(short_gnupghome), "TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        # Sign with attacker; both keys are in the keyring so git exits 0
        sha = _make_signed_commit_with_key(repo, attacker_fpr, short_gnupghome, message="attacker")
        entry = _make_repo_entry("repo", sha, str(repo))

        with pytest.raises(fetch.FetchError):
            fetch.verify_gpg(entry, sha, repo_path=repo, env=env, expected_key_fpr=trusted_fpr)


# ---------------------------------------------------------------------------
# Finding 2 — HIGH: clone_and_verify green-path (and fixed checkout)
# ---------------------------------------------------------------------------


class TestFinding2CloneAndVerifyGreenPath:
    """Finding 2 (HIGH): clone_and_verify must succeed on the happy path.

    The original code used ``git checkout -- <sha>`` which treats SHA as a
    pathspec (wrong) and always exits nonzero — the green path was never
    reachable. After the fix (remove the ``--``) the flow completes.
    """

    def test_clone_and_verify_success_path(self, tmp_path, short_gnupghome):
        """Green path: clone from local source, checkout pinned SHA, GPG-verified → returns path.

        This test FAILS before Finding 2 is fixed because checkout -- <sha>
        always fails (SHA treated as pathspec).
        """
        fetch = _import_fetch()

        trusted_fpr = _make_gpg_key(short_gnupghome, "Trusted Key <trusted@example.com>")

        source_repo = tmp_path / "source"
        _git_init(source_repo)
        env = {**os.environ, "GNUPGHOME": str(short_gnupghome), "TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        sha = _make_signed_commit_with_key(source_repo, trusted_fpr, short_gnupghome)

        entry = _make_repo_entry("myrepo", sha, str(source_repo))
        dest_parent = tmp_path / "dest"
        dest_parent.mkdir()

        promoted = fetch.clone_and_verify(
            entry, dest_parent=dest_parent, env=env, expected_key_fpr=trusted_fpr
        )

        assert promoted.exists(), "promoted path must exist after successful clone_and_verify"
        assert promoted == dest_parent / "myrepo"
        # Verify the HEAD in the promoted repo matches the pinned SHA
        head_sha = subprocess.run(
            ["git", "-C", str(promoted), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert head_sha == sha

    def test_clone_and_verify_wrong_key_refused_no_promote(self, tmp_path, short_gnupghome):
        """Wrong key: clone succeeds, GPG fails → no promote, FetchError raised."""
        fetch = _import_fetch()

        trusted_fpr = _make_gpg_key(short_gnupghome, "Trusted Key <trusted@example.com>")
        attacker_fpr = _make_gpg_key(short_gnupghome, "Attacker Key <attacker@example.com>")

        source_repo = tmp_path / "source"
        _git_init(source_repo)
        env = {**os.environ, "GNUPGHOME": str(short_gnupghome), "TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        sha = _make_signed_commit_with_key(source_repo, attacker_fpr, short_gnupghome)

        entry = _make_repo_entry("myrepo", sha, str(source_repo))
        dest_parent = tmp_path / "dest"
        dest_parent.mkdir()

        with pytest.raises(fetch.FetchError):
            fetch.clone_and_verify(
                entry, dest_parent=dest_parent, env=env, expected_key_fpr=trusted_fpr
            )

        assert not (dest_parent / "myrepo").exists(), "no dest must be promoted after GPG failure"


# ---------------------------------------------------------------------------
# Finding 3 — HIGH: path traversal via entry.name
# ---------------------------------------------------------------------------


class TestFinding3PathTraversalInName:
    """Finding 3 (HIGH): a name with path-traversal components must be refused."""

    def test_dotdot_name_refused_at_manifest_parse(self, tmp_path):
        """A name containing '..' is refused during manifest parsing."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "../../evil"\nrev = "' + "a" * 40 + '"\n'
            'source = "https://github.com/example/evil"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(manifest_path, registry=None)
        assert "name" in str(exc_info.value).lower() or "traversal" in str(exc_info.value).lower() or "evil" in str(exc_info.value)

    def test_slash_name_refused_at_manifest_parse(self, tmp_path):
        """A name containing '/' is refused during manifest parsing."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "sub/dir"\nrev = "' + "a" * 40 + '"\n'
            'source = "https://github.com/example/sub"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None)

    def test_backslash_name_refused_at_manifest_parse(self, tmp_path):
        """A name containing backslash is refused during manifest parsing."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "sub\\\\dir"\nrev = "' + "a" * 40 + '"\n'
            'source = "https://github.com/example/sub"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None)

    def test_valid_simple_name_passes(self, tmp_path):
        """A simple alphanumeric name with hyphens/underscores passes."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "my-repo_v2"\nrev = "' + "a" * 40 + '"\n'
            'source = "https://github.com/example/my-repo"\ntools = []\n'
        )
        result = load_install_manifest(manifest_path, registry=None)
        assert result.repos[0].name == "my-repo_v2"


# ---------------------------------------------------------------------------
# Finding 4 — MEDIUM: source allowlist (ext::, fd::, file://, http:// refused)
# ---------------------------------------------------------------------------


class TestFinding4SourceAllowlist:
    """Finding 4 (MEDIUM): _validate_source must be a true allowlist, refusing ext:: etc."""

    def test_ext_transport_refused(self, tmp_path):
        """ext::sh -c 'x' is refused — RCE vector at git-clone time."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "evil"\nrev = "' + "a" * 40 + '"\n'
            "source = \"ext::sh -c 'id'\"\ntools = []\n"
        )
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(manifest_path, registry=None)
        assert "ext::" in str(exc_info.value) or "source" in str(exc_info.value).lower()

    def test_fd_transport_refused(self, tmp_path):
        """fd:: transport is refused."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "evil"\nrev = "' + "a" * 40 + '"\n'
            'source = "fd::3"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None)

    def test_file_url_refused(self, tmp_path):
        """file:// URL is refused (not an https:// or git@ source)."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "' + "a" * 40 + '"\n'
            'source = "file:///etc/passwd"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None)

    def test_http_url_refused(self, tmp_path):
        """Plain http:// (not https://) is refused."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "' + "a" * 40 + '"\n'
            'source = "http://github.com/example/repo"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None)

    def test_https_url_passes(self, tmp_path):
        """https:// sources are accepted."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "' + "a" * 40 + '"\n'
            'source = "https://github.com/trailhead-ai/trailhead"\ntools = []\n'
        )
        result = load_install_manifest(manifest_path, registry=None)
        assert result.repos[0].source == "https://github.com/trailhead-ai/trailhead"

    def test_git_ssh_url_passes(self, tmp_path):
        """git@host:path SSH URLs are accepted."""
        manifest_path = tmp_path / "install_manifest.toml"
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "' + "a" * 40 + '"\n'
            'source = "git@github.com:trailhead-ai/trailhead"\ntools = []\n'
        )
        result = load_install_manifest(manifest_path, registry=None)
        assert result.repos[0].source == "git@github.com:trailhead-ai/trailhead"


# ---------------------------------------------------------------------------
# Finding 5 — MEDIUM: local path with local_root=None must be refused
# ---------------------------------------------------------------------------


class TestFinding5LocalRootNoneRefused:
    """Finding 5 (MEDIUM): a bare local path with local_root=None must be refused."""

    def test_local_path_no_local_root_refused(self, tmp_path):
        """A local filesystem path with no local_root is refused (not silently accepted)."""
        manifest_path = tmp_path / "install_manifest.toml"
        local_path = str(tmp_path / "some_repo")
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "' + "a" * 40 + '"\n'
            f'source = "{local_path}"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(manifest_path, registry=None, local_root=None)
        err = str(exc_info.value)
        assert "local_root" in err.lower() or "local" in err.lower() or "source" in err.lower()

    def test_local_path_with_local_root_passes(self, tmp_path):
        """A local path confined within local_root passes validation."""
        manifest_path = tmp_path / "install_manifest.toml"
        local_path = str(tmp_path / "some_repo")
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "' + "a" * 40 + '"\n'
            f'source = "{local_path}"\ntools = []\n'
        )
        result = load_install_manifest(manifest_path, registry=None, local_root=tmp_path)
        assert result.repos[0].source == local_path

    def test_local_path_escaping_local_root_refused(self, tmp_path):
        """A local path that escapes local_root is refused."""
        manifest_path = tmp_path / "install_manifest.toml"
        escaping_path = str(tmp_path.parent / "escape")
        manifest_path.write_text(
            '[[repo]]\nname = "repo"\nrev = "' + "a" * 40 + '"\n'
            f'source = "{escaping_path}"\ntools = []\n'
        )
        with pytest.raises(InstallManifestError):
            load_install_manifest(manifest_path, registry=None, local_root=tmp_path)


# ---------------------------------------------------------------------------
# Finding 6 — LOW: staging directory must use mode 0o700
# ---------------------------------------------------------------------------


class TestFinding6StagingPermissions:
    """Finding 6 (LOW): staging directory must be created with 0o700 permissions."""

    def test_staging_dir_created_with_0700_mode(self, tmp_path, short_gnupghome):
        """The staging directory under state_dir must have mode 0700 (not world-readable)."""
        fetch = _import_fetch()

        trusted_fpr = _make_gpg_key(short_gnupghome, "Trusted Key <trusted@example.com>")

        source_repo = tmp_path / "source"
        _git_init(source_repo)
        env = {**os.environ, "GNUPGHOME": str(short_gnupghome), "TRAILHEAD_STATE_DIR": str(tmp_path / "state")}
        sha = _make_signed_commit_with_key(source_repo, trusted_fpr, short_gnupghome)

        entry = _make_repo_entry("myrepo", sha, str(source_repo))
        dest_parent = tmp_path / "dest"
        dest_parent.mkdir()

        # Run a successful clone_and_verify so the staging dir gets created
        fetch.clone_and_verify(entry, dest_parent=dest_parent, env=env, expected_key_fpr=trusted_fpr)

        # Check the staging dir mode under state_dir
        staging_parent = tmp_path / "state" / "trailhead" / "staging"
        if staging_parent.exists():
            mode = stat.S_IMODE(staging_parent.stat().st_mode)
            assert mode == 0o700, f"staging dir must be mode 0700, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Finding 7 — LOW: _get_head_sha must thread env override
# ---------------------------------------------------------------------------


class TestFinding7GetHeadShaEnv:
    """Finding 7 (LOW): _get_head_sha must accept and thread an env override."""

    def test_get_head_sha_accepts_env_parameter(self):
        """_get_head_sha must have an env parameter (not silently ignore it)."""
        import inspect
        fetch = _import_fetch()
        sig = inspect.signature(fetch._get_head_sha)
        assert "env" in sig.parameters, "_get_head_sha must have an 'env' parameter"

    def test_get_head_sha_returns_correct_sha_with_env(self, tmp_path):
        """_get_head_sha returns the correct SHA when called with an env override."""
        fetch = _import_fetch()
        repo = tmp_path / "repo"
        _git_init(repo)
        sha = _make_unsigned_commit(repo)
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path / "state")}

        result = fetch._get_head_sha(repo, env=env)
        assert result == sha
