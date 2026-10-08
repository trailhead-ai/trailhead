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


# ---------------------------------------------------------------- archive limits and permissions


def test_the_fetched_site_is_readable_by_its_owner_only(tmp_path, env, monkeypatch, capfd):
    dest = env / "gen"

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _serve_bytes(tmp_path, _good_tar()))

    assert code == 0 and doc["ok"] is True
    assert (os.stat(dest).st_mode & 0o777) == 0o700


def _extract_expecting(tmp_path, data: bytes, **caps):
    fetch = _fetch_module()
    archive = tmp_path / "a.tar"
    archive.write_bytes(data)
    into = tmp_path / "out"
    into.mkdir(exist_ok=True)
    try:
        fetch._extract(str(archive), str(into), **caps)
    except fetch._Failure as exc:
        return exc.reason, into
    return None, into


def _raw_header(name: bytes, typeflag: bytes, size: int, *, checksum_ok: bool = True, size_field: bytes | None = None) -> bytes:
    block = bytearray(512)
    block[0 : len(name)] = name
    block[100:108] = b"0000644\0"
    block[124:136] = size_field if size_field is not None else b"%011o\0" % size
    block[148:156] = b" " * 8
    block[156:157] = typeflag
    block[257:263] = b"ustar\0"
    total = sum(block) + (0 if checksum_ok else 1)
    block[148:156] = b"%06o\0 " % total
    return bytes(block)


def _padded(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 512)


_END = b"\0" * 1024


def _pax_record(key: str, value: str) -> bytes:
    body = f" {key}={value}\n".encode()
    length = len(body) + 1
    while len(str(length)) + len(body) != length:
        length = len(str(length)) + len(body)
    return str(length).encode() + body


def _x_header(*records: bytes, padding_records: bytes = b"") -> bytes:
    payload = b"".join(records)
    return _raw_header(b"PaxHeader", b"x", len(payload)) + _padded(payload + padding_records)


def _long_name(name: bytes) -> bytes:
    data = name + b"\0"
    return _raw_header(b"././@LongLink", b"L", len(data)) + _padded(data)


def _empty_file(name: bytes = b"f") -> bytes:
    return _raw_header(name, b"0", 0)


def _global_header_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": "g"}) as tar:
        info, data = _file("index.html", b"hi")
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_a_global_extended_header_is_refused(tmp_path):
    reason, into = _extract_expecting(tmp_path, _global_header_tar())

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def test_a_global_extended_header_is_refused_before_its_records_are_parsed(tmp_path):
    # A parse of this payload raises InvalidHeaderError, which the extractor reports as transfer-failed.
    garbage = b"this is not a pax record\n"
    raw = _raw_header(b"g", b"g", len(garbage)) + _padded(garbage) + _empty_file() + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def _long_path_tar(count: int = 1, length: int = 150) -> tuple[bytes, int]:
    """A tar of *count* files with long names (each gets an `x` header); returns it and one x header's data size."""
    members = [_file(f"{i}".ljust(length, "n"), b"hi") for i in range(count)]
    raw = _tar_bytes(members)
    assert raw[156:157] == b"x"
    return raw, int(raw[124:136].strip(b"\0 "), 8)


def test_an_extended_header_one_byte_over_the_cap_is_refused_and_at_the_cap_passes(tmp_path):
    raw, size = _long_path_tar()

    reason, into = _extract_expecting(tmp_path, raw, max_extended_header_bytes=size - 1)
    assert reason == "refused-archive"
    assert os.listdir(into) == []

    (tmp_path / "ok").mkdir()
    reason, into = _extract_expecting(tmp_path / "ok", raw, max_extended_header_bytes=size)
    assert reason is None
    assert os.listdir(into) == ["0".ljust(150, "n")]


def test_a_gnu_long_name_header_one_byte_over_the_cap_is_refused_and_at_the_cap_passes(tmp_path):
    raw = _long_name(b"n" * 200) + _empty_file() + _END

    reason, _ = _extract_expecting(tmp_path, raw, max_extended_header_bytes=200)
    assert reason == "refused-archive"

    (tmp_path / "ok").mkdir()
    reason, into = _extract_expecting(tmp_path / "ok", raw, max_extended_header_bytes=201)
    assert reason is None
    assert os.listdir(into) == ["n" * 200]


