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


def _simulate_interpreter_without_data_filter(monkeypatch) -> None:
    """Make the running interpreter look like one that never had `data_filter`.

    Removing the attribute alone is not that interpreter. From Python 3.12 the
    stdlib's own `TarFile._get_filter_function` falls back to the `data_filter`
    global when no filter is chosen, so deleting it leaves `tarfile` in a state
    no real interpreter is ever in — on 3.14 the fallback raises `NameError`
    before camp's code is reached at all. An interpreter without `data_filter`
    also extracts fully trusted, because the filter machinery does not exist
    there, so both halves are simulated: the attribute camp looks for is gone,
    and extraction behaves the way it did before filters existed. What is under
    test either way is camp's own confinement check, which is unconditional.
    """
    fully_trusted = tarfile.fully_trusted_filter
    monkeypatch.setattr(
        tarfile.TarFile, "extraction_filter", staticmethod(fully_trusted), raising=False
    )
    monkeypatch.delattr(tarfile, "data_filter", raising=False)



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

        _simulate_interpreter_without_data_filter(monkeypatch)

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

        _simulate_interpreter_without_data_filter(monkeypatch)

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

        def _first_bin_size() -> int:
            first_bin = peer_wt / "first.bin"
            return first_bin.stat().st_size if first_bin.exists() else -1

        # Poll on the fully-written size, not mere existence: tarfile opens
        # (and truncates) the target file before copying any of its data in,
        # so `.exists()` alone can observe a real but still-empty file under
        # scheduling contention (seen under CI's parallel test workers).
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _first_bin_size() != first_size:
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


# ---------------------------------------------------------------------------
# escaping_members() — the sender-side walk that finds, before anything
# crosses, exactly what the peer's confinement gate would refuse.
# ---------------------------------------------------------------------------


