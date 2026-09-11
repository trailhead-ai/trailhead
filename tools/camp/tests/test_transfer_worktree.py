"""Tests for `camp transfer-receive worktree` — one member's working-tree
content, crossing directly from sender to peer.

Test contract (all must RED before implementation, GREEN after):

- an uncommitted edit to a tracked file arrives with the sender's content.
- an untracked file arrives.
- a file listed in that member's `excluded` does NOT arrive, and a file
  beneath a declared excluded directory does not either.
- `.git` does not arrive, and the peer's worktree git linkage still resolves
  afterwards — the peer's `git status` in that worktree works.
- an archive member whose path escapes the worktree (absolute, `..`, or a
  symlink out) is refused and nothing is written outside the worktree.
- the stream is consumed incrementally — a worktree larger than the
  process's memory budget does not require buffering the whole archive.
- a re-run over a worktree carrying a file from a prior attempt that the
  sender no longer has: that file is gone (delivered by `begin`'s teardown,
  asserted here end to end).
- `camp transfer-receive worktree` is reachable as a real command through
  the actual `camp` dispatcher, not only by calling `worktree()` directly.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

from ._helpers import camp_state_env, init_git_repo

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import _bootstrap  # noqa: E402

_bootstrap.ensure_trailhead_importable()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _archive_bytes(worktree: Path, excluded: tuple[str, ...] = ()) -> bytes:
    from camp.transfer.worktree import write_archive

    buf = io.BytesIO()
    write_archive(worktree, excluded, buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# write_archive() + extract_archive() round trip — no group config involved.
# ---------------------------------------------------------------------------


@pytest.fixture()
def sender_worktree(tmp_path: Path) -> Path:
    """A plain directory standing in for a member's worktree on the sender:
    a tracked file carrying an uncommitted edit, an untracked file, an
    excluded file, a file beneath an excluded directory, and a `.git`
    directory that must never cross."""
    wt = tmp_path / "sender-wt"
    wt.mkdir()
    (wt / "tracked.txt").write_text("edited but not committed\n")
    (wt / "new.txt").write_text("never added to git\n")
    (wt / "secrets.env").write_text("SECRET=1\n")
    (wt / "build").mkdir()
    (wt / "build" / "artifact.bin").write_text("regenerable\n")
    (wt / ".git").mkdir()
    (wt / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return wt


class TestContentCrosses:
    def test_uncommitted_edit_and_untracked_file_arrive(
        self, sender_worktree: Path, tmp_path: Path
    ):
        from camp.transfer.worktree import extract_archive

        archive = _archive_bytes(sender_worktree, excluded=("secrets.env", "build"))
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()

        extract_archive(io.BytesIO(archive), peer_wt)

        assert (peer_wt / "tracked.txt").read_text() == "edited but not committed\n"
        assert (peer_wt / "new.txt").read_text() == "never added to git\n"

    def test_excluded_file_and_file_beneath_excluded_dir_do_not_arrive(
        self, sender_worktree: Path, tmp_path: Path
    ):
        from camp.transfer.worktree import extract_archive

        archive = _archive_bytes(sender_worktree, excluded=("secrets.env", "build"))
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()

        extract_archive(io.BytesIO(archive), peer_wt)

        assert not (peer_wt / "secrets.env").exists()
        assert not (peer_wt / "build").exists()
        assert not (peer_wt / "build" / "artifact.bin").exists()

    def test_undeclared_file_still_arrives_when_not_excluded(
        self, sender_worktree: Path, tmp_path: Path
    ):
        """A property this excluded-set enumeration must vary: a file NOT
        named in `excluded` is not swept away incidentally."""
        from camp.transfer.worktree import extract_archive

        archive = _archive_bytes(sender_worktree, excluded=("secrets.env", "build"))
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()

        extract_archive(io.BytesIO(archive), peer_wt)

        assert (peer_wt / "tracked.txt").exists()


class TestGitLinkageSurvives:
    def test_git_dir_does_not_arrive_and_peer_git_status_still_works(
        self, sender_worktree: Path, tmp_path: Path
    ):
        from camp.transfer.worktree import extract_archive

        # A REAL git worktree on the peer side — `git worktree add` gives it
        # a genuine `.git` file (not directory) pointing at the main repo's
        # `.git/worktrees/<name>` admin area, exactly what `history`'s phase
        # produces before `worktree` ever runs.
        main_repo = tmp_path / "main-repo"
        init_git_repo(main_repo, origin=False)
        _git(main_repo, "branch", "worktree-feat")
        peer_wt = tmp_path / "peer-wt"
        _git(main_repo, "worktree", "add", str(peer_wt), "worktree-feat")
        assert (peer_wt / ".git").is_file()  # a FILE, not a dir, for a worktree

        archive = _archive_bytes(sender_worktree, excluded=("secrets.env", "build"))
        extract_archive(io.BytesIO(archive), peer_wt)

        # .git untouched: still a file (never overwritten by the sender's
        # own `.git` directory, which the archive never even carried).
        assert (peer_wt / ".git").is_file()
        status = subprocess.run(
            ["git", "-C", str(peer_wt), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        assert status.returncode == 0, status.stderr


# ---------------------------------------------------------------------------
# Confinement — a hostile or corrupted archive is refused before it writes.
# ---------------------------------------------------------------------------


def _malicious_archive(member_name: str, *, symlink_to: str | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name=member_name)
        if symlink_to is not None:
            info.type = tarfile.SYMTYPE
            info.linkname = symlink_to
            tf.addfile(info)
        else:
            data = b"payload\n"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestConfinement:
    def test_absolute_path_member_refused_and_nothing_written_outside(self, tmp_path: Path):
        from camp.transfer.worktree import ArchiveMemberEscaped, extract_archive

        outside = tmp_path / "outside.txt"
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        archive = _malicious_archive(str(outside))

        with pytest.raises(ArchiveMemberEscaped) as exc_info:
            extract_archive(io.BytesIO(archive), peer_wt)
        assert str(outside) in str(exc_info.value) or "absolute" in str(exc_info.value)
        assert not outside.exists()

    def test_dotdot_member_refused_and_nothing_written_outside(self, tmp_path: Path):
        from camp.transfer.worktree import ArchiveMemberEscaped, extract_archive

        peer_wt = tmp_path / "nested" / "peer-wt"
        peer_wt.mkdir(parents=True)
        escaping_target = peer_wt.parent / "evil.txt"
        archive = _malicious_archive("../evil.txt")

        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(archive), peer_wt)
        assert not escaping_target.exists()

    def test_symlink_escaping_worktree_refused_and_nothing_written_outside(self, tmp_path: Path):
        from camp.transfer.worktree import ArchiveMemberEscaped, extract_archive

        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        archive = _malicious_archive("escape-link", symlink_to="../../etc/passwd")

        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(archive), peer_wt)
        assert not (peer_wt / "escape-link").exists()

    def test_refusal_holds_even_when_data_filter_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The floor of this repository is Python 3.11, and 3.11.0-3.11.3
        carry no `tarfile.data_filter` at all. The confinement check must
        not depend on it being present — simulate its absence and confirm
        the escaping member is still refused."""
        import camp.transfer.worktree as worktree_mod

        monkeypatch.delattr(worktree_mod.tarfile, "data_filter", raising=False)

        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        outside = tmp_path / "outside2.txt"
        archive = _malicious_archive(str(outside))

        with pytest.raises(worktree_mod.ArchiveMemberEscaped):
            worktree_mod.extract_archive(io.BytesIO(archive), peer_wt)
        assert not outside.exists()

    def test_safe_member_still_extracts_when_data_filter_is_unavailable(
        self, sender_worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The other side of the same property: removing `data_filter`
        must not turn into an over-broad refusal of ordinary content."""
        import camp.transfer.worktree as worktree_mod

        monkeypatch.delattr(worktree_mod.tarfile, "data_filter", raising=False)

        archive = _archive_bytes(sender_worktree, excluded=("secrets.env", "build"))
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()

        worktree_mod.extract_archive(io.BytesIO(archive), peer_wt)

        assert (peer_wt / "tracked.txt").read_text() == "edited but not committed\n"


# ---------------------------------------------------------------------------
# Mode sanitization — the setuid/setgid/sticky bits and group/other write
# bits an archive member declares must never survive extraction, independent
# of whether `tarfile.data_filter` is available on the running interpreter.
# ---------------------------------------------------------------------------


def _mode_archive(member_name: str, mode: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name=member_name)
        data = b"payload\n"
        info.size = len(data)
        info.mode = mode
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestModeSanitization:
    def test_setuid_and_world_writable_bits_are_stripped_on_extraction(
        self, tmp_path: Path
    ):
        from camp.transfer.worktree import extract_archive

        archive = _mode_archive("evil.sh", 0o4777)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()

        extract_archive(io.BytesIO(archive), peer_wt)

        extracted_mode = (peer_wt / "evil.sh").stat().st_mode & 0o7777
        assert extracted_mode == 0o755
        assert extracted_mode & 0o4000 == 0  # setuid gone
        assert extracted_mode & 0o022 == 0  # group/other write gone

    def test_normal_mode_member_keeps_its_normal_mode(self, tmp_path: Path):
        from camp.transfer.worktree import extract_archive

        archive = _mode_archive("plain.txt", 0o644)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()

        extract_archive(io.BytesIO(archive), peer_wt)

        extracted_mode = (peer_wt / "plain.txt").stat().st_mode & 0o7777
        assert extracted_mode == 0o644


# ---------------------------------------------------------------------------
# Incremental consumption — a stream larger than the pipe's own buffer must
# not require buffering the whole thing before extraction can start.
# ---------------------------------------------------------------------------


class TestIncrementalConsumption:
    def test_first_member_lands_while_the_stream_is_still_open(self, tmp_path: Path):
        """A direct proof of incrementality, not just an absence of deadlock:
        the underlying pipe is paused (application-level, independent of any
        internal buffering `tarfile` itself does on either end) once the
        first member's bytes have definitely crossed it, with the stream
        still open — no EOF. `extract_archive` must have already written
        that member to disk during the pause. An implementation that reads
        the whole stream first (an unbounded `fileobj.read()`) keeps
        accumulating bytes as they arrive but cannot begin parsing or
        extracting anything until `read()` itself returns, which does not
        happen until EOF — long after this pause starts.

        The first member is sized well past `tarfile`'s own internal
        streaming-buffer granularity (`RECORDSIZE`, 10240 bytes) so its
        bytes are guaranteed to have actually reached the pipe by the time
        the pause point (comfortably past that size) is hit — otherwise
        tarfile's own write-side buffering could mask a real regression
        by holding a small member back regardless of this test.
        """
        from camp.transfer.worktree import extract_archive, write_archive

        src = tmp_path / "src"
        src.mkdir()
        first_size = 40 * 1024
        second_size = 40 * 1024
        (src / "first.bin").write_bytes(os.urandom(first_size))
        (src / "second.bin").write_bytes(os.urandom(second_size))

        read_fd, write_fd = os.pipe()
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        release = threading.Event()

        class _PausingWriter:
            """Wraps the real pipe write end. Once at least `first.bin`'s
            own tar-formatted bytes have definitely been pushed through, it
            pauses (with the stream left open) until released."""

            def __init__(self, raw) -> None:
                self._raw = raw
                self._sent = 0
                self._paused = False

            def write(self, data: bytes) -> int:
                n = self._raw.write(data)
                self._sent += n
                if not self._paused and self._sent >= first_size + 5000:
                    self._paused = True
                    released = release.wait(timeout=10)
                    assert released, "test never released the paused producer"
                return n

            def flush(self) -> None:
                self._raw.flush()

            def close(self) -> None:
                self._raw.close()

        def _produce() -> None:
            raw_w = os.fdopen(write_fd, "wb", buffering=0)
            write_archive(src, (), _PausingWriter(raw_w))
            raw_w.close()

        producer = threading.Thread(target=_produce, daemon=True)
        producer.start()

        result: dict[str, object] = {}

        def _consume() -> None:
            r = os.fdopen(read_fd, "rb", buffering=0)
            try:
                extract_archive(r, peer_wt)
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc
            finally:
                r.close()

        consumer = threading.Thread(target=_consume, daemon=True)
        consumer.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (peer_wt / "first.bin").exists():
            time.sleep(0.02)

        assert (peer_wt / "first.bin").exists(), (
            "the first archive member never landed while the producer was "
            "still paused mid-stream — consistent with buffering the whole "
            "stream before extracting anything"
        )
        assert (peer_wt / "first.bin").stat().st_size == first_size
        # second.bin's data had only started crossing (if at all) when the
        # producer paused — it cannot be COMPLETE yet, though extraction may
        # have already opened the file and written a partial prefix.
        second_size_now = (
            (peer_wt / "second.bin").stat().st_size if (peer_wt / "second.bin").exists() else 0
        )
        assert second_size_now < second_size

        release.set()
        producer.join(timeout=5)
        consumer.join(timeout=5)
        assert not consumer.is_alive()
        assert result.get("ok") is True, result.get("error")
        assert (peer_wt / "second.bin").stat().st_size == second_size


# ---------------------------------------------------------------------------
# receive.worktree() — resolves the destination from local group config.
# ---------------------------------------------------------------------------


def _make_group(name: str, members: list[dict]) -> dict:
    return {"group": {"name": name}, "members": members, "branch_pattern": "worktree-{slug}"}


class TestReceiveWorktreePhase:
    def test_worktree_phase_lands_content_at_the_locally_resolved_worktree_path(
        self, sender_worktree: Path, tmp_path: Path
    ):
        from camp.provision.reconcile import _worktree_path
        from camp.transfer import receive

        peer_repo = tmp_path / "peer_repo_a"
        init_git_repo(peer_repo, origin=False)
        group = _make_group(
            "testgroup",
            [{"name": "repo_a", "repo_root": str(peer_repo), "tasks": [], "base": "origin/main"}],
        )
        env = camp_state_env(tmp_path)
        wt_path = _worktree_path("testgroup", "feat-x", "repo_a", env=env)
        wt_path.mkdir(parents=True)

        archive = _archive_bytes(sender_worktree, excluded=("secrets.env", "build"))

        result = receive.worktree(
            groups=[group],
            group_name="testgroup",
            slug="feat-x",
            member="repo_a",
            archive_stream=io.BytesIO(archive),
            env=env,
        )

        assert result["member"] == "repo_a"
        assert (wt_path / "tracked.txt").read_text() == "edited but not committed\n"
        assert not (wt_path / "secrets.env").exists()

    def test_worktree_phase_refuses_when_member_not_configured(self, tmp_path: Path):
        from camp.transfer import receive

        group = _make_group("testgroup", [])
        env = camp_state_env(tmp_path)

        with pytest.raises(receive.MemberNotConfigured):
            receive.worktree(
                groups=[group],
                group_name="testgroup",
                slug="feat-x",
                member="repo_a",
                archive_stream=io.BytesIO(b""),
                env=env,
            )


# ---------------------------------------------------------------------------
# Re-run: a prior attempt's leftover content does not survive a fresh begin.
# ---------------------------------------------------------------------------


class TestRerunDropsStaleContent:
    def test_file_from_prior_attempt_absent_from_sender_is_gone_after_rerun(
        self, tmp_path: Path
    ):
        from camp.transfer import receive

        sender_repo = tmp_path / "sender"
        init_git_repo(sender_repo, origin=True)
        branch = "worktree-feat-rerun"
        _git(sender_repo, "checkout", "-b", branch)
        (sender_repo / "keep.txt").write_text("kept across both attempts\n")
        _git(sender_repo, "add", "keep.txt")
        _git(
            sender_repo, "-c", "user.email=t@t.com", "-c", "user.name=t",
            "commit", "-m", "first attempt content", "--no-gpg-sign",
        )

        peer_repo = tmp_path / "peer_repo_a"
        peer_repo.mkdir(parents=True)
        _git(peer_repo, "init", "-q", "-b", "main")
        (peer_repo / "peer-only.md").write_text("# peer init\n")
        _git(peer_repo, "add", "peer-only.md")
        _git(
            peer_repo, "-c", "user.email=peer@test.com", "-c", "user.name=Peer",
            "commit", "-m", "peer init", "--no-gpg-sign",
        )

        group = _make_group(
            "testgroup",
            [{"name": "repo_a", "repo_root": str(peer_repo), "tasks": [], "base": "origin/main"}],
        )
        env = camp_state_env(tmp_path)

        def _bundle(basis: str | None = None) -> bytes:
            from camp.transfer.history import build_bundle_argv

            argv = build_bundle_argv(sender_repo, branch, basis_commit=basis)
            return subprocess.run(argv, capture_output=True, check=True).stdout

        # --- first attempt: begin, history, worktree -----------------------
        receive.begin(
            groups=[group], group_name="testgroup", slug="feat-rerun",
            sender="host-a", overwrite=False, env=env,
        )
        receive.history(
            groups=[group], group_name="testgroup", slug="feat-rerun",
            member="repo_a", bundle_bytes=_bundle(), env=env,
        )
        from camp.provision.reconcile import _worktree_path

        wt_path = _worktree_path("testgroup", "feat-rerun", "repo_a", env=env)
        # A file that exists ONLY in this first attempt's worktree, never
        # committed on the sender and never re-sent on the second pass.
        (wt_path / "stale-uncommitted.txt").write_text("only in attempt one\n")
        assert (wt_path / "stale-uncommitted.txt").exists()

        # --- second attempt: begin --overwrite, history, worktree ----------
        receive.begin(
            groups=[group], group_name="testgroup", slug="feat-rerun",
            sender="host-a", overwrite=True, env=env,
        )
        receive.history(
            groups=[group], group_name="testgroup", slug="feat-rerun",
            member="repo_a", bundle_bytes=_bundle(), env=env,
        )
        wt_path_after = _worktree_path("testgroup", "feat-rerun", "repo_a", env=env)
        receive.worktree(
            groups=[group], group_name="testgroup", slug="feat-rerun",
            member="repo_a", archive_stream=io.BytesIO(_archive_bytes(sender_repo)),
            env=env,
        )

        assert (wt_path_after / "keep.txt").exists()
        assert not (wt_path_after / "stale-uncommitted.txt").exists()


# ---------------------------------------------------------------------------
# CLI wiring — `camp transfer-receive worktree` end to end through the real
# dispatcher, exactly like test_transfer_history.py proves `history`.
# ---------------------------------------------------------------------------


def _dispatch_module():
    import importlib

    return importlib.import_module("camp.cli.dispatch")


def _write_group_toml(groups_dir: Path, name: str, members: list[tuple[str, str]]) -> None:
    groups_dir.mkdir(parents=True, exist_ok=True)
    member_tables = "\n\n".join(
        f'[[members]]\nname = "{member_name}"\nrepo_root = "{repo_root}"'
        for member_name, repo_root in members
    )
    (groups_dir / f"{name}.toml").write_text(f'[group]\nname = "{name}"\n\n{member_tables}\n')


class TestWorktreeCliDispatch:
    def test_worktree_requires_member_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["camp", "transfer-receive", "worktree", "--group", "testgroup", "--slug", "x"],
        )
        dispatch = _dispatch_module()
        with pytest.raises(SystemExit) as exc_info:
            dispatch.main()
        assert exc_info.value.code != 0
        assert "--member" in capsys.readouterr().err

    def test_worktree_reachable_through_real_dispatcher_end_to_end(
        self, sender_worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from camp.provision.reconcile import _worktree_path

        peer_repo = tmp_path / "peer_cli_repo_a"
        init_git_repo(peer_repo, origin=False)

        cfg = tmp_path / "config"
        (cfg / "groups").mkdir(parents=True)
        _write_group_toml(cfg / "groups", "testgroup", [("repo_a", str(peer_repo))])
        state_dir = tmp_path / "state"

        monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
        monkeypatch.setenv("CAMP_STATE_DIR", str(state_dir))

        wt_path = _worktree_path("testgroup", "feat-cli", "repo_a", env={"CAMP_STATE_DIR": str(state_dir)})
        wt_path.mkdir(parents=True)

        archive = _archive_bytes(sender_worktree, excluded=("secrets.env", "build"))

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "camp",
                "transfer-receive",
                "worktree",
                "--group",
                "testgroup",
                "--slug",
                "feat-cli",
                "--member",
                "repo_a",
            ],
        )

        class _NoFullReadBytesIO(io.BytesIO):
            """Raises if anything asks it to read the whole stream at once —
            the worktree CLI phase must hand the raw stream to the extractor
            rather than buffering it via a whole-stream `.read()`."""

            def read(self, size: int | None = -1) -> bytes:
                if size is None or size < 0:
                    raise AssertionError(
                        "the worktree CLI phase read the whole stream at "
                        "once instead of streaming it incrementally"
                    )
                return super().read(size)

        class _FakeStdin:
            buffer = _NoFullReadBytesIO(archive)

        monkeypatch.setattr(sys, "stdin", _FakeStdin())

        _dispatch_module().main()  # success path returns normally, no SystemExit

        payload = json.loads(capsys.readouterr().out)
        assert payload["contract_version"] == 1
        assert payload["member"] == "repo_a"
        assert (wt_path / "tracked.txt").read_text() == "edited but not committed\n"
        assert not (wt_path / "secrets.env").exists()
