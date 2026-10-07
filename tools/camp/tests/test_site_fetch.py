"""`camp site-fetch` — pull one remote workspace site into a fresh local directory.

Every test drives the real `main()` entry point. The transport's spawner is the
only seam: it ignores the ssh argv and runs a local child instead, so the bytes
cross a real pipe through the real `fetch_camp_bytes`.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = "grp"
SLUG = "ws"
SITE = "docs"
HOST = "andromeda"


def _fetch_module():
    return importlib.import_module("camp.sites.fetch")


class Calls:
    """Records every spawn; `count` is how many times the transport ran."""

    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    @property
    def count(self) -> int:
        return len(self.argvs)


def _serve_bytes(tmp_path: Path, data: bytes, *, exit_code: int = 0, stderr: bytes = b"", calls: Calls | None = None):
    """A spawner whose child writes *data* to stdout, *stderr* to stderr, and exits."""
    blob = tmp_path / "served.bin"
    blob.write_bytes(data)
    err = tmp_path / "served.err"
    err.write_bytes(stderr)
    code = (
        "import sys;"
        "sys.stdout.buffer.write(open(sys.argv[1],'rb').read());sys.stdout.buffer.flush();"
        "sys.stderr.buffer.write(open(sys.argv[2],'rb').read());sys.stderr.buffer.flush();"
        f"sys.exit({exit_code})"
    )

    def spawner(argv, env):
        if calls is not None:
            calls.argvs.append(list(argv))
        return subprocess.Popen(
            [sys.executable, "-c", code, str(blob), str(err)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    return spawner


def _serve_hang(calls: Calls | None = None):
    def spawner(argv, env):
        if calls is not None:
            calls.argvs.append(list(argv))
        return subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    return spawner


def _serve_real_export(state_dir: Path, calls: Calls | None = None):
    """A spawner that runs camp's real `site-export` locally as the ssh payload."""

    def spawner(argv, env):
        remote = shlex.split(argv[-1])[1:]
        if calls is not None:
            calls.argvs.append(list(argv))
        child_env = {**os.environ, "CAMP_STATE_DIR": str(state_dir), "CAMP_CONFIG_DIR": str(state_dir / "cfg")}
        code = (
            "import sys;"
            f"sys.path.insert(0, {str(_PLUGIN_DIR)!r});"
            "from camp.cli import dispatch;"
            "sys.argv=['camp']+sys.argv[1:];"
            "dispatch.main()"
        )
        return subprocess.Popen(
            [sys.executable, "-c", code, *remote],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
        )

    return spawner


def _no_transport(calls: Calls):
    def spawner(argv, env):
        calls.argvs.append(list(argv))
        pytest.fail("the transport must not be called")

    return spawner


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Local camp config declaring the host `andromeda`; returns the parent dir for dests."""
    cfg = tmp_path / "localcfg"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text(f'[hosts.{HOST}]\nssh = "{HOST}"\n')
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "localstate"))
    parent = tmp_path / "dests"
    parent.mkdir()
    return parent


def _argv(dest: Path, **over) -> list[str]:
    vals = dict(
        **{"from": HOST},
        group=GROUP,
        slug=SLUG,
        site=SITE,
        dest=str(dest),
        timeout="8",
    )
    vals.update(over)
    return ["site-fetch", *(f"--{k}={v}" for k, v in vals.items()), "--json"]


def _run(monkeypatch, capfd, argv, spawner):
    """Run the real main() and return (exit_code, parsed stdout JSON, raw stdout, stderr)."""
    fetch = _fetch_module()
    monkeypatch.setattr(fetch, "_spawn", spawner)
    dispatch = importlib.import_module("camp.cli.dispatch")
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    cap = capfd.readouterr()
    out = cap.out
    return code, json.loads(out), out, cap.err


def _tar_bytes(members) -> bytes:
    """Build a tar from (TarInfo, payload-or-None) pairs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info, payload in members:
            tar.addfile(info, io.BytesIO(payload) if payload is not None else None)
    return buf.getvalue()


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.size = len(data)
    info.mode = 0o644
    return info, data