class TestEscapingMembersWalk:
    def test_absolute_symlink_target_outside_root_is_named(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (wt / "escape-link").symlink_to(outside)

        result = escaping_members(wt, ())

        assert result == ("escape-link",)

    def test_same_symlink_under_a_declared_excluded_directory_is_clean(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (wt / "nested").mkdir()
        (wt / "nested" / "escape-link").symlink_to(outside)

        result = escaping_members(wt, ("nested",))

        assert result == ()

    def test_relative_symlink_resolving_inside_the_worktree_is_clean(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "target.txt").write_text("hi\n")
        (wt / "inside-link").symlink_to("target.txt")

        result = escaping_members(wt, ())

        assert result == ()

    def test_relative_symlink_climbing_out_via_dotdot_is_named(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        (tmp_path / "evil.txt").write_text("evil\n")
        (wt / "climb-link").symlink_to("../evil.txt")

        result = escaping_members(wt, ())

        assert result == ("climb-link",)

    def test_absolute_symlink_target_inside_the_worktree_is_still_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An absolute target that happens to sit inside the sender's own
        worktree is still refused: the walk evaluates the gate against an
        empty scratch root (never the worktree's own resolved root), so
        this absolute path resolves outside that scratch regardless of
        where it points on the sender's real disk. That means `_check_member`
        alone (not merely `data_filter`) refuses it, so the refusal holds on
        every interpreter — including 3.11.0-3.11.3, which never had
        `data_filter` at all. Sender and peer agree: `extract_archive` also
        refuses the archive `write_archive` produces from this worktree."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        _simulate_interpreter_without_data_filter(monkeypatch)

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "target.txt").write_text("hi\n")
        (wt / "abs-inside-link").symlink_to(wt / "target.txt")

        result = escaping_members(wt, ())

        assert result == ("abs-inside-link",)

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer_wt)

    def test_worktree_passing_the_walk_round_trips_with_no_escape(self, tmp_path: Path):
        """Sender and peer agree in the clean direction: a worktree
        `escaping_members` reports clean crosses through the real
        `write_archive` -> `extract_archive` pair with no
        `ArchiveMemberEscaped`."""
        from camp.transfer.worktree import escaping_members, extract_archive, write_archive

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "target.txt").write_text("hi\n")
        (wt / "inside-link").symlink_to("target.txt")

        assert escaping_members(wt, ()) == ()

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        extract_archive(io.BytesIO(buf.getvalue()), peer_wt)  # must not raise

        assert (peer_wt / "inside-link").is_symlink()

    def test_worktree_failing_the_walk_is_also_refused_by_extract_archive(self, tmp_path: Path):
        """Sender and peer agree in the failing direction: a worktree
        `escaping_members` names is refused by the real `extract_archive`
        when it receives the archive `write_archive` produced from it."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        wt = tmp_path / "wt"
        wt.mkdir()
        (tmp_path / "evil.txt").write_text("evil\n")
        (wt / "climb-link").symlink_to("../evil.txt")

        assert escaping_members(wt, ()) == ("climb-link",)

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer_wt)

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root ignores the chmod(0o000) permission bit"
    )
    def test_excluded_directory_made_unreadable_is_never_descended_into(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        blocked = wt / "blocked"
        blocked.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (blocked / "escape-link").symlink_to(outside)
        blocked.chmod(0o000)
        try:
            result = escaping_members(wt, ("blocked",))
        finally:
            blocked.chmod(0o755)

        assert result == ()

    def test_missing_worktree_directory_is_reported_unwalkable_not_clean(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        result = escaping_members(tmp_path / "does-not-exist", ())

        assert result is None

    def test_more_than_ten_offenders_are_all_named(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        for i in range(12):
            (wt / f"escape-link-{i}").symlink_to(outside)

        result = escaping_members(wt, ())

        assert len(result) == 12
        assert set(result) == {f"escape-link-{i}" for i in range(12)}

    def test_fifo_is_reported_and_refused_on_the_archive_it_writes(self, tmp_path: Path):
        """`write_archive` emits a FIFO as a real archive member (`tarfile`
        adds it by type, never by reading its contents), and the peer's gate
        (`_check_member`'s `_UNSAFE_TYPES`) refuses a device or special
        file. The sender must catch that ahead of time too — not only a
        symlink."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        wt = tmp_path / "wt"
        wt.mkdir()
        os.mkfifo(wt / "a-fifo")

        result = escaping_members(wt, ())

        assert result == ("a-fifo",)

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer_wt)

    def test_fifo_under_an_excluded_directory_is_never_walked(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "blocked").mkdir()
        os.mkfifo(wt / "blocked" / "a-fifo")

        result = escaping_members(wt, ("blocked",))

        assert result == ()

    def test_relative_symlink_climbing_out_one_level_stays_inside_and_is_clean(
        self, tmp_path: Path
    ):
        """`sub/up -> ..` lexically resolves back to the worktree root
        itself under the scratch-root evaluation — the sender must not
        follow this on-disk link back through its own real worktree, which
        would otherwise walk past the root into the parent of `tmp_path`
        and (depending on layout) refuse a link the peer would accept.
        Sender and peer agree: both accept."""
        from camp.transfer.worktree import escaping_members, extract_archive, write_archive

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "sub").mkdir()
        (wt / "sub" / "up").symlink_to("..")

        assert escaping_members(wt, ()) == ()

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        extract_archive(io.BytesIO(buf.getvalue()), peer_wt)  # must not raise

    def test_self_referential_symlink_is_evaluated_the_same_on_both_ends(self, tmp_path: Path):
        """`self -> .` — whatever the peer does with it, the sender's
        evaluation must agree, because the sender's own on-disk copy of
        `self` already exists and resolving through it (rather than against
        an empty scratch root) would make the sender see something the peer
        — extracting into a tree that does not yet contain `self` — never
        sees."""
        from camp.transfer.worktree import escaping_members, extract_archive, write_archive

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "self").symlink_to(".")

        sender_verdict = escaping_members(wt, ())

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        if sender_verdict == ():
            extract_archive(io.BytesIO(buf.getvalue()), peer_wt)  # must not raise
        else:
            from camp.transfer.worktree import ArchiveMemberEscaped

            with pytest.raises(ArchiveMemberEscaped):
                extract_archive(io.BytesIO(buf.getvalue()), peer_wt)

    def test_excluded_venv_chained_absolute_link_is_clean_for_both_ends(self, tmp_path: Path):
        """A top-level link (`py -> .venv/bin/python`) that is itself
        emitted must not be evaluated by following it all the way through
        `.venv`'s own (excluded, never-crossed) further symlink hop to
        wherever that ultimately points — the peer never extracts `.venv`
        at all, so it only ever sees the literal one-hop target `py`
        declares. An implementation that resolves against the real
        worktree root would find the chain lands outside and wrongly
        refuse a link the peer accepts."""
        from camp.transfer.worktree import escaping_members, extract_archive, write_archive

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".venv" / "bin").mkdir(parents=True)
        outside = tmp_path / "outside-interpreter"
        outside.write_text("#!/bin/sh\n")
        (wt / ".venv" / "bin" / "python").symlink_to(outside)
        (wt / "py").symlink_to(".venv/bin/python")

        result = escaping_members(wt, (".venv",))

        assert result == ()

        buf = io.BytesIO()
        write_archive(wt, (".venv",), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        extract_archive(io.BytesIO(buf.getvalue()), peer_wt)  # must not raise

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root ignores the chmod(0o000) permission bit"
    )
    def test_unreadable_non_excluded_directory_is_reported_unwalkable(self, tmp_path: Path):
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        blocked = wt / "blocked"
        blocked.mkdir()
        (blocked / "inner.txt").write_text("hi\n")
        blocked.chmod(0o000)
        try:
            result = escaping_members(wt, ())
        finally:
            blocked.chmod(0o755)

        assert result is None

    def test_chained_link_through_an_already_accepted_sibling_is_named(self, tmp_path: Path):
        """`sub/up -> ..` alone resolves back to the worktree root and is
        accepted — but once that link is standing, a later sibling `z` whose
        own target routes back through it (`sub/up/..`) lands one level
        above the root. The walk must evaluate `z` against the state left by
        `sub/up` already having been accepted, exactly as the peer does when
        it extracts `sub/up` before it ever reaches `z`. Sender and peer
        agree: both refuse `z`."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "sub").mkdir()
        (wt / "sub" / "up").symlink_to("..")
        (wt / "z").symlink_to("sub/up/..")

        result = escaping_members(wt, ())

        assert "z" in result

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer_wt)

    def test_chained_link_through_a_self_referential_sibling_is_named(self, tmp_path: Path):
        """`a -> .` alone resolves to the worktree root and is accepted, but
        `b -> a/..` — evaluated after `a` already stands — routes one level
        above the root through it. Sender and peer agree: both refuse `b`."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "a").symlink_to(".")
        (wt / "b").symlink_to("a/..")

        result = escaping_members(wt, ())

        assert "b" in result

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer_wt)

    def test_chained_link_two_levels_deep_through_an_accepted_sibling_is_named(
        self, tmp_path: Path
    ):
        """`a/b/up -> ../..` alone resolves exactly to the worktree root
        (still accepted), but `z -> a/b/up/../../..` — evaluated after
        `a/b/up` already stands — climbs three levels above the root through
        it. Sender and peer agree: both refuse `z`."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "a" / "b").mkdir(parents=True)
        (wt / "a" / "b" / "up").symlink_to("../..")
        (wt / "z").symlink_to("a/b/up/../../..")

        result = escaping_members(wt, ())

        assert "z" in result

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer_wt)

    def test_link_that_sorts_before_the_link_it_routes_through_is_clean_in_order(
        self, tmp_path: Path
    ):
        """`link -> self/..` sorts before `self -> .`. At the moment `link`
        is checked, `self` has not been materialized in the replay yet, so
        `link`'s target is evaluated literally (a not-yet-existing path
        component) and stays inside the root — the same thing the peer sees,
        since it extracts in this same order and `self` is not on its disk
        yet either. Reversing the replay order would materialize `self`
        first (itself accepted, a real symlink to the root), and `link`
        would then resolve THROUGH it and climb one level above the root —
        proof that the clean verdict here depends on the order itself, not
        just on each link's own target. Sender and peer agree: both accept.
        Replaces the removed `test_reversed_traversal_order_would_miss_the_chained_escape`,
        which asserted only `sorted(("sub", "z")) == ["sub", "z"]` — a
        restated constant, not a run of the walk."""
        from camp.transfer.worktree import escaping_members, extract_archive, write_archive

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "link").symlink_to("self/..")
        (wt / "self").symlink_to(".")

        assert escaping_members(wt, ()) == ()

        buf = io.BytesIO()
        write_archive(wt, (), buf)
        peer_wt = tmp_path / "peer-wt"
        peer_wt.mkdir()
        extract_archive(io.BytesIO(buf.getvalue()), peer_wt)  # must not raise

    def test_symlink_loop_is_named_an_offender_not_a_crash(self, tmp_path: Path):
        """A symlink loop already standing before a later member routes
        through it must be treated as an offender — a `RuntimeError` from
        `Path.resolve()` crashing the whole walk would leave every other
        member's status unreported."""
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "loop").symlink_to("loop")
        (wt / "victim").symlink_to("loop")

        result = escaping_members(wt, ())

        assert result == ("victim",)

    def test_excluding_the_escaping_symlink_by_its_own_exact_path_is_clean(self, tmp_path: Path):
        """The remedy the FAILED detail names — `excluded` carrying the
        offending path itself, not just an enclosing directory."""
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (wt / "bin").mkdir()
        (wt / "bin" / "python").symlink_to(outside)

        result = escaping_members(wt, ("bin/python",))

        assert result == ()