def test_a_gnu_long_link_header_over_the_cap_is_refused(tmp_path):
    data = b"n" * 200 + b"\0"
    raw = _raw_header(b"././@LongLink", b"K", len(data)) + _padded(data) + _empty_file() + _END

    reason, _ = _extract_expecting(tmp_path, raw, max_extended_header_bytes=200)

    assert reason == "refused-archive"


def test_total_extended_header_bytes_at_the_cap_extract_and_one_over_is_refused(tmp_path):
    raw, size = _long_path_tar(count=3)

    reason, into = _extract_expecting(tmp_path, raw, max_extended_total_bytes=3 * size - 1)
    assert reason == "refused-archive"
    assert os.listdir(into) == []

    (tmp_path / "ok").mkdir()
    reason, into = _extract_expecting(tmp_path / "ok", raw, max_extended_total_bytes=3 * size)
    assert reason is None
    assert len(os.listdir(into)) == 3


def test_a_chain_of_two_extension_headers_extracts_and_three_is_refused(tmp_path):
    two = _long_name(b"a" * 120) + _long_name(b"b" * 120) + _empty_file() + _END
    three = _long_name(b"a" * 120) + two

    reason, into = _extract_expecting(tmp_path, two)
    assert reason is None
    assert os.listdir(into) == ["a" * 120]

    (tmp_path / "over").mkdir()
    reason, into = _extract_expecting(tmp_path / "over", three)
    assert reason == "refused-archive"
    assert os.listdir(into) == []


def test_the_extension_chain_resets_after_each_member(tmp_path):
    raw = b"".join(_long_name(bytes([97 + i]) * 120) + _long_name(bytes([65 + i]) * 120) + _empty_file() for i in range(3)) + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason is None
    assert len(os.listdir(into)) == 3


def _n_files(n: int) -> bytes:
    return _tar_bytes([_file(f"f{i}", b"x") for i in range(n)])


def test_a_member_count_at_the_cap_extracts_and_one_over_is_refused(tmp_path):
    reason, into = _extract_expecting(tmp_path, _n_files(3), max_members=3)
    assert reason is None
    assert sorted(os.listdir(into)) == ["f0", "f1", "f2"]

    (tmp_path / "over").mkdir()
    reason, into = _extract_expecting(tmp_path / "over", _n_files(4), max_members=3)
    assert reason == "refused-archive"
    assert os.listdir(into) == []


def test_the_member_cap_refuses_at_the_first_member_over_without_reading_further(tmp_path, monkeypatch):
    read = []
    real = tarfile.TarInfo._proc_builtin

    def counting(self, tarfile_):
        read.append(self.name)
        return real(self, tarfile_)

    monkeypatch.setattr(tarfile.TarInfo, "_proc_builtin", counting)

    reason, _ = _extract_expecting(tmp_path, _n_files(50), max_members=3)

    assert reason == "refused-archive"
    assert read == ["f0", "f1", "f2"]


def test_directories_count_toward_the_member_cap(tmp_path):
    d = tarfile.TarInfo("sub")
    d.type = tarfile.DIRTYPE
    d.mode = 0o755
    raw = _tar_bytes([(d, None), _file("sub/a", b"x")])

    reason, _ = _extract_expecting(tmp_path, raw, max_members=1)

    assert reason == "refused-archive"


@pytest.mark.parametrize(
    "key", ["comment", "size", "linkpath", "uid", "gid", "uname", "gname", "atime", "hdrcharset", "GNU.sparse.realsize"]
)
def test_a_pax_key_outside_the_allowlist_is_refused(tmp_path, key):
    raw = _x_header(_pax_record(key, "1")) + _empty_file() + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def test_the_path_pax_key_is_allowed_and_names_the_member(tmp_path):
    raw = _x_header(_pax_record("path", "renamed.html")) + _raw_header(b"orig", b"0", 0) + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason is None
    assert os.listdir(into) == ["renamed.html"]