def _good_tar() -> bytes:
    d = tarfile.TarInfo("sub")
    d.type = tarfile.DIRTYPE
    d.mode = 0o755
    return _tar_bytes([_file("index.html", b"<h1>hi</h1>"), (d, None), _file("sub/a.bin", bytes(range(256)) * 100)])


def _assert_failure(code, doc, reason, parent: Path, keep: set[str] = frozenset()):
    assert code != 0
    assert doc["ok"] is False
    assert doc["reason"] == reason
    assert isinstance(doc["message"], str) and doc["message"]
    assert set(os.listdir(parent)) == set(keep), "dest or scratch left behind"


# ---------------------------------------------------------------- integration seam


def test_fetch_through_main_and_real_site_export_writes_the_site_byte_for_byte(tmp_path, env, monkeypatch, capfd):
    state = tmp_path / "remotestate"
    site = state / GROUP / "worktrees" / SLUG / "sites" / SITE
    (site / "a" / "b").mkdir(parents=True)
    (site / "index.html").write_bytes(b"<h1>hi</h1>\r\n")
    payload = bytes(range(256)) * 4000
    (site / "a" / "b" / "blob.bin").write_bytes(payload)
    (site / "empty").mkdir()
    dest = env / "gen1"
    calls = Calls()

    code, doc, raw, _ = _run(monkeypatch, capfd, _argv(dest), _serve_real_export(state, calls))

    assert code == 0
    assert doc == {"ok": True, "dest": str(dest)}
    assert raw.count("\n") == 1
    assert (dest / "index.html").read_bytes() == b"<h1>hi</h1>\r\n"
    assert (dest / "a" / "b" / "blob.bin").read_bytes() == payload
    assert (dest / "empty").is_dir()
    assert os.listdir(env) == ["gen1"]
    remote = shlex.split(calls.argvs[0][-1])
    assert remote[1:] == ["site-export", f"--group={GROUP}", f"--slug={SLUG}", f"--site={SITE}"]


def test_the_remote_sees_the_names_given_not_a_fixed_site(tmp_path, env, monkeypatch, capfd):
    state = tmp_path / "remotestate"
    site = state / "other-grp" / "worktrees" / "other-ws" / "sites" / "my.site"
    site.mkdir(parents=True)
    (site / "index.html").write_bytes(b"other")
    dest = env / "g"

    code, doc, _, _ = _run(
        monkeypatch, capfd, _argv(dest, group="other-grp", slug="other-ws", site="my.site"), _serve_real_export(state)
    )

    assert code == 0 and doc["ok"] is True
    assert (dest / "index.html").read_bytes() == b"other"


# ---------------------------------------------------------------- crafted archives


def _symlink():
    i = tarfile.TarInfo("link")
    i.type = tarfile.SYMTYPE
    i.linkname = "/etc/passwd"
    return _tar_bytes([_file("index.html", b"ok"), (i, None)])


def _hardlink():
    i = tarfile.TarInfo("hard")
    i.type = tarfile.LNKTYPE
    i.linkname = "index.html"
    return _tar_bytes([_file("index.html", b"ok"), (i, None)])


def _device():
    i = tarfile.TarInfo("dev")
    i.type = tarfile.CHRTYPE
    i.devmajor, i.devminor = 1, 3
    return _tar_bytes([_file("index.html", b"ok"), (i, None)])


def _fifo():
    i = tarfile.TarInfo("pipe")
    i.type = tarfile.FIFOTYPE
    return _tar_bytes([_file("index.html", b"ok"), (i, None)])


def _contiguous():
    i = tarfile.TarInfo("contig")
    i.type = tarfile.CONTTYPE
    i.size = 3
    return _tar_bytes([_file("index.html", b"ok"), (i, b"abc")])


