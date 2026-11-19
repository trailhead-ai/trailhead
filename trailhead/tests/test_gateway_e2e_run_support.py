"""The helpers behind the gateway end-to-end run scenario (ungated: none of them needs the run engine).

The scenario itself lives in ``test_gateway_e2e_andromeda.py``; what it needs the sandbox of a normal
test run can check: the preflight's verdicts against fake hosts, the fixture repo through the
gateway command's own uploader and Outpost's own workflow reader, and the parsers and verdicts the
scenario applies to what a run left behind.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

from trailhead.gateway import tree_upload

from ._gateway_e2e_support import (
    RUN_GROUP,
    RealOutpost,
    act_container_names,
    act_process_lines,
    HomeSentinel,
    build_fixture_repo,
    check_container_inspect,
    copy_operator_config,
    exit_code,
    groups_of,
    load_runs_host,
    parse_stamped_lines,
    probe_problems,
    pull_stream_error,
    split_image_ref,
    preflight_problems,
    streamed_as_produced,
)

IMAGE = "registry.example/runner@sha256:" + "ab" * 32
RUNS_TOML = f"""\
checkout = "/somewhere/outpost"

[daemon]
http_port = 7313

[runs]
engine_socket = "@ENGINE@"
act_command = ["sudo", "-n", "-u", "outpost-runner", "/usr/local/lib/outpost-runner/act-run"]
runs_folder = "@FOLDER@"
platforms = {{ "ubuntu-latest" = "{IMAGE}", "ubuntu-24.04" = "{IMAGE}" }}
"""
GID = 4242


def _config(tmp_path: Path, *, text: str | None = None) -> Path:
    folder = tmp_path / "runs"
    folder.mkdir(exist_ok=True)
    path = tmp_path / "config.toml"
    path.write_text(
        text
        if text is not None
        else RUNS_TOML.replace("@ENGINE@", str(tmp_path / "engine.sock")).replace("@FOLDER@", str(folder))
    )
    return path


def _proc(tmp_path: Path, pid: int, groups: list[int], gid: int = 1000) -> Path:
    proc = tmp_path / "proc"
    (proc / str(pid)).mkdir(parents=True, exist_ok=True)
    (proc / str(pid) / "status").write_text(
        f"Name:\tpython\nUid:\t1000\t1000\t1000\t1000\nGid:\t{gid}\t{gid}\t{gid}\t{gid}\n"
        f"Groups:\t{' '.join(map(str, groups))} \nVmRSS:\t10 kB\n"
    )
    return proc


def _problems(
    tmp_path: Path, *, groups=(1000, GID), known=True, answers=lambda path: True, home_mode=0o750, config=None
):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    home.chmod(home_mode)

    def gid_of(name: str) -> int:
        if known and name == RUN_GROUP:
            return GID
        raise KeyError(name)

    return preflight_problems(
        config if config is not None else _config(tmp_path),
        pid=77,
        proc=_proc(tmp_path, 77, list(groups)),
        gid_of=gid_of,
        ping=answers,
        home=home,
    )


# --- preflight -----------------------------------------------------------------------------------


def test_a_host_that_is_set_up_has_no_preflight_problems(tmp_path):
    assert _problems(tmp_path) == []


def test_the_groups_of_a_process_are_read_from_its_status_file(tmp_path):
    proc = _proc(tmp_path, 5, [10, 20, 30], gid=1000)
    assert groups_of(5, proc) == {10, 20, 30, 1000}


def test_a_process_without_the_run_group_is_told_to_restart_the_user_manager(tmp_path):
    [problem] = _problems(tmp_path, groups=(1000,))
    assert RUN_GROUP in problem and "user@" in problem and ".service" in problem
    assert "logging out" in problem


def test_the_run_group_missing_from_the_host_says_to_provision_it(tmp_path):
    [problem] = _problems(tmp_path, known=False)
    assert f"no {RUN_GROUP} group" in problem and "provision-outpost-runner" in problem


def test_an_engine_socket_that_does_not_answer_is_named(tmp_path):
    config = _config(tmp_path)
    engine = load_runs_host(config).engine_socket
    [problem] = _problems(tmp_path, answers=lambda path: path != engine, config=config)
    assert engine in problem and "does not answer" in problem


def test_a_filter_socket_that_answers_means_the_operators_daemon_is_serving_and_must_be_stopped(tmp_path):
    config = _config(tmp_path)
    folder = load_runs_host(config).runs_folder
    (folder / "filter.sock").write_text("")
    [problem] = _problems(tmp_path, answers=lambda path: True, config=config)
    assert "filter.sock" in problem and "systemctl --user stop outpost.service" in problem
    assert "start" in problem


def test_a_filter_socket_that_is_left_behind_and_silent_is_to_be_removed(tmp_path):
    config = _config(tmp_path)
    host = load_runs_host(config)
    (host.runs_folder / "filter.sock").write_text("")
    [problem] = _problems(tmp_path, answers=lambda path: path == host.engine_socket, config=config)
    assert "stale" in problem and f"rm {host.runs_folder / 'filter.sock'}" in problem


def test_a_home_the_run_account_could_read_is_a_problem(tmp_path):
    [problem] = _problems(tmp_path, home_mode=0o755)
    assert "0755" in problem and "0750" in problem


def test_a_config_without_a_runs_table_is_one_problem_and_nothing_else_is_checked(tmp_path):
    config = _config(tmp_path, text="[daemon]\nhttp_port = 7313\n")
    problems = _problems(tmp_path, groups=(1000,), answers=lambda path: False, config=config)
    assert len(problems) == 1 and "[runs]" in problems[0]


def test_every_problem_is_reported_together(tmp_path):
    problems = _problems(tmp_path, groups=(1000,), answers=lambda path: False, home_mode=0o755)
    assert len(problems) == 3


def test_a_config_that_is_missing_is_a_problem_naming_the_path(tmp_path):
    missing = tmp_path / "absent.toml"
    [problem] = _problems(tmp_path, config=missing)
    assert str(missing) in problem


def test_the_operators_runs_table_is_carried_over_exactly(tmp_path):
    source = _config(tmp_path)
    dest = tmp_path / "outpost-config"
    copy_operator_config(source, dest)
    assert load_runs_host(dest / "config.toml") == load_runs_host(source)
    assert load_runs_host(source).platforms["ubuntu-latest"] == IMAGE


# --- the fixture repo, through the real uploader and the real workflow reader ------------------------

JOBS = {
    "pass",
    "fail",
    "matrix",
    "tree",
    "local_action",
    "probe",
    "public",
    "mount_priv",
    "pull_missing",
    "unmapped",
    "slow",
    "cancel_hold",
    "cancel_behind",
}


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixture")
    ssh = root / "operator-home" / ".ssh"
    ssh.mkdir(parents=True)
    sentinel = HomeSentinel(root / "operator-home" / ".outpost-e2e-x", "SENTINEL-TEXT")
    repo = build_fixture_repo(
        root / "app", image=IMAGE, ssh=ssh, sentinel=sentinel, ports=(7401, 7402), operator_home=root / "operator-home"
    )
    return repo, ssh, sentinel


def _tar(repo: Path, home: Path) -> tarfile.TarFile:
    """The upload the gateway command builds of *repo*, its private folder made under *home*."""
    spool = tempfile.TemporaryFile()
    tree_upload.write_tar(tree_upload.top_level(repo, home=home), spool)
    spool.seek(0)
    return tarfile.open(fileobj=spool)


def _text(tar: tarfile.TarFile, name: str) -> str:
    return tar.extractfile(name).read().decode()


def test_the_upload_carries_the_uncommitted_edit_and_the_untracked_file(fixture_repo, tmp_path):
    repo, _, _ = fixture_repo
    tar = _tar(repo, tmp_path)
    assert _text(tar, "tracked.txt").strip() == "edited-uncommitted"
    assert _text(tar, "untracked.txt").strip() == "new-file"


def test_the_upload_leaves_out_the_ignored_file(fixture_repo, tmp_path):
    repo, _, _ = fixture_repo
    assert "ignored.txt" not in _tar(repo, tmp_path).getnames()
    assert (repo / "ignored.txt").exists()


def test_the_upload_carries_the_uncommitted_workflow_change(fixture_repo, tmp_path):
    repo, _, _ = fixture_repo
    workflow = _text(_tar(repo, tmp_path), ".github/workflows/ci.yml")
    assert 'workflow-edit=ap""plied' in workflow and 'NOT-ap""plied' not in workflow
    committed = subprocess.run(
        ["git", "show", "HEAD:.github/workflows/ci.yml"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert 'workflow-edit=NOT-ap""plied' in committed


def test_the_symlinks_into_the_operators_home_travel_as_links_to_where_they_point(fixture_repo, tmp_path):
    repo, ssh, sentinel = fixture_repo
    tar = _tar(repo, tmp_path)
    assert tar.getmember("link-to-ssh").issym() and tar.getmember("link-to-ssh").linkname == str(ssh)
    assert tar.getmember("link-to-home").issym() and tar.getmember("link-to-home").linkname == str(sentinel.path)


def test_every_job_is_found_by_the_command_in_the_one_workflow(fixture_repo):
    repo, _, _ = fixture_repo
    top = tree_upload.top_level(repo)
    for job in JOBS:
        assert tree_upload.workflow_defining(top, job) == ".github/workflows/ci.yml", job


def test_the_workflow_follows_the_image_and_the_ports_the_builder_is_given(tmp_path, fixture_repo):
    _, ssh, sentinel = fixture_repo
    other = build_fixture_repo(
        tmp_path / "app-other",
        image="registry.example/other-image:9",
        ssh=ssh,
        sentinel=sentinel,
        ports=(8811, 8822),
        operator_home=ssh.parent,
    )
    workflow = (other / ".github/workflows/ci.yml").read_text()
    assert "image: registry.example/other-image:9" in workflow
    assert f"image: {IMAGE}" not in workflow
    assert "8811" in workflow and "8822" in workflow
    assert "7401" not in workflow and "7402" not in workflow


def test_a_second_repo_with_another_name_builds_beside_the_first(tmp_path, fixture_repo):
    _, ssh, sentinel = fixture_repo
    other = build_fixture_repo(
        tmp_path / "app2", image=IMAGE, ssh=ssh, sentinel=sentinel, ports=(1, 2), operator_home=ssh.parent
    )
    assert tree_upload.workflow_defining(tree_upload.top_level(other), "pass") == ".github/workflows/ci.yml"


# --- what a session or a harness left behind -----------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("Exit code 3\nstderr line", 3),
        ("Exit code 1\n", 1),
        ("Exit code 2", 2),
        ("7\n", 0),
        ("the words Exit code 9 in the middle\n", 0),
    ],
)
def test_a_tool_result_carries_its_exit_code_only_as_a_prefix(output, code):
    assert exit_code(output) == code


def test_stamped_lines_are_read_in_order_and_other_lines_are_ignored():
    out = "noise\n@@line 100.5 stream-first\n@@line 120.25 stream-second with words\n@@end rc=0\n"
    assert parse_stamped_lines(out) == [(100.5, "stream-first"), (120.25, "stream-second with words")]


def test_output_that_arrived_apart_is_streamed():
    stamped = [(100.0, "stream-first"), (121.0, "stream-second")]
    assert streamed_as_produced(stamped, "stream-first", "stream-second", 10.0) is None


def test_output_that_arrived_together_is_not_streamed():
    stamped = [(121.0, "stream-first"), (121.1, "stream-second")]
    assert "together" in streamed_as_produced(stamped, "stream-first", "stream-second", 10.0)


def test_output_missing_a_line_is_not_streamed():
    assert "stream-second" in streamed_as_produced([(1.0, "stream-first")], "stream-first", "stream-second", 10.0)


def test_output_exactly_at_the_gap_is_streamed():
    stamped = [(1.0, "a"), (11.0, "b")]
    assert streamed_as_produced(stamped, "a", "b", 10.0) is None
    assert streamed_as_produced(stamped, "a", "b", 10.5) is not None


def _inspect(**over):
    body = {
        "Name": "/act-ci-slow-0123",
        "Config": {"Env": ["PATH=/usr/bin", "GITHUB_TOKEN=", "RUNNER_OS=Linux"]},
        "HostConfig": {"Binds": None, "Privileged": False, "Mounts": None},
        "Mounts": [
            {
                "Type": "volume",
                "Name": "act-toolcache",
                "Source": "/v/act-toolcache",
                "Destination": "/opt/hostedtoolcache",
            }
        ],
    }
    for path, value in over.items():
        node = body
        *parents, leaf = path.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return body


def test_a_container_with_only_volumes_and_no_secret_is_clean():
    assert check_container_inspect(_inspect(), ["s3cret"]) == []


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"Mounts": [{"Type": "bind", "Source": "/home/op", "Destination": "/h"}]}, "bind"),
        ({"HostConfig__Binds": ["/:/host"]}, "Binds"),
        ({"HostConfig__Privileged": True}, "privileged"),
        ({"HostConfig__Mounts": [{"Type": "bind", "Source": "/x", "Destination": "/y"}]}, "bind"),
        (
            {"Mounts": [{"Type": "volume", "Source": "/run/docker.sock", "Destination": "/var/run/docker.sock"}]},
            "socket",
        ),
        ({"Config__Env": ["A=has-s3cret-inside"]}, "A"),
        ({"Config__Env": ["GITHUB_TOKEN=ghp_abc"]}, "GITHUB_TOKEN"),
        ({"Config__Env": ["SSH_AUTH_SOCK=/tmp/agent"]}, "SSH_AUTH_SOCK"),
    ],
)
def test_each_way_a_container_could_carry_the_host_in_is_named(change, needle):
    problems = check_container_inspect(_inspect(**change), ["s3cret"])
    assert problems and any(needle in p for p in problems)


def test_a_secret_is_named_by_variable_and_never_printed():
    problems = check_container_inspect(_inspect(Config__Env=["A=has-s3cret-inside"]), ["s3cret"])
    assert all("s3cret" not in p for p in problems)


def test_the_control_action_is_in_the_upload_and_the_sentinel_action_is_a_dot_slash_reference_that_climbs_out(
    fixture_repo, tmp_path
):
    repo, _, sentinel = fixture_repo
    tar = _tar(repo, tmp_path)
    assert "ok-action/action.yml" in tar.getnames()
    workflow = _text(tar, ".github/workflows/ci.yml")
    assert "- uses: ./ok-action\n" in workflow
    [reference] = [line.split("uses: ")[1] for line in workflow.splitlines() if "uses: ./../" in line]
    assert reference.startswith("./../../") and reference.endswith(str(sentinel.path).lstrip("/"))


def test_the_workflow_source_never_holds_a_token_the_scenario_asserts_on_the_log(fixture_repo):
    repo, _, sentinel = fixture_repo
    workflow = (repo / ".github/workflows/ci.yml").read_text()
    for token in (
        "pass-job-ran-42",
        "fail-job-ran-42",
        "ok-action-ran-42",
        "stream-first-42",
        "stream-second-42",
        sentinel.marker,
    ):
        assert token not in workflow + (repo / "ok-action/action.yml").read_text(), token
    for verdict in ("REACHED", "refused", "reached", "applied", "absent", "present", "=link"):
        assert verdict not in workflow, verdict


# --- the probe job's log, judged ---------------------------------------------------------------------

PORTS = (7401, 7402)
MARKER = "SENTINEL-0123"


def _probe_log(*, drop=None, replace=None, extra=""):
    lines = [
        "probe control: 21",
        "probe control public: reached",
        "probe home: ls: cannot access '/home/op': No such file or directory",
        "probe sentinel read: cat: /home/op/.x/secret.txt: No such file or directory",
        "probe engine socket /var/run/docker.sock: absent",
        "probe engine socket /run/docker.sock: absent",
        "probe engine socket /var/lib/outpost-runner-engine/docker.sock: absent",
        "probe gateway: 10.0.2.2",
        "probe docker env: 0",
    ]
    for h in ("127.0.0.1", "10.0.2.2", "172.17.0.1", "host.docker.internal"):
        for p in PORTS:
            lines.append(f"probe loopback {h}:{p}: refused")
    if drop:
        lines = [ln for ln in lines if drop not in ln]
    if replace:
        lines = [ln.replace(*replace) for ln in lines]
    return "\n".join(f"[e2e/probe]   | {ln}" for ln in lines) + extra


def test_a_probe_log_where_everything_was_refused_and_every_control_ran_has_no_problem():
    assert probe_problems(_probe_log(), PORTS, MARKER) == []


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"drop": "probe control: "}, "control"),
        ({"replace": ("probe control: 21", "probe control: 0")}, "control"),
        ({"drop": "control public"}, "public"),
        ({"replace": ("control public: reached", "control public: unreached")}, "public"),
        ({"drop": "probe gateway"}, "gateway"),
        ({"replace": ("probe gateway: 10.0.2.2", "probe gateway: ")}, "gateway"),
        ({"replace": ("10.0.2.2:7401: refused", "10.0.2.2:7401: REACHED")}, "10.0.2.2:7401"),
        ({"drop": "172.17.0.1:7402"}, "172.17.0.1:7402"),
        ({"replace": ("socket /run/docker.sock: absent", "socket /run/docker.sock: present")}, "/run/docker.sock"),
        (
            {
                "replace": (
                    "probe home: ls: cannot access '/home/op': No such file or directory",
                    "probe home: Documents",
                )
            },
            "home",
        ),
        ({"replace": ("secret.txt: No such file or directory", "secret.txt: ok")}, "sentinel"),
        ({"extra": f"\n| {MARKER}"}, "operator"),
        ({"replace": ("docker env: 0", "docker env: 2")}, "docker env"),
    ],
)
def test_each_way_the_probe_could_be_wrong_or_not_have_run_is_a_problem(kwargs, needle):
    problems = probe_problems(_probe_log(**kwargs), PORTS, MARKER)
    assert problems and any(needle in p for p in problems), problems


def test_the_gateway_the_job_found_is_probed_too():
    log = _probe_log(replace=("probe gateway: 10.0.2.2", "probe gateway: 192.168.5.1"))
    assert any("192.168.5.1:7401" in p for p in probe_problems(log, PORTS, MARKER))
    good = log + "".join(f"\n| probe loopback 192.168.5.1:{p}: refused" for p in PORTS)
    assert probe_problems(good, PORTS, MARKER) == []


# --- images and the engine's pull stream ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "parts"),
    [
        (
            "ghcr.io/catthehacker/ubuntu:act-24.04@sha256:" + "ab" * 32,
            ("ghcr.io/catthehacker/ubuntu", "sha256:" + "ab" * 32),
        ),
        ("ghcr.io/x/y@sha256:" + "cd" * 32, ("ghcr.io/x/y", "sha256:" + "cd" * 32)),
        ("registry:5000/x/y:v1", ("registry:5000/x/y", "v1")),
        ("registry:5000/x/y", ("registry:5000/x/y", "latest")),
        ("ubuntu", ("ubuntu", "latest")),
    ],
)
def test_an_image_reference_splits_into_what_the_engines_pull_endpoint_takes(ref, parts):
    assert split_image_ref(ref) == parts


def test_a_pull_stream_that_ends_well_has_no_error():
    body = b'{"status":"Pulling from x"}\r\n{"status":"Digest: sha256:ab"}\r\n'
    assert pull_stream_error(body) is None


def test_a_pull_stream_carrying_an_error_line_reports_it():
    body = b'{"status":"Pulling from x"}\n{"errorDetail":{"message":"denied"},"error":"denied: no access"}\n'
    assert pull_stream_error(body) == "denied: no access"


def test_a_container_record_that_is_cut_down_proves_nothing_and_is_a_problem():
    cut = _inspect()
    del cut["HostConfig"]
    assert any("HostConfig" in p for p in check_container_inspect(cut, ["s3cret"]))
    cut = _inspect()
    del cut["Config"]["Env"]
    assert any("Env" in p for p in check_container_inspect(cut, ["s3cret"]))
    assert any("volume" in p for p in check_container_inspect(_inspect(Mounts=[]), ["s3cret"]))
    assert any("secrets" in p for p in check_container_inspect(_inspect(), []))


@pytest.mark.parametrize(("with_reason", "reason"), [(False, None), (True, "run did not execute: x")])
def test_the_run_rows_are_read_from_a_table_with_or_without_a_reason_column(tmp_path, with_reason, reason):
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute(
        "create table gateway_runs(id integer primary key, workspace text, job text, state text, result text"
        + (", reason text" if with_reason else "")
        + ")"
    )
    db.execute(
        "insert into gateway_runs values(1, 'g/w', 'pass', 'completed', 'failure'"
        + (f", '{reason}'" if with_reason else "")
        + ")"
    )
    db.commit()
    db.close()
    assert RealOutpost(1, tmp_path, tmp_path / "log").runs() == [(1, "g/w", "pass", "completed", "failure", reason)]


# --- what a cancelled run must leave behind: act processes and act containers --------------------------

PS = """\
  PID COMMAND         COMMAND
  101 rootlesskit     rootlesskit --net=slirp4netns dockerd
  102 dockerd         dockerd --rootless
  230 sudo            sudo -n -u outpost-runner /usr/local/lib/outpost-runner/act-run -j slow
  231 act-run         /bin/bash /usr/local/lib/outpost-runner/act-run -j slow
  232 act             act -j slow