def test_a_pax_key_hidden_in_an_inner_header_of_a_chain_is_still_refused(tmp_path):
    # tarfile applies the inner header's attributes to the member and then overwrites its pax_headers
    # with the outer's, so a check on the finished member would never see the inner key.
    raw = _x_header(_pax_record("path", "a.html")) + _x_header(_pax_record("size", "0")) + _empty_file() + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def _vector1(hidden: bytes, blocks: int) -> bytes:
    # The size field starts with NUL: tarfile reads it as 0, a parser stripping NULs reads the digits.
    carrier = _raw_header(b"carrier", b"0", 0, size_field=b"\0" + b"%011o" % (512 * blocks))
    return carrier + hidden + _empty_file(b"index.html") + _END


def _vector2(hidden: bytes, blocks: int) -> bytes:
    # A NUL type with a trailing slash is a directory to tarfile (no data); its size field is not skipped.
    carrier = _raw_header(b"decoy/", b"\0", 512 * blocks)
    return carrier + hidden + _empty_file(b"index.html") + _END


def _vector3(hidden: bytes, blocks: int) -> bytes:
    # The size=0 record sits in the padding after the x header's declared data; tarfile parses the whole block.
    x = _x_header(_pax_record("path", "carrier"), padding_records=_pax_record("size", "0"))
    return x + _raw_header(b"c", b"0", 512 * blocks) + hidden + _empty_file(b"index.html") + _END


_VECTORS = [_vector1, _vector2, _vector3]
_G_HEADER = _raw_header(b"g", b"g", 0)


@pytest.mark.parametrize("vector", _VECTORS, ids=lambda f: f.__name__.strip("_"))
def test_a_global_header_hidden_behind_a_header_desync_is_still_refused(tmp_path, vector):
    reason, into = _extract_expecting(tmp_path, vector(_G_HEADER, 1))

    assert reason == "refused-archive"
    assert os.listdir(into) == []


@pytest.mark.parametrize("vector", _VECTORS, ids=lambda f: f.__name__.strip("_"))
def test_members_hidden_behind_a_header_desync_still_count_toward_the_cap(tmp_path, vector):
    hidden = b"".join(_empty_file(f"h{i}".encode()) for i in range(5))

    reason, into = _extract_expecting(tmp_path, vector(hidden, 5), max_members=3)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def test_the_same_desync_archives_with_nothing_hostile_hidden_extract(tmp_path):
    hidden = b"".join(_empty_file(f"h{i}".encode()) for i in range(2))
    for n, vector in enumerate((_vector1, _vector2)):
        (tmp_path / str(n)).mkdir()
        reason, into = _extract_expecting(tmp_path / str(n), vector(hidden, 2), max_members=10)
        assert reason is None
        assert {"h0", "h1", "index.html"} <= set(os.listdir(into))


def test_a_size_pax_record_in_block_padding_is_refused_even_with_nothing_hidden(tmp_path):
    raw = _vector3(b"", 0)

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def test_a_header_with_a_corrupted_checksum_is_transfer_failed(tmp_path):
    raw = _raw_header(b"f", b"0", 0, checksum_ok=False) + _END

    reason, _ = _extract_expecting(tmp_path, raw)

    assert reason == "transfer-failed"


def test_a_valid_checksum_on_the_same_header_is_accepted(tmp_path):
    reason, into = _extract_expecting(tmp_path, _raw_header(b"f", b"0", 0) + _END)

    assert reason is None
    assert os.listdir(into) == ["f"]


def test_a_malformed_pax_payload_that_tarfile_rejects_is_transfer_failed(tmp_path):
    garbage = b"this is not a pax record\n"
    raw = _raw_header(b"PaxHeader", b"x", len(garbage)) + _padded(garbage) + _empty_file() + _END

    reason, _ = _extract_expecting(tmp_path, raw)

    assert reason == "transfer-failed"


def test_a_size_field_that_runs_past_the_end_of_the_file_is_transfer_failed(tmp_path):
    raw = _raw_header(b"f", b"0", 5000) + b"x" * 512

    reason, _ = _extract_expecting(tmp_path, raw)

    assert reason == "transfer-failed"