def _sparse():
    i = tarfile.TarInfo("sparse")
    i.type = tarfile.GNUTYPE_SPARSE
    return _tar_bytes([_file("index.html", b"ok"), (i, None)])


def _pax_sparse():
    # A PAX 1.0 sparse member keeps the ordinary regular-file type flag; only its
    # GNU.sparse.* headers say it expands to a far larger file on extraction.
    map_block = b"0\n".ljust(512, b"\0")
    i = tarfile.TarInfo("GNU.sparseFile.0/big.bin")
    i.type = tarfile.REGTYPE
    i.mode = 0o644
    i.pax_headers = {
        "GNU.sparse.major": "1",
        "GNU.sparse.minor": "0",
        "GNU.sparse.name": "big.bin",
        "GNU.sparse.realsize": str(10 * 1024 * 1024),
    }
    i.size = len(map_block)
    return _tar_bytes([_file("index.html", b"ok"), (i, map_block)])


def _absolute(tmp_path):
    return _tar_bytes([_file("index.html", b"ok"), _file(str(tmp_path / "abs-escape.txt"), b"x")])


def _dotdot():
    return _tar_bytes([_file("index.html", b"ok"), _file("../escape.txt", b"x")])


def _dotdot_inside():
    return _tar_bytes([_file("index.html", b"ok"), _file("a/../b.txt", b"x")])


@pytest.mark.parametrize(
    "builder",
    [_symlink, _hardlink, _device, _fifo, _contiguous, _sparse, _pax_sparse, _absolute, _dotdot, _dotdot_inside],
    ids=lambda f: f.__name__.strip("_"),
)
def test_a_crafted_member_refuses_the_whole_archive_and_writes_nothing(tmp_path, env, monkeypatch, capfd, builder):
    dest = env / "gen"
    sentinel = tmp_path / "escape.txt"
    archive = builder(tmp_path) if builder is _absolute else builder()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, archive))

    _assert_failure(code, doc, "refused-archive", env)
    assert not dest.exists()
    assert not sentinel.exists()
    assert not (tmp_path / "abs-escape.txt").exists()


def _realsize_overlap(count: int = 20, claimed: int = 4000, data_len: int = 100) -> bytes:
    # Each member carries a lone GNU.sparse.realsize: tarfile adopts it as the
    # member size (type stays REGTYPE, issparse() False) while the next header's
    # offset still comes from the small ustar size, so the claimed data runs into
    # the members that follow.
    members = []
    for n in range(count):
        info, data = _file(f"f{n}.bin", bytes([n]) * data_len)
        if n < count - 3:
            info.pax_headers = {"GNU.sparse.realsize": str(claimed)}
        members.append((info, data))
    return _tar_bytes(members)


def _tree_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def test_overlapping_realsize_members_are_refused_and_nothing_is_written(tmp_path, env, monkeypatch, capfd):
    dest = env / "gen"
    archive = _realsize_overlap()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, archive))

    _assert_failure(code, doc, "refused-archive", env)
    assert not dest.exists()


def test_a_lone_gnu_sparse_realsize_header_refuses_even_when_it_equals_the_true_size(
    tmp_path, env, monkeypatch, capfd
):
    info, data = _file("index.html", b"x" * 100)
    info.pax_headers = {"GNU.sparse.realsize": "100"}
    dest = env / "gen"

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, _tar_bytes([(info, data)])))

    _assert_failure(code, doc, "refused-archive", env)
    assert not dest.exists()


def _two_files(sizes=(10, 6)) -> bytes:
    return _tar_bytes([_file(f"f{n}.bin", b"a" * size) for n, size in enumerate(sizes)])


