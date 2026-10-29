"""Tests for camp.env.attachment — an attachment found again from the process table.

Test contract:
- Recognition against a fake ps table, each decoy rejected, the real K2 ps line
  recognised, malformed rows skipped, ps failures raising Unverifiable.
- end(): SIGTERM first, SIGKILL after the grace, and nothing signalled once a pid
  is no longer a recognised attachment with the same id (real child processes).
- port_holder(): free, held on 127.0.0.1, held only on ::1, lsof absent, escape
  sequences removed, an unavailable ::1 counting as free.
- tag_for() and the namespace override refuse what camp's identifier rule refuses.
"""
from __future__ import annotations

import errno
import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.env import attachment  # noqa: E402
from camp.env.attachment import Attachment, Unverifiable, Untaggable  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_UID = os.getuid()
_NS = "camp-env-test"
_TAG = f"{_NS}:abc123:perseus/grp/main"
_GOOD_CMD = (
    f"ssh -o BatchMode=yes -f -N -o Tag={_TAG} "
    "-L 127.0.0.1:5173:127.0.0.1:41990 -L 127.0.0.1:80:127.0.0.1:41991 -- user@perseus"
)


@pytest.fixture(autouse=True)
def _namespace(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CAMP_ENV_TAG_NAMESPACE", _NS)
    return _NS


def _row(pid: int, command: str, uid: int = _UID) -> str:
    return f"{pid:>7} {uid:>5} {command}"


def _table(*rows: str):
    text = "\n".join(rows) + "\n"
    return lambda argv: text


def _good(pid: int = 100) -> Attachment:
    return Attachment(
        pid=pid, host="perseus", group="grp", name="main",
        ports=((80, 41991), (5173, 41990)), attach_id="abc123",
    )


# --- recognition -----------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        _GOOD_CMD,
        _GOOD_CMD.replace("ssh ", "/usr/bin/ssh ", 1),
        _GOOD_CMD.replace("-o Tag=", "-oTag="),
        _GOOD_CMD.replace("-L 127.0.0.1:5173", "-L127.0.0.1:5173"),
        _GOOD_CMD.replace("-f -N", "-fN"),
        _GOOD_CMD.replace("-o BatchMode=yes", "-p22 -l bob -i /k/id -oBatchMode=yes"),
        _GOOD_CMD.replace(" -- ", " "),
        _GOOD_CMD.replace("-f -N", "-fNT"),
        _GOOD_CMD.replace("-f -N", "-T -N -f"),
        _GOOD_CMD.replace("-f -N", "-12N"),
    ],
)
def test_recognises_an_ssh_forward_with_one_tag_option(command: str) -> None:
    found = attachment.attachments(runner=_table(_row(100, command)))
    assert found == [_good(100)]


def test_ports_are_ordered_by_standard_port() -> None:
    command = _GOOD_CMD.replace("5173", "9000")
    found = attachment.attachments(runner=_table(_row(100, command)))
    assert found[0].ports == ((80, 41991), (9000, 41990))