def test_a_size_field_ending_exactly_at_the_end_of_the_data_blocks_is_not_a_truncation(tmp_path):
    raw = _raw_header(b"f", b"0", 600) + _padded(b"x" * 600) + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason is None
    assert (into / "f").stat().st_size == 600


def test_a_gnu_sparse_member_is_refused(tmp_path):
    reason, _ = _extract_expecting(tmp_path, _raw_header(b"f", b"S", 0) + _END)

    assert reason == "refused-archive"


def test_an_archive_missing_its_end_marker_is_transfer_failed(tmp_path):
    reason, _ = _extract_expecting(tmp_path, _raw_header(b"f", b"0", 0))

    assert reason == "transfer-failed"


def test_a_real_export_with_a_long_nested_path_a_non_ascii_name_and_a_large_file_round_trips(
    tmp_path, env, monkeypatch, capfd
):
    state = tmp_path / "remotestate"
    site = state / GROUP / "worktrees" / SLUG / "sites" / SITE
    deep = site.joinpath(*["d" * 40] * 5, "n" * 22)
    deep.mkdir(parents=True)
    assert len(str(deep.relative_to(site) / "x.txt")) == 233
    (deep / "x.txt").write_bytes(b"deep")
    (site / "index.html").write_bytes(b"i")
    (site / "café-日本.html").write_bytes(b"unicode")
    big = os.urandom(3_000_000)
    (site / "big.bin").write_bytes(big)
    dest = env / "gen"

    code, doc, _, _ = _run(monkeypatch, capfd, _argv(dest), _serve_real_export(state))

    assert code == 0 and doc["ok"] is True
    assert (dest / deep.relative_to(site) / "x.txt").read_bytes() == b"deep"
    assert (dest / "café-日本.html").read_bytes() == b"unicode"
    assert (dest / "big.bin").read_bytes() == big


def _sparse_x_header(*records: bytes) -> bytes:
    return _x_header(*records) + _raw_header(b"f", b"0", 512) + b"garbage!" * 64 + _END


@pytest.mark.parametrize(
    "records",
    [
        [("GNU.sparse.major", "1"), ("GNU.sparse.minor", "0")],
        [("GNU.sparse.map", "x,y")],
        [("GNU.sparse.size", "9"), ("GNU.sparse.offset", "x"), ("GNU.sparse.numbytes", "y")],
    ],
    ids=["sparse-1.0", "sparse-0.1", "sparse-0.0"],
)
def test_gnu_sparse_pax_headers_are_refused_before_tarfile_interprets_them(tmp_path, records):
    # Each shape sends tarfile into its sparse interpreter, which raises a non-tar error on this garbage.
    raw = _sparse_x_header(*(_pax_record(k, v) for k, v in records))

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def test_a_gnu_sparse_member_with_extension_blocks_is_refused_not_crashed(tmp_path):
    header = bytearray(_raw_header(b"f", b"S", 0))
    header[482] = 1
    header[148:156] = b"        "
    header[148:156] = b"%06o\0 " % sum(header)

    reason, _ = _extract_expecting(tmp_path, bytes(header))

    assert reason == "refused-archive"


def _negative_size_field(value: int) -> bytes:
    return b"\xff" + (256**11 + value).to_bytes(11, "big")


def test_a_regular_member_with_a_negative_base_256_size_is_refused_and_writes_nothing(tmp_path):
    raw = _raw_header(b"f", b"0", 0, size_field=_negative_size_field(-512)) + _empty_file(b"g") + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def _hook_member(monkeypatch, limits, *, size: int, offset_data: int, typeflag=tarfile.REGTYPE):
    """Run the real `_proc_member` hook with tarfile's own parse stubbed to hand back a chosen member.

    The stub stands in for an interpreter whose tarfile has no negative-size or
    offset guard, so the hook's own refusal is the only thing under test.
    """
    fetch = _fetch_module()

    def stub(self, tar):
        self.offset_data = offset_data
        return self

    monkeypatch.setattr(tarfile.TarInfo, "_proc_member", stub)
    info = fetch._limited_tarinfo(limits)("f")
    info.type = typeflag
    info.size = size
    return info._proc_member(None)