def test_a_declared_total_exactly_at_the_ceiling_extracts(tmp_path):
    fetch = _fetch_module()
    archive = tmp_path / "a.tar"
    archive.write_bytes(_two_files((10, 6)))
    into = tmp_path / "out"
    into.mkdir()

    fetch._extract(str(archive), str(into), max_extracted_bytes=16)

    assert _tree_bytes(into) == 16


def test_a_declared_total_one_byte_over_the_ceiling_is_refused_and_writes_nothing(tmp_path):
    fetch = _fetch_module()
    archive = tmp_path / "a.tar"
    archive.write_bytes(_two_files((10, 6)))
    into = tmp_path / "out"
    into.mkdir()

    with pytest.raises(fetch._Failure) as exc:
        fetch._extract(str(archive), str(into), max_extracted_bytes=15)

    assert exc.value.reason == "refused-archive"
    assert os.listdir(into) == []


def _symlink_in_tree():
    i = tarfile.TarInfo("link")
    i.type = tarfile.SYMTYPE
    i.linkname = "index.html"
    return _tar_bytes([_file("index.html", b"ok"), (i, None)])


def _hardlink_in_tree():
    i = tarfile.TarInfo("hard")
    i.type = tarfile.LNKTYPE
    i.linkname = "index.html"
    return _tar_bytes([_file("index.html", b"ok"), (i, None)])


@pytest.mark.parametrize("builder", [_symlink_in_tree, _hardlink_in_tree], ids=lambda f: f.__name__.strip("_"))
def test_a_link_member_with_an_in_tree_target_is_refused_by_the_member_check_itself(
    tmp_path, env, monkeypatch, capfd, builder
):
    # tarfile's extraction filter accepts these, so only the member check can refuse them.
    dest = env / "gen"

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, builder()))

    _assert_failure(code, doc, "refused-archive", env)
    assert not dest.exists()


# ---------------------------------------------------------------- truncated streams


def test_a_stream_cut_mid_member_is_transfer_failed(tmp_path, env, monkeypatch, capfd):
    data = _good_tar()
    cut = data[: 512 + 512 + 300]  # first header, its data block, then a partial header
    assert len(cut) < len(data)

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, cut))

    _assert_failure(code, doc, "transfer-failed", env)


def test_a_stream_cut_inside_member_data_is_transfer_failed(tmp_path, env, monkeypatch, capfd):
    data = _good_tar()
    cut = data[: 1024 + 5000]

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, cut))

    _assert_failure(code, doc, "transfer-failed", env)


def test_a_stream_cut_exactly_between_members_is_transfer_failed(tmp_path, env, monkeypatch, capfd):
    full = _tar_bytes([_file("index.html", b"ok"), _file("two.html", b"2")])
    cut = full[:1024]  # index header + data block; second member and end marker missing

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, cut))

    _assert_failure(code, doc, "transfer-failed", env)


def test_an_exporter_that_dies_mid_stream_is_transfer_failed_even_with_a_valid_looking_tar(
    tmp_path, env, monkeypatch, capfd
):
    code, doc, _, _ = _run(
        monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, _good_tar(), exit_code=1, stderr=b"boom\n")
    )

    _assert_failure(code, doc, "transfer-failed", env)


def test_a_stream_that_is_not_a_tar_is_transfer_failed(tmp_path, env, monkeypatch, capfd):
    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, b"not a tar at all" * 100))

    _assert_failure(code, doc, "transfer-failed", env)


# ---------------------------------------------------------------- byte cap


def test_a_stream_over_the_byte_cap_is_archive_too_large(tmp_path, env, monkeypatch, capfd):
    fetch = _fetch_module()
    data = _good_tar()
    monkeypatch.setattr(fetch, "MAX_ARCHIVE_BYTES", len(data) - 1)

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, data))

    _assert_failure(code, doc, "archive-too-large", env)


def test_a_stream_exactly_at_the_byte_cap_is_accepted(tmp_path, env, monkeypatch, capfd):
    fetch = _fetch_module()
    data = _good_tar()
    monkeypatch.setattr(fetch, "MAX_ARCHIVE_BYTES", len(data))

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, data))

    assert code == 0 and doc["ok"] is True