# N-after-destination and options-after-destination are unrecognised by design: OpenSSH resumes
# option parsing after the destination, camp's recogniser stops there (stricter), and camp's own
# forwards always carry `--`, so they are unaffected.
@pytest.mark.parametrize(
    "decoy_row",
    [
        _row(200, _GOOD_CMD, uid=_UID + 1),
        _row(200, f"grep {_TAG}"),
        _row(200, _GOOD_CMD.replace("ssh ", "grep ", 1)),
        _row(200, f"ssh -N user@perseus echo Tag={_TAG}"),
        _row(200, _GOOD_CMD.replace("-N ", "")),
        _row(200, f"ssh -N -- user@perseus echo Tag={_TAG}"),
        _row(200, f"ssh -N -- user@perseus -o Tag={_TAG}"),
        _row(200, _GOOD_CMD.replace("-f -N", f"-f -N -o Tag={_NS}:zzz:perseus/grp/main")),
        _row(200, _GOOD_CMD.replace(_NS, "camp-env-other")),
        _row(200, _GOOD_CMD.replace("perseus/grp/main", "perseus/Grp/main")),
        _row(200, _GOOD_CMD.replace("perseus/grp/main", "-perseus/grp/main")),
        _row(200, _GOOD_CMD.replace("perseus/grp/main", "perseus/grp")),
        _row(200, _GOOD_CMD.replace("5173", "٣١٧٣")),
        _row(200, _GOOD_CMD.replace("ssh ", "sshd ", 1)),
        _row(200, f"ssh tduffield@perseus pgrep -N -o Tag={_TAG} -L 127.0.0.1:5173:127.0.0.1:41990"),
        _row(200, f"ssh -l -o Tag={_TAG} -N -L 127.0.0.1:5173:127.0.0.1:41990 box"),
        _row(200, f"ssh -o Tag={_TAG} -L 127.0.0.1:5173:127.0.0.1:41990 box -N"),
        _row(200, f"ssh -N box -o Tag={_TAG} -L 127.0.0.1:5173:127.0.0.1:41990"),
        _row(200, f"ssh -f -N -o Tag={_TAG} -- user@perseus"),
        _row(200, f"ssh -f -N -o Tag={_TAG} -Z -L 127.0.0.1:5173:127.0.0.1:41990 -- user@perseus"),
        _row(200, f"ssh -f -N -o Tag={_TAG} -L"),
        _row(200, _GOOD_CMD.replace(f"{_NS}:abc123:perseus/grp/main", f"{_NS}:abc123:Perseus/grp/main")),
        _row(200, _GOOD_CMD.replace(f"{_NS}:abc123:perseus/grp/main", f"{_NS}:abc123:perseus.local/grp/main")),
    ],
    ids=[
        "other-uid", "grep-argv0", "grep-wearing-ssh-flags", "tag-word-in-remote-command-no-dashdash", "no-dash-N", "tag-after-dashdash-in-remote-command",
        "o-tag-after-dashdash", "two-tag-options", "other-namespace",
        "bad-group", "bad-host", "missing-name", "non-ascii-port", "argv0-sshd",
        "tag-and-N-in-remote-command", "o-is-the-value-of-l", "N-after-destination-unrecognised-by-design-stricter-than-openssh",
        "options-after-destination-unrecognised-by-design-stricter-than-openssh", "no-L", "unknown-option-letter", "L-without-a-value",
        "uppercase-host", "dotted-host",
    ],
)
def test_a_decoy_is_not_recognised_and_the_others_still_parse(decoy_row: str) -> None:
    found = attachment.attachments(runner=_table(decoy_row, _row(100, _GOOD_CMD)))
    assert found == [_good(100)]


@pytest.mark.parametrize(
    "standard, published",
    [
        ("9" * 5000, "41990"),
        ("99999", "41990"),
        ("5173", "70000"),
        ("65536", "41990"),
        ("0", "41990"),
        ("5173", "0"),
        ("05173", "41990"),
        ("5173", "041990"),
        ("1" * 6, "41990"),
    ],
    ids=["5000-digits", "above-65535", "published-above-65535", "65536", "zero", "published-zero",
         "leading-zero", "published-leading-zero", "six-digits"],
)
def test_an_L_whose_ports_are_not_1_to_65535_in_five_digits_is_not_recognised_and_the_others_still_parse(
    standard: str, published: str
) -> None:
    absurd = _GOOD_CMD.replace("127.0.0.1:5173:127.0.0.1:41990", f"127.0.0.1:{standard}:127.0.0.1:{published}")
    found = attachment.attachments(runner=_table(_row(200, absurd), _row(100, _GOOD_CMD)))
    assert found == [_good(100)]


@pytest.mark.parametrize("standard, published", [("1", "65535"), ("65535", "1"), ("80", "41991")])
def test_an_L_with_ports_at_the_edges_of_the_range_is_recognised(standard: str, published: str) -> None:
    command = f"ssh -N -o Tag={_TAG} -L 127.0.0.1:{standard}:127.0.0.1:{published} -- dest"
    found = attachment.attachments(runner=_table(_row(100, command)))
    assert [a.ports for a in found] == [((int(standard), int(published)),)]


@pytest.mark.parametrize("port", [70000, 65536, -1, 10**6])
def test_a_port_outside_the_range_is_reported_as_could_not_be_checked_not_a_traceback(port: int) -> None:
    holder = attachment.port_holder(port, lsof_runner=lambda argv: "")
    assert holder is not None and "could not be checked" in holder