def _hook_limits(**over):
    fetch = _fetch_module()
    kwargs = dict(extended_header=8192, extended_total=1 << 20, chain=2, members=100, archive_size=10_000, complete=True)
    kwargs.update(over)
    return fetch._Limits(**kwargs)


@pytest.mark.parametrize("typeflag", [tarfile.REGTYPE, tarfile.DIRTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME])
def test_the_hook_refuses_a_negative_size_whatever_the_interpreter_does_with_it(monkeypatch, typeflag):
    fetch = _fetch_module()

    with pytest.raises(fetch._Failure) as exc:
        _hook_member(monkeypatch, _hook_limits(), size=-512, offset_data=1024, typeflag=typeflag)

    assert exc.value.reason == "refused-archive"


def test_the_hook_accepts_a_zero_size_member(monkeypatch):
    assert _hook_member(monkeypatch, _hook_limits(), size=0, offset_data=512).size == 0


def test_the_hook_refuses_data_that_starts_at_or_before_the_previous_members_data(monkeypatch):
    fetch = _fetch_module()
    limits = _hook_limits()
    _hook_member(monkeypatch, limits, size=100, offset_data=1024)

    with pytest.raises(fetch._Failure) as same:
        _hook_member(monkeypatch, limits, size=100, offset_data=1024)
    with pytest.raises(fetch._Failure) as back:
        _hook_member(monkeypatch, limits, size=100, offset_data=512)

    assert same.value.reason == back.value.reason == "refused-archive"


def test_the_hook_accepts_data_that_starts_one_byte_after_the_previous_members_data(monkeypatch):
    limits = _hook_limits()
    _hook_member(monkeypatch, limits, size=100, offset_data=1024)

    assert _hook_member(monkeypatch, limits, size=100, offset_data=1025).offset_data == 1025


def test_the_hook_refuses_a_regular_members_data_range_past_the_end_of_a_complete_archive(monkeypatch):
    fetch = _fetch_module()

    with pytest.raises(fetch._Failure) as exc:
        _hook_member(monkeypatch, _hook_limits(archive_size=10_000), size=9_000, offset_data=1_001)

    assert exc.value.reason == "refused-archive"


def test_the_hook_accepts_a_regular_members_data_range_ending_exactly_at_the_end_of_the_file(monkeypatch):
    assert _hook_member(monkeypatch, _hook_limits(archive_size=10_000), size=9_000, offset_data=1_000).size == 9_000


def test_a_complete_archive_whose_member_declares_more_data_than_the_file_holds_is_refused(tmp_path):
    raw = _raw_header(b"f", b"0", 5000) + b"x" * 512 + _END

    reason, into = _extract_expecting(tmp_path, raw)

    assert reason == "refused-archive"
    assert os.listdir(into) == []


def _export_round_trip(tmp_path: Path, mtime: int):
    export = importlib.import_module("camp.sites.export")
    fetch = _fetch_module()
    site = tmp_path / "state" / GROUP / "worktrees" / SLUG / "sites" / SITE
    site.mkdir(parents=True)
    page = site / "index.html"
    page.write_bytes(b"<h1>hi</h1>")
    os.utime(page, (mtime, mtime))
    buf = io.BytesIO()
    export.export_site(GROUP, SLUG, SITE, buf, env={"CAMP_STATE_DIR": str(tmp_path / "state"), "HOME": str(tmp_path)})
    archive = tmp_path / "a.tar"
    archive.write_bytes(buf.getvalue())
    into = tmp_path / "out"
    into.mkdir()
    fetch._extract(str(archive), str(into))
    return into


@pytest.mark.parametrize("mtime", [-86400 * 365, 9_000_000_000])
def test_an_export_of_a_file_with_an_extreme_mtime_round_trips_byte_identical(tmp_path, mtime):
    into = _export_round_trip(tmp_path, mtime)

    assert (into / "index.html").read_bytes() == b"<h1>hi</h1>"