# ---------------------------------------------------------------- transport outcomes


@pytest.mark.parametrize(
    ("exit_code", "stderr", "reason"),
    [
        (255, b"ssh: Could not resolve hostname andromeda\n", "unreachable"),
        (127, b"bash: camp: command not found\n", "camp-unavailable-remote"),
        (4, b"camp site-export: site not found\n", "not-found"),
        (255, b"user@andromeda: Permission denied (publickey).\n", "auth-refused"),
        (255, b"No ED25519 host key is known for andromeda and you have requested strict checking.\n", "auth-refused"),
        (255, b"WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n", "auth-refused"),
        (
            1,
            b"camp: bare slug dispatch is no longer supported.\n  Use 'camp new site-export' to create or enter a workspace.\n",
            "remote-camp-outdated",
        ),
        (1, b"camp site-export: something else went wrong\n", "transfer-failed"),
        (255, b"ssh: something unclassified\n", "transfer-failed"),
    ],
    ids=[
        "unreachable",
        "camp-unavailable-remote",
        "not-found",
        "auth-refused-permission",
        "auth-refused-identity-unknown",
        "auth-refused-identity-changed",
        "remote-camp-outdated",
        "transfer-failed-other-refusal",
        "transfer-failed-unclassified-255",
    ],
)
def test_a_transport_outcome_maps_to_its_reason_code(tmp_path, env, monkeypatch, capfd, exit_code, stderr, reason):
    code, doc, _, _ = _run(
        monkeypatch, capfd, _argv(env / "gen"), _serve_bytes(tmp_path, b"", exit_code=exit_code, stderr=stderr)
    )

    _assert_failure(code, doc, reason, env)


def test_a_remote_that_stops_responding_is_timed_out_and_the_given_timeout_is_used(tmp_path, env, monkeypatch, capfd):
    import time

    started = time.monotonic()
    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen", timeout="0.5"), _serve_hang())

    _assert_failure(code, doc, "timed-out", env)
    assert time.monotonic() - started < 10


def test_the_failure_message_never_carries_remote_text(tmp_path, env, monkeypatch, capfd):
    code, doc, _, _ = _run(
        monkeypatch,
        capfd,
        _argv(env / "gen"),
        _serve_bytes(tmp_path, b"", exit_code=1, stderr=b"SECRET-REMOTE-DETAIL\n"),
    )

    assert doc["reason"] == "transfer-failed"
    assert "SECRET-REMOTE-DETAIL" not in doc["message"]


# ---------------------------------------------------------------- pre-transport refusals


def test_an_undeclared_host_is_refused_without_a_transport_call(tmp_path, env, monkeypatch, capfd):
    calls = Calls()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen", **{"from": "nowhere"}), _no_transport(calls))

    _assert_failure(code, doc, "host-not-declared", env)
    assert calls.count == 0


def test_a_missing_hosts_file_is_host_not_declared(tmp_path, env, monkeypatch, capfd):
    (tmp_path / "localcfg" / "hosts.toml").unlink()
    calls = Calls()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _no_transport(calls))

    _assert_failure(code, doc, "host-not-declared", env)
    assert calls.count == 0


def test_an_unreadable_hosts_file_is_host_not_declared(tmp_path, env, monkeypatch, capfd):
    (tmp_path / "localcfg" / "hosts.toml").write_text("[hosts.andromeda\nssh = \n")
    calls = Calls()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _no_transport(calls))

    _assert_failure(code, doc, "host-not-declared", env)
    assert calls.count == 0