def test_the_real_k2_forward_line_is_recognised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMP_ENV_TAG_NAMESPACE", "camp-env-k2probe")
    text = (_FIXTURES / "env_forward_ps_linux_openssh_9_6.txt").read_text()
    uid = int(text.split()[1])
    found = attachment.attachments(runner=lambda argv: text, uid=uid)
    assert found == [
        Attachment(
            pid=1518325, host="perseus", group="grp", name="name",
            ports=((41875, 41990),), attach_id="fetch",
        )
    ]


@pytest.mark.parametrize("junk", ["", "   ", "123", "123 1000", "abc 1000 ssh -N", "123 xyz ssh -N"])
def test_a_row_that_does_not_split_into_pid_uid_command_is_skipped(junk: str) -> None:
    found = attachment.attachments(runner=_table(junk, _row(100, _GOOD_CMD)))
    assert found == [_good(100)]


def test_ps_is_asked_for_pid_uid_and_the_whole_command() -> None:
    seen: list[list[str]] = []

    def runner(argv):
        seen.append(argv)
        return ""

    assert attachment.attachments(runner=runner) == []
    assert seen == [["ps", "-axwwo", "pid=,uid=,command="]]


def _path_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ps_body: str | None) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if ps_body is not None:
        script = bindir / "ps"
        script.write_text(f"#!{sys.executable}\nimport sys, time\n" + ps_body + "\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir))


def test_ps_missing_raises_unverifiable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path_with(tmp_path, monkeypatch, None)
    with pytest.raises(Unverifiable) as raised:
        attachment.attachments()
    assert str(raised.value)


def test_ps_exiting_non_zero_raises_unverifiable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path_with(tmp_path, monkeypatch, "sys.stderr.write('broken\\n'); sys.exit(3)")
    with pytest.raises(Unverifiable) as raised:
        attachment.attachments()
    assert "broken" in str(raised.value) or "3" in str(raised.value)


def test_ps_timing_out_raises_unverifiable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path_with(tmp_path, monkeypatch, "time.sleep(5)")
    started = time.monotonic()
    with pytest.raises(Unverifiable) as raised:
        attachment.attachments(timeout=0.3)
    assert time.monotonic() - started < 3
    assert "did not answer" in str(raised.value)


def test_a_runner_that_cannot_run_raises_unverifiable() -> None:
    def runner(argv):
        raise OSError("no ps here")

    with pytest.raises(Unverifiable):
        attachment.attachments(runner=runner)


def test_a_working_ps_lists_the_real_table_without_error() -> None:  # inert-gate: allow real ps parses on this platform
    assert isinstance(attachment.attachments(), list)


# --- the tag ---------------------------------------------------------------


def test_tag_for_roundtrips_through_recognition() -> None:
    tag = attachment.tag_for("perseus", "grp", "main", "abc123")
    assert tag == f"{_NS}:abc123:perseus/grp/main"
    row = _row(7, f"ssh -N -o Tag={tag} -L 127.0.0.1:81:127.0.0.1:9 -- h")
    found = attachment.attachments(runner=_table(row))
    assert [(a.host, a.group, a.name, a.attach_id) for a in found] == [("perseus", "grp", "main", "abc123")]


@pytest.mark.parametrize("host", ["Perseus", "perseus.local", "-perseus", "per seus", ""])
def test_tag_for_refuses_a_host_outside_the_identifier_rule(host: str) -> None:
    with pytest.raises(Untaggable):
        attachment.tag_for(host, "grp", "main", "abc123")


def test_an_undecodable_row_from_another_process_does_not_hide_the_real_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = _row(100, _GOOD_CMD, uid=os.getuid())
    _path_with(
        tmp_path,
        monkeypatch,
        "sys.stdout.buffer.write(b'  900  0 /bin/odd \\xff\\xfe arg\\n' + " + repr(good.encode()) + " + b'\\n')",
    )
    assert attachment.attachments(uid=os.getuid()) == [_good(100)]


def test_default_namespace_is_camp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAMP_ENV_TAG_NAMESPACE")
    assert attachment.tag_for("h", "g", "n", "i1") == "camp-env:i1:h/g/n"


def test_namespace_is_read_on_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    first = attachment.tag_for("h", "g", "n", "i1")
    monkeypatch.setenv("CAMP_ENV_TAG_NAMESPACE", "other-ns")
    assert first.startswith(_NS + ":")
    assert attachment.tag_for("h", "g", "n", "i1").startswith("other-ns:")


@pytest.mark.parametrize("namespace", ["has space", "a:b", "", "-lead", "UPPER", "tab\t"])
def test_a_bad_namespace_is_refused(namespace: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMP_ENV_TAG_NAMESPACE", namespace)
    with pytest.raises(Untaggable):
        attachment.tag_for("h", "g", "n", "i1")
    with pytest.raises(Untaggable):
        attachment.attachments(runner=_table())


@pytest.mark.parametrize("host", ["bad host", "-leading", "a/b", "a:b", "", "h\n"])
def test_a_host_that_cannot_be_tagged_is_refused(host: str) -> None:
    with pytest.raises(Untaggable):
        attachment.tag_for(host, "g", "n", "i1")


def test_a_host_with_dashes_can_be_tagged() -> None:
    assert attachment.tag_for("my-box", "g", "n", "i1").endswith(":my-box/g/n")


@pytest.mark.parametrize("group,name,attach_id", [("G", "n", "i"), ("g", "n n", "i"), ("g", "n", "a:b"), ("g", "n", "")])
def test_a_bad_group_name_or_id_is_refused(group: str, name: str, attach_id: str) -> None:
    with pytest.raises(Untaggable):
        attachment.tag_for("h", group, name, attach_id)


# --- end -------------------------------------------------------------------

_CHILD = """
import os, signal, sys, time
mode = os.environ["CHILD_MODE"]
marker = os.environ.get("CHILD_MARKER")
if mode == "ignore":
    def _term(signum, frame):
        if marker:
            open(marker, "w").close()
    signal.signal(signal.SIGTERM, _term)
print("ready", flush=True)
while True:
    time.sleep(0.02)
"""


class _Child:
    def __init__(self, tmp_path: Path, mode: str, attach_id: str) -> None:
        self.marker = tmp_path / f"{attach_id}.marker"
        self.attach_id = attach_id
        script = tmp_path / "child.py"
        script.write_text(_CHILD)
        self.port = 40000 + os.getpid() % 1000
        tag = f"{os.environ['CAMP_ENV_TAG_NAMESPACE']}:{attach_id}:perseus/grp/main"
        env = {**os.environ, "CHILD_MODE": mode, "CHILD_MARKER": str(self.marker), "CHILD_SCRIPT": str(script)}
        self.proc = subprocess.Popen(
            ["ssh", "-c", "__import__('runpy').run_path(__import__('os').environ['CHILD_SCRIPT'])", "-N", "-o", f"Tag={tag}",
             "-L", f"127.0.0.1:{self.port}:127.0.0.1:{self.port + 1}", "--", "dest"],
            executable=sys.executable, env=env, stdout=subprocess.PIPE, text=True,
        )
        assert self.proc.stdout.readline().strip() == "ready"
        self.reaper = threading.Thread(target=self.proc.wait, daemon=True)
        self.reaper.start()

    @property
    def attachment(self) -> Attachment:
        return Attachment(
            pid=self.proc.pid, host="perseus", group="grp", name="main",
            ports=((self.port, self.port + 1),), attach_id=self.attach_id,
        )

    def row(self, command: str | None = None, attach_id: str | None = None) -> str:
        tag = f"{os.environ['CAMP_ENV_TAG_NAMESPACE']}:{attach_id or self.attach_id}:perseus/grp/main"
        return _row(self.proc.pid, command or f"ssh -N -o Tag={tag} -L 127.0.0.1:{self.port}:127.0.0.1:{self.port + 1} -- dest")


@pytest.fixture
def child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAMP_ENV_TAG_NAMESPACE", f"camp-env-t{uuid.uuid4().hex[:10]}")
    started: list[_Child] = []

    def start(mode: str, directory: str = "") -> _Child:
        root = tmp_path / directory
        root.mkdir(exist_ok=True)
        made = _Child(root, mode, uuid.uuid4().hex[:8])
        started.append(made)
        return made

    yield start
    for made in started:
        if made.proc.poll() is None:
            made.proc.kill()
        made.proc.wait()
        made.proc.stdout.close()


def test_a_child_whose_script_lives_under_a_directory_with_a_space_is_still_found(child) -> None:
    made = child("term", "a directory with spaces")
    assert made.attachment in attachment.attachments()


def test_the_real_table_finds_a_real_child_under_the_test_namespace(child) -> None:
    made = child("term")
    assert made.attachment in attachment.attachments()


def test_end_stops_a_forward_that_exits_on_sigterm_without_sigkill(child) -> None:
    made = child("term")
    assert attachment.end([made.attachment], grace=5.0) == []
    made.reaper.join(5)
    assert made.proc.returncode == -signal.SIGTERM


def test_end_kills_a_forward_that_ignores_sigterm_after_the_grace(child) -> None:
    made = child("ignore")
    started = time.monotonic()
    assert attachment.end([made.attachment], grace=0.4) == []
    made.reaper.join(5)
    assert made.proc.returncode == -signal.SIGKILL
    assert time.monotonic() - started >= 0.4
    assert made.marker.exists()


def test_end_reports_a_pid_it_could_not_end(child, monkeypatch: pytest.MonkeyPatch) -> None:
    made = child("ignore")
    real_kill = os.kill

    def kill(pid, sig):
        if sig == signal.SIGKILL and pid == made.proc.pid:
            return
        real_kill(pid, sig)

    monkeypatch.setattr(attachment, "os", types.SimpleNamespace(**{**vars(os), "kill": kill}))
    assert attachment.end([made.attachment], grace=0.2) == [made.proc.pid]


def test_end_with_nothing_to_end_returns_nothing() -> None:
    assert attachment.end([], runner=_table()) == []


def test_end_signals_nothing_when_the_table_cannot_be_read(child) -> None:
    made = child("term")

    def runner(argv):
        raise OSError("no ps here")

    with pytest.raises(Unverifiable):
        attachment.end([made.attachment], runner=runner, grace=0.2)
    time.sleep(0.3)
    assert made.proc.poll() is None


def test_end_does_not_signal_a_pid_that_is_not_an_attachment_at_the_first_read(child) -> None:
    made = child("term")
    reused = made.row(command="python3 -m http.server")
    assert attachment.end([made.attachment], runner=_table(reused), grace=0.2) == []
    time.sleep(0.2)
    assert made.proc.poll() is None


@pytest.mark.parametrize("change", ["non-ssh-process", "different-attach-id"])
def test_end_does_not_kill_a_pid_that_changed_identity_between_sigterm_and_sigkill(child, change: str) -> None:
    made = child("ignore")
    real = made.row()
    changed = (
        made.row(command="python3 -m http.server")
        if change == "non-ssh-process"
        else made.row(attach_id="otherid")
    )

    def runner(argv):
        return (changed if made.marker.exists() else real) + "\n"

    assert attachment.end([made.attachment], runner=runner, grace=0.4) == []
    assert made.marker.exists()
    time.sleep(0.2)
    assert made.proc.poll() is None


def test_a_table_that_fails_before_any_signal_is_plain_unverifiable_and_signals_nothing(child) -> None:
    made = child("term")

    def runner(argv):
        raise Unverifiable("ps exited 1: simulated")

    with pytest.raises(Unverifiable) as raised:
        attachment.end([made.attachment], runner=runner, grace=0.2)
    assert not isinstance(raised.value, attachment.UnverifiableAfterSignal)
    time.sleep(0.2)
    assert made.proc.poll() is None


def test_a_table_that_fails_after_the_first_signal_says_so(child) -> None:
    made = child("term")
    reads = []

    def runner(argv):
        reads.append(argv)
        if len(reads) > 1:
            raise Unverifiable("ps exited 1: simulated")
        return made.row() + "\n"

    with pytest.raises(attachment.UnverifiableAfterSignal):
        attachment.end([made.attachment], runner=runner, grace=2.0)
    made.reaper.join(5)
    assert made.proc.returncode == -signal.SIGTERM


def test_end_does_not_term_a_pid_now_carrying_a_different_attach_id(child) -> None:
    made = child("term")
    other = made.row(attach_id="otherid")
    assert attachment.end([made.attachment], runner=_table(other), grace=0.2) == []
    time.sleep(0.2)
    assert made.proc.poll() is None


# --- port_holder -----------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def listener():
    held: list[socket.socket] = []

    def start(host: str = "127.0.0.1", family: int = socket.AF_INET) -> int:
        sock = socket.socket(family, socket.SOCK_STREAM)
        held.append(sock)
        sock.bind((host, 0))
        sock.listen(1)
        return sock.getsockname()[1]

    yield start
    for sock in held:
        sock.close()


def _no_lsof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def test_a_free_port_has_no_holder() -> None:
    assert attachment.port_holder(_free_port()) is None


def _time_wait_port() -> int:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _ = server.accept()
    accepted.close()
    client.close()
    server.close()
    return port


def test_a_port_in_time_wait_is_free_because_the_probe_binds_with_so_reuseaddr() -> None:
    port = _time_wait_port()
    control = socket.socket()
    try:
        control.bind(("127.0.0.1", port))
    except OSError:
        pass
    else:
        pytest.skip("this kernel lets a plain bind take a TIME_WAIT port")
    finally:
        control.close()
    assert attachment.port_holder(port, lsof_runner=lambda argv: "") is None


@pytest.mark.parametrize("address", ["0.0.0.0", "::"])
def test_a_wildcard_listener_holds_the_port(listener, address: str) -> None:
    family = socket.AF_INET if address == "0.0.0.0" else socket.AF_INET6
    try:
        port = listener(address, family)
    except OSError:
        pytest.skip("no IPv6 on this host")
    assert attachment.port_holder(port, lsof_runner=lambda argv: "") == "a process camp cannot see"


@pytest.mark.parametrize(
    "held_family, held_address",
    [
        (socket.AF_INET, "127.0.0.1"),
        (socket.AF_INET6, "::1"),
        (socket.AF_INET, "0.0.0.0"),
        (socket.AF_INET6, "::"),
    ],
)
def test_in_use_on_any_one_of_the_four_addresses_is_held(held_family: int, held_address: str) -> None:
    def bind(family, address, port):
        if (family, address) == (held_family, held_address):
            raise OSError(errno.EADDRINUSE, "in use")

    holder = attachment.port_holder(5555, bind=bind, lsof_runner=lambda argv: "", platform="darwin")
    assert holder == "a process camp cannot see"


def test_off_linux_every_address_is_probed_with_its_family() -> None:
    seen: list[tuple[int, str, int]] = []
    assert attachment.port_holder(5555, bind=lambda *a: seen.append(a), platform="darwin") is None
    assert seen == [
        (socket.AF_INET, "127.0.0.1", 5555),
        (socket.AF_INET6, "::1", 5555),
        (socket.AF_INET, "0.0.0.0", 5555),
        (socket.AF_INET6, "::", 5555),
    ]


@pytest.mark.parametrize("family, address", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::"), (socket.AF_INET, "0.0.0.0")])
def test_an_unexpected_bind_error_is_a_refusal_not_a_traceback(family: int, address: str) -> None:
    def bind(fam, addr, port):
        if (fam, addr) == (family, address):
            raise OSError(errno.EACCES, "Permission denied\x1b[31m")

    holder = attachment.port_holder(5555, bind=bind, lsof_runner=lambda argv: "p1\ncx\n", platform="darwin")
    assert holder is not None
    assert "could not be checked" in holder
    assert "Permission denied" in holder and "\x1b" not in holder


def _recording_bind(seen: list[tuple[int, str, int]]):
    return lambda *args: seen.append(args)


def test_on_linux_only_the_loopback_addresses_are_probed() -> None:
    seen: list[tuple[int, str, int]] = []
    assert attachment.port_holder(5555, bind=_recording_bind(seen), platform="linux") is None
    assert seen == [(socket.AF_INET, "127.0.0.1", 5555), (socket.AF_INET6, "::1", 5555)]


@pytest.mark.parametrize("platform", ["darwin", "freebsd14", "win32"])
def test_off_linux_the_wildcard_addresses_are_probed_too(platform: str) -> None:
    seen: list[tuple[int, str, int]] = []
    assert attachment.port_holder(5555, bind=_recording_bind(seen), platform=platform) is None
    assert {(family, address) for family, address, _ in seen} >= {(socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")}


def test_a_wildcard_in_use_answer_is_ignored_on_linux_and_held_off_it() -> None:
    def bind(family, address, port):
        if address in ("0.0.0.0", "::"):
            raise OSError(errno.EADDRINUSE, "in use")

    assert attachment.port_holder(5555, bind=bind, platform="linux") is None
    assert attachment.port_holder(5555, bind=bind, platform="darwin", lsof_runner=lambda argv: "") is not None


def _own_nonloopback_ipv4() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
        except OSError:
            pytest.skip("no route, so no non-loopback address of this machine to bind")
        address = probe.getsockname()[0]
    if address.startswith("127."):
        pytest.skip("this machine has no non-loopback IPv4 address")
    return address


def test_on_linux_a_listener_on_a_specific_non_loopback_address_leaves_the_port_free(listener) -> None:
    port = listener(_own_nonloopback_ipv4())
    assert attachment.port_holder(port, lsof_runner=lambda argv: "", platform="linux") is None
    with socket.socket() as ssh_like:
        ssh_like.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ssh_like.bind(("127.0.0.1", port))


def test_on_linux_a_dual_stack_wildcard_listener_is_still_held_by_the_loopback_probes(listener) -> None:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", 0))
    except OSError:
        sock.close()
        pytest.skip("no dual-stack IPv6 on this host")
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        assert attachment.port_holder(port, lsof_runner=lambda argv: "", platform="linux") == "a process camp cannot see"
    finally:
        sock.close()


@pytest.mark.parametrize("address, family", [("0.0.0.0", socket.AF_INET), ("::", socket.AF_INET6)])
def test_on_linux_a_wildcard_listener_is_held_without_the_wildcard_probes(listener, address: str, family: int) -> None:
    try:
        port = listener(address, family)
    except OSError:
        pytest.skip("no IPv6 on this host")
    assert attachment.port_holder(port, lsof_runner=lambda argv: "", platform="linux") == "a process camp cannot see"


def test_a_free_port_is_released_at_once() -> None:
    port = _free_port()
    assert attachment.port_holder(port) is None
    with socket.socket() as again:
        again.bind(("127.0.0.1", port))


def test_a_port_held_on_127_0_0_1_is_named_by_command_and_pid(listener) -> None:
    if subprocess.run(["which", "lsof"], capture_output=True).returncode != 0:
        pytest.skip("lsof not installed")
    port = listener()
    holder = attachment.port_holder(port)
    assert holder is not None and f"pid {os.getpid()}" in holder
    assert "python" in holder.lower()


def test_a_holder_is_described_from_lsof_field_output() -> None:
    fake = lambda argv: "p4242\ncsshd\nn127.0.0.1:2222\n"
    bound = lambda family, address, port: (_ for _ in ()).throw(OSError(errno.EADDRINUSE, "in use"))
    assert attachment.port_holder(2222, bind=bound, lsof_runner=fake) == "sshd (pid 4242)"


def test_a_holder_command_with_an_escape_sequence_is_described_with_it_removed() -> None:
    fake = lambda argv: "p4242\nc\x1b[31mevil\x1b[0m\nn127.0.0.1:2222\n"
    bound = lambda family, address, port: (_ for _ in ()).throw(OSError(errno.EADDRINUSE, "in use"))
    holder = attachment.port_holder(2222, bind=bound, lsof_runner=fake)
    assert "\x1b" not in holder and "evil" in holder and "4242" in holder


def test_a_port_held_only_on_ipv6_loopback_is_held(listener, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        port = listener("::1", socket.AF_INET6)
    except OSError:
        pytest.skip("no IPv6 loopback on this host")
    _no_lsof(tmp_path, monkeypatch)
    assert attachment.port_holder(port) == "a process camp cannot see"


def test_with_lsof_absent_a_held_port_is_a_process_camp_cannot_see(listener, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = listener()
    _no_lsof(tmp_path, monkeypatch)
    assert attachment.port_holder(port) == "a process camp cannot see"


def test_lsof_seeing_nothing_is_a_process_camp_cannot_see(listener) -> None:
    port = listener()
    assert attachment.port_holder(port, lsof_runner=lambda argv: "") == "a process camp cannot see"


@pytest.mark.parametrize("code", [errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT])
def test_an_ipv6_loopback_that_does_not_exist_counts_as_free(code: int) -> None:
    def bind(family, address, port):
        if family == socket.AF_INET6:
            raise OSError(code, "no ipv6")

    assert attachment.port_holder(5555, bind=bind) is None


def test_ipv6_in_use_is_held_even_when_ipv4_is_free() -> None:
    def bind(family, address, port):
        if family == socket.AF_INET6:
            raise OSError(errno.EADDRINUSE, "in use")

    assert attachment.port_holder(5555, bind=bind, lsof_runner=lambda argv: "") == "a process camp cannot see"


def test_ipv4_in_use_is_held_even_when_ipv6_is_free() -> None:
    def bind(family, address, port):
        if family == socket.AF_INET:
            raise OSError(errno.EADDRINUSE, "in use")

    assert attachment.port_holder(5555, bind=bind, lsof_runner=lambda argv: "") == "a process camp cannot see"