# ---------------------------------------------------------------------------
# escaping_members()'s scratch root is named after the worktree's own
# basename — a relative link that climbs out and re-enters by that name must
# be judged the way a real peer (whose own root carries that same name)
# judges it.
# ---------------------------------------------------------------------------


class TestScratchRootBasenameMatchesTheWorktree:
    def test_relative_link_reentering_by_the_worktrees_own_basename_is_clean(
        self, tmp_path: Path
    ):
        """`docs/x -> ../../member/README` climbs two levels above `docs`
        and back down into a directory named `member` — the worktree's own
        basename. On the real peer, whose extraction root is *also* named
        `member` (whatever its parent path), that same climb lands back
        inside its own root. A scratch root with a random name would put
        that reentry point nowhere real, reading as an escape the peer never
        sees."""
        from camp.transfer.worktree import escaping_members, extract_archive, write_archive

        sender_root = tmp_path / "sender-parent" / "member"
        sender_root.mkdir(parents=True)
        (sender_root / "docs").mkdir()
        (sender_root / "README").write_text("hi\n")
        (sender_root / "docs" / "x").symlink_to("../../member/README")

        assert escaping_members(sender_root, ()) == ()

        buf = io.BytesIO()
        write_archive(sender_root, (), buf)
        peer_root = tmp_path / "peer-parent" / "member"
        peer_root.mkdir(parents=True)
        extract_archive(io.BytesIO(buf.getvalue()), peer_root)  # must not raise

        assert (peer_root / "docs" / "x").is_symlink()