@pytest.mark.parametrize("flag", ["from", "group", "slug", "site", "dest", "timeout"])
def test_an_abbreviated_option_is_a_usage_error_not_a_match(tmp_path, env, monkeypatch, capfd, flag):
    calls = Calls()
    argv = [a.replace(f"--{flag}=", f"--{flag[:-1]}=", 1) for a in _argv(env / "gen")]

    code, doc, raw, _ = _run(monkeypatch, capfd, argv, _no_transport(calls))

    _assert_failure(code, doc, "invalid-name", env)
    assert calls.count == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("from", "andromeda\n"),
        ("from", "Andromeda"),
        ("from", "-oProxyCommand=x"),
        ("group", "grp\n"),
        ("group", "Grp"),
        ("group", ""),
        ("slug", "ws\n"),
        ("slug", "w/s"),
        ("slug", ".."),
        ("site", "docs\n"),
        ("site", ".hidden"),
        ("site", "../x"),
        ("site", ""),
        ("timeout", "abc"),
        ("timeout", "0"),
        ("timeout", "-1"),
        ("timeout", "nan"),
    ],
)
def test_an_invalid_name_is_refused_without_a_transport_call(tmp_path, env, monkeypatch, capfd, field, value):
    calls = Calls()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen", **{field: value}), _no_transport(calls))

    _assert_failure(code, doc, "invalid-name", env)
    assert calls.count == 0


@pytest.mark.parametrize(
    "argv_tail",
    [
        [],  # everything missing
        ["--from=andromeda", "--group=grp", "--slug=ws", "--site=docs"],  # no dest or timeout
        ["--bogus=1"],
        ["--from"],  # option without value
    ],
    ids=["nothing", "no-dest-no-timeout", "unknown-flag", "valueless-option"],
)
def test_argparse_usage_errors_come_out_as_one_json_object(tmp_path, env, monkeypatch, capfd, argv_tail):
    calls = Calls()

    code, doc, raw, _ = _run(monkeypatch, capfd, ["site-fetch", *argv_tail], _no_transport(calls))

    assert code != 0
    assert doc["ok"] is False and doc["reason"] == "invalid-name"
    assert raw.count("\n") == 1
    assert calls.count == 0


def test_help_flag_does_not_print_usage_on_stdout(tmp_path, env, monkeypatch, capfd):
    code, doc, raw, _ = _run(monkeypatch, capfd, ["site-fetch", "--help"], _no_transport(Calls()))

    assert code != 0 and doc["reason"] == "invalid-name"


def test_an_existing_dest_is_refused_and_left_untouched(tmp_path, env, monkeypatch, capfd):
    dest = env / "gen"
    dest.mkdir()
    (dest / "keep.txt").write_bytes(b"mine")
    calls = Calls()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _no_transport(calls))

    _assert_failure(code, doc, "dest-exists", env, keep={"gen"})
    assert (dest / "keep.txt").read_bytes() == b"mine"
    assert calls.count == 0


def test_an_existing_file_or_dangling_symlink_at_dest_is_dest_exists(tmp_path, env, monkeypatch, capfd):
    os.symlink(tmp_path / "nowhere", env / "gen")
    calls = Calls()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "gen"), _no_transport(calls))

    _assert_failure(code, doc, "dest-exists", env, keep={"gen"})
    assert calls.count == 0


def test_a_dest_whose_parent_does_not_exist_is_refused_and_nothing_is_created(tmp_path, env, monkeypatch, capfd):
    calls = Calls()

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(env / "missing" / "gen"), _no_transport(calls))

    _assert_failure(code, doc, "invalid-name", env)
    assert calls.count == 0


def test_the_host_flag_is_refused_and_stdout_stays_empty(tmp_path, env, monkeypatch, capfd):
    fetch = _fetch_module()
    monkeypatch.setattr(fetch, "_spawn", _no_transport(Calls()))
    dispatch = importlib.import_module("camp.cli.dispatch")
    monkeypatch.setattr(sys, "argv", ["camp", *_argv(env / "gen")[:1], "--host=other", *_argv(env / "gen")[1:]])

    with pytest.raises(SystemExit) as e:
        dispatch.main()

    cap = capfd.readouterr()
    assert e.value.code not in (0, None)
    assert cap.out == ""
    assert "--host has no meaning here" in cap.err
    assert os.listdir(env) == []