"""


def test_the_act_processes_are_those_named_act_and_not_the_engine_beside_them():
    assert [line.split()[0] for line in act_process_lines(PS)] == ["231", "232"]


def test_a_process_list_with_no_act_in_it_has_no_act_process():
    assert act_process_lines("  PID COMMAND COMMAND\n  101 dockerd dockerd --rootless\n") == []


def test_an_empty_process_list_has_no_act_process():
    assert act_process_lines("") == []


def _engine(containers, status=200):
    """A stand-in for ``engine_request`` that answers only the all-containers listing."""

    def request(socket_path, method, path, timeout=10):
        if (method, path) != ("GET", "/containers/json?all=1"):
            return 404, b"[]"
        return status, json.dumps(containers).encode()

    return request


@pytest.mark.parametrize(
    "containers, names",
    [
        ([{"Id": "a", "Names": ["/act-ci-hold-1"]}, {"Id": "b", "Names": ["/someone-elses"]}], ["/act-ci-hold-1"]),
        ([{"Id": "b", "Names": ["/someone-elses"]}], []),
        ([], []),
    ],
)
def test_the_act_containers_are_listed_stopped_ones_included(containers, names):
    assert act_container_names("unused", request=_engine(containers)) == names


def test_an_engine_that_will_not_say_lists_nothing():
    assert act_container_names("unused", request=_engine([{"Id": "a", "Names": ["/act-x"]}], status=500)) == []