# ---------------------------------------------------------------------------
# escaping_members()'s optional `tip` seeds the scratch root from the tree
# the peer's `history` phase has already checked out before its `worktree`
# phase ever runs — the peer never extracts into an empty root, and these
# prove the sender now agrees with what a REAL git checkout produces.
# ---------------------------------------------------------------------------


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c", "commit.gpgsign=false",
        "-c", "user.email=t@t.com",
        "-c", "user.name=t",
        "commit", "-m", message, "--no-gpg-sign",
    )
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _worktree_at_tip(repo: Path, dest: Path, tip: str) -> None:
    """A real linked git worktree of *repo*, checked out detached at *tip* —
    exactly the shape `_add_worktree_for_member` leaves for the peer's
    `history` phase to have already produced before `worktree` extracts."""
    _git(repo, "worktree", "add", "-q", "--detach", str(dest), tip)


class TestEscapingMembersAgreesWithARealCheckout:
    def test_tracked_self_reentering_link_is_refused_once_the_checkout_already_holds_it(
        self, tmp_path: Path
    ):
        """`sub/up -> ..` alone is accepted against an empty root — but the
        peer's checkout ALREADY has `sub/up` standing before the archive
        even lands, so re-extracting the very same member resolves its own
        NAME back to the destination root itself (`tarfile`'s "resolves to
        the destination itself" guard) and the peer refuses it. Sender and
        peer agree once seeded from the real tip; against an empty root
        (`tip=None`) they would disagree — this is the reported gap."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "sub").mkdir()
        (repo / "sub" / "up").symlink_to("..")
        tip = _commit_all(repo, "add sub/up")

        peer = tmp_path / "peer"
        _worktree_at_tip(repo, peer, tip)

        assert escaping_members(repo, (), tip) == ("sub/up",)

        buf = io.BytesIO()
        write_archive(repo, (), buf)
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer)

    def test_tracked_self_referential_link_is_refused_once_the_checkout_already_holds_it(
        self, tmp_path: Path
    ):
        """Same shape as `sub/up -> ..`, for the simpler `self -> .`."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "self").symlink_to(".")
        tip = _commit_all(repo, "add self")

        peer = tmp_path / "peer"
        _worktree_at_tip(repo, peer, tip)

        assert escaping_members(repo, (), tip) == ("self",)

        buf = io.BytesIO()
        write_archive(repo, (), buf)
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer)

    def test_tracked_chained_link_through_an_already_checked_out_sibling_is_named(
        self, tmp_path: Path
    ):
        """`a -> b/..` sorts before `b -> .` — over an empty root both would
        be accepted (the ordering tests above). But the peer's checkout
        already has `b` standing before `a` is even checked, so `a` resolves
        through it and climbs one level above the root. Sender and peer
        agree: both refuse `a`."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "a").symlink_to("b/..")
        (repo / "b").symlink_to(".")
        tip = _commit_all(repo, "add a and b")

        peer = tmp_path / "peer"
        _worktree_at_tip(repo, peer, tip)

        result = escaping_members(repo, (), tip)

        assert "a" in result

        buf = io.BytesIO()
        write_archive(repo, (), buf)
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer)

    def test_link_routing_through_a_locally_deleted_but_still_checked_out_sibling_is_named(
        self, tmp_path: Path
    ):
        """`zz -> .` is committed, then deleted from the sender's own
        working tree (uncommitted) — `write_archive` never emits it, since
        it enumerates the real worktree, not git's history. But the peer's
        checkout still has it (the `history` phase materializes *tip*'s
        tree regardless of what the sender's working tree currently holds),
        so a NEW link `a -> zz/..` still routes through it and escapes."""
        from camp.transfer.worktree import (
            ArchiveMemberEscaped,
            escaping_members,
            extract_archive,
            write_archive,
        )

        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "zz").symlink_to(".")
        tip = _commit_all(repo, "add zz")

        peer = tmp_path / "peer"
        _worktree_at_tip(repo, peer, tip)

        (repo / "zz").unlink()  # uncommitted deletion — tip still has it
        (repo / "a").symlink_to("zz/..")

        result = escaping_members(repo, (), tip)

        assert "a" in result

        buf = io.BytesIO()
        write_archive(repo, (), buf)
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer)

    def test_clean_worktree_over_a_real_checkout_is_accepted_by_both(self, tmp_path: Path):
        """The control case: nothing escaping, seeded from a real tip — both
        ends still agree clean."""
        from camp.transfer.worktree import escaping_members, extract_archive, write_archive

        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "inside-link").symlink_to("README.md")
        tip = _commit_all(repo, "add inside-link")

        peer = tmp_path / "peer"
        _worktree_at_tip(repo, peer, tip)

        assert escaping_members(repo, (), tip) == ()

        buf = io.BytesIO()
        write_archive(repo, (), buf)
        extract_archive(io.BytesIO(buf.getvalue()), peer)  # must not raise

    def test_unresolvable_tip_is_reported_unwalkable_not_clean(self, tmp_path: Path):
        """A git failure reading *tip* (here: not a git repository at all)
        must never read as clean."""
        from camp.transfer.worktree import escaping_members

        wt = tmp_path / "wt"
        wt.mkdir()

        assert escaping_members(wt, (), "deadbeef") is None


# ---------------------------------------------------------------------------
# Every replayed member type — directory and regular file, not only symlink
# and special — must go through the confinement gate before any filesystem
# mutation, and a mutation itself must never be able to reach outside the
# scratch root through a symlink an earlier replayed member left standing.
# ---------------------------------------------------------------------------


class TestEveryMemberTypeIsGatedBeforeMutation:
    def test_directory_replacing_a_tracked_escaping_symlink_never_writes_outside_it(
        self, tmp_path: Path
    ):
        """The tip tracks `sub` as a symlink escaping the repo; the sender's
        working tree has since replaced `sub` with a real directory holding
        a nested dangling symlink and an empty subdirectory. The seed step
        stands the tracked symlink up in the scratch root first, exactly as
        the peer's already-checked-out worktree would have it — replaying
        the worktree's real directory over that seeded symlink must never
        mkdir/unlink/symlink THROUGH it into the outside location the
        symlink points at."""
        from camp.transfer.worktree import escaping_members

        repo = tmp_path / "repo"
        init_git_repo(repo)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "nested_dir").mkdir()
        (outside / "nested_dir" / "victim").write_text("original\n")

        (repo / "sub").symlink_to(outside)
        tip = _commit_all(repo, "track sub as an escaping symlink")

        (repo / "sub").unlink()
        (repo / "sub").mkdir()
        (repo / "sub" / "nested_dir").mkdir()
        (repo / "sub" / "nested_dir" / "victim").symlink_to("/nowhere/attacker")
        (repo / "sub" / "newdir").mkdir()

        before_listing = sorted(p.name for p in outside.iterdir())

        result = escaping_members(repo, (), tip)

        assert result is not None
        assert len(result) >= 1

        after_listing = sorted(p.name for p in outside.iterdir())
        assert after_listing == before_listing

        victim = outside / "nested_dir" / "victim"
        assert not victim.is_symlink()
        assert victim.read_text() == "original\n"
        assert not (outside / "newdir").exists()

    def test_plain_directory_and_file_under_a_tracked_escaping_link_is_flagged_and_refused_by_a_real_peer(
        self, tmp_path: Path
    ):
        """A member type that was never gated before this fix — a plain
        directory, and the regular file inside it — replacing a tracked
        symlink that escapes the repo. The sender must flag it, and a real
        peer (a genuine `git worktree add` checkout at *tip*, then the real
        `extract_archive`) must refuse the archive `write_archive` produces."""
        from camp.transfer.worktree import ArchiveMemberEscaped, escaping_members, extract_archive, write_archive

        repo = tmp_path / "repo"
        init_git_repo(repo)
        outside_target = tmp_path / "outside-target"
        outside_target.mkdir()
        (repo / "sub").symlink_to(outside_target)
        tip = _commit_all(repo, "track sub as an escaping symlink")

        peer = tmp_path / "peer"
        _worktree_at_tip(repo, peer, tip)

        (repo / "sub").unlink()
        (repo / "sub").mkdir()
        (repo / "sub" / "file.txt").write_text("hi\n")

        result = escaping_members(repo, (), tip)

        assert result is not None
        assert len(result) >= 1

        buf = io.BytesIO()
        write_archive(repo, (), buf)
        with pytest.raises(ArchiveMemberEscaped):
            extract_archive(io.BytesIO(buf.getvalue()), peer)

    def test_directory_replacing_a_tracked_link_that_points_inside_the_root_agrees_with_the_peer(
        self, tmp_path: Path
    ):
        """The mirror of the escaping case: the tip tracks `sub -> other`
        where `other/` is itself tracked and stays inside the root. The
        sender's working tree has since replaced `sub` with a real
        directory. Sender and peer must agree — whichever way the peer
        actually resolves it — and nothing may be written outside the
        scratch root while the sender evaluates it."""
        from camp.transfer.worktree import ArchiveMemberEscaped, escaping_members, extract_archive, write_archive

        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "other").mkdir()
        (repo / "other" / "file.txt").write_text("hi\n")
        (repo / "sub").symlink_to("other")
        tip = _commit_all(repo, "track sub->other and other/")

        peer = tmp_path / "peer"
        _worktree_at_tip(repo, peer, tip)

        (repo / "sub").unlink()
        (repo / "sub").mkdir()
        (repo / "sub" / "newfile.txt").write_text("stuff\n")

        sender_verdict = escaping_members(repo, (), tip)
        assert sender_verdict is not None

        buf = io.BytesIO()
        write_archive(repo, (), buf)
        if sender_verdict == ():
            extract_archive(io.BytesIO(buf.getvalue()), peer)  # must not raise
        else:
            with pytest.raises(ArchiveMemberEscaped):
                extract_archive(io.BytesIO(buf.getvalue()), peer)

    def test_worktree_root_symlink_loop_is_reported_unwalkable_not_a_crash(
        self, tmp_path: Path
    ):
        """The worktree path itself resolving through a symlink loop must
        read as unwalkable, not crash the walk with an uncaught
        `RuntimeError` from `Path.resolve()`."""
        from camp.transfer.worktree import escaping_members

        (tmp_path / "loopA").symlink_to("loopB")
        (tmp_path / "loopB").symlink_to("loopA")

        result = escaping_members(tmp_path / "loopA", ())

        assert result is None