# ---------------------------------------------------------------- routing


@pytest.mark.parametrize(("token", "is_verb"), [("site-fetch", True), ("site-fetchs", False)])
def test_only_the_exact_site_fetch_token_is_dispatched_as_the_verb(tmp_path, env, monkeypatch, capfd, token, is_verb):
    dispatch = importlib.import_module("camp.cli.dispatch")
    monkeypatch.setattr(sys, "argv", ["camp", token])

    with pytest.raises(SystemExit) as e:
        dispatch.main()

    cap = capfd.readouterr()
    assert e.value.code == 1
    assert ("bare slug dispatch" not in cap.err) is is_verb
    assert (cap.out.startswith("{") and json.loads(cap.out)["reason"] == "invalid-name") is is_verb


# ---------------------------------------------------------------- filesystem


def test_the_rename_succeeds_when_dest_is_on_a_different_filesystem_from_the_system_temp_dir(
    tmp_path, env, monkeypatch, capfd
):
    here = Path(__file__).resolve().parent
    parent = Path(tempfile.mkdtemp(dir=here))
    try:
        if os.stat(parent).st_dev == os.stat(tempfile.gettempdir()).st_dev:
            pytest.skip("the worktree shares a filesystem with the system temp dir")
        monkeypatch.setenv("TMPDIR", tempfile.gettempdir())
        tempfile.tempdir = None
        dest = parent / "gen"

        code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, _good_tar()))

        assert code == 0 and doc == {"ok": True, "dest": str(dest)}
        assert (dest / "sub" / "a.bin").read_bytes() == bytes(range(256)) * 100
        assert os.listdir(parent) == ["gen"]
    finally:
        shutil.rmtree(parent, ignore_errors=True)
        tempfile.tempdir = None


# ---------------------------------------------------------------- catch-all and scratch lifecycle


def _run_capturing_escape(monkeypatch, capfd, argv, spawner):
    """Run the real main(); return (escaped exception or None, exit code, raw stdout)."""
    fetch = _fetch_module()
    monkeypatch.setattr(fetch, "_spawn", spawner)
    dispatch = importlib.import_module("camp.cli.dispatch")
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    escaped = None
    code = 0
    try:
        dispatch.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:  # noqa: BLE001 - recorded so the test can assert it never escapes
        escaped = exc
    return escaped, code, capfd.readouterr().out


def test_an_unexpected_exception_still_yields_one_transfer_failed_json_object(tmp_path, env, monkeypatch, capfd):
    fetch = _fetch_module()
    dest = env / "gen"

    def boom(archive, into):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(fetch, "_extract", boom)

    escaped, code, out = _run_capturing_escape(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, _good_tar()))

    assert escaped is None, "the exception escaped run_cli"
    doc = json.loads(out)
    assert doc["ok"] is False
    assert doc["reason"] == "transfer-failed"
    assert code != 0
    assert not dest.exists()
    assert os.listdir(env) == []


@pytest.mark.parametrize("failing", ["mkdtemp", "mkstemp"])
def test_a_failure_creating_scratch_leaves_no_scratch_behind(tmp_path, env, monkeypatch, capfd, failing):
    fetch = _fetch_module()
    dest = env / "gen"

    def boom(*args, **kwargs):
        raise OSError("no space")

    monkeypatch.setattr(fetch.tempfile, failing, boom)

    escaped, code, out = _run_capturing_escape(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, _good_tar()))

    assert escaped is None
    doc = json.loads(out)
    assert doc["ok"] is False
    assert doc["reason"] == "transfer-failed"
    assert code != 0
    assert os.listdir(env) == [], "scratch left behind"
