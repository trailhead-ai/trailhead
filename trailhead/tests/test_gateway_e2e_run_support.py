"""The helpers behind the gateway end-to-end run scenario (ungated: none of them needs the run engine).

The scenario itself lives in ``test_gateway_e2e_andromeda.py``; what it needs the sandbox of a normal
test run can check: the preflight's verdicts against fake hosts, the fixture repo through the
gateway command's own uploader and Outpost's own workflow reader, and the parsers and verdicts the
scenario applies to what a run left behind.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

from trailhead.gateway import tree_upload

from ._gateway_e2e_support import (
    RUN_GROUP,
    HomeSentinel,
    build_fixture_repo,
    check_container_inspect,
    copy_operator_config,
    exit_code,
    groups_of,
    load_runs_host,
    outpost_checkout,
    parse_stamped_lines,
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
    path.write_text(text if text is not None else RUNS_TOML.replace("@ENGINE@", str(tmp_path / "engine.sock")).replace("@FOLDER@", str(folder)))
    return path


def _proc(tmp_path: Path, pid: int, groups: list[int], gid: int = 1000) -> Path:
    proc = tmp_path / "proc"
    (proc / str(pid)).mkdir(parents=True, exist_ok=True)
    (proc / str(pid) / "status").write_text(
        f"Name:\tpython\nUid:\t1000\t1000\t1000\t1000\nGid:\t{gid}\t{gid}\t{gid}\t{gid}\n"
        f"Groups:\t{' '.join(map(str, groups))} \nVmRSS:\t10 kB\n"
    )
    return proc


def _problems(tmp_path: Path, *, groups=(1000, GID), known=True, answers=lambda path: True, home_mode=0o750, config=None):
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

JOBS = {"pass", "fail", "matrix", "tree", "local_action", "probe", "public", "mount_priv", "pull_missing", "unmapped", "slow"}


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


def _tar(repo: Path) -> tarfile.TarFile:
    spool = tempfile.TemporaryFile()
    tree_upload.write_tar(tree_upload.top_level(repo), spool)
    spool.seek(0)
    return tarfile.open(fileobj=spool)


def _text(tar: tarfile.TarFile, name: str) -> str:
    return tar.extractfile(name).read().decode()


def test_the_upload_carries_the_uncommitted_edit_and_the_untracked_file(fixture_repo):
    repo, _, _ = fixture_repo
    tar = _tar(repo)
    assert _text(tar, "tracked.txt").strip() == "edited-uncommitted"
    assert _text(tar, "untracked.txt").strip() == "new-file"


def test_the_upload_leaves_out_the_ignored_file(fixture_repo):
    repo, _, _ = fixture_repo
    assert "ignored.txt" not in _tar(repo).getnames()
    assert (repo / "ignored.txt").exists()


def test_the_upload_carries_the_uncommitted_workflow_change(fixture_repo):
    repo, _, _ = fixture_repo
    workflow = _text(_tar(repo), ".github/workflows/ci.yml")
    assert "workflow-edit=applied" in workflow and "workflow-edit=NOT-applied" not in workflow
    committed = subprocess.run(
        ["git", "show", "HEAD:.github/workflows/ci.yml"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "workflow-edit=NOT-applied" in committed


def test_the_symlinks_into_the_operators_home_travel_as_links_to_where_they_point(fixture_repo):
    repo, ssh, sentinel = fixture_repo
    tar = _tar(repo)
    assert tar.getmember("link-to-ssh").issym() and tar.getmember("link-to-ssh").linkname == str(ssh)
    assert tar.getmember("link-to-home").issym() and tar.getmember("link-to-home").linkname == str(sentinel.path)


def test_every_job_is_found_by_the_command_in_the_one_workflow(fixture_repo):
    repo, _, _ = fixture_repo
    top = tree_upload.top_level(repo)
    for job in JOBS:
        assert tree_upload.workflow_defining(top, job) == ".github/workflows/ci.yml", job


def _node_reads(workflow: Path) -> dict:
    checkout = outpost_checkout()
    if not (checkout / "node_modules" / "tsx").is_dir() or shutil.which("node") is None:
        pytest.skip("needs node and the Outpost checkout's node_modules")
    code = (
        "import { readFileSync } from 'node:fs';"
        f"const w = await import({json.dumps(str(checkout / 'server/runs/workflow.ts'))});"
        "const wf = w.parseWorkflow(readFileSync(process.argv[1], 'utf8'));"
        "const out = {};"
        "for (const id of w.jobIds(wf)) out[id] = w.planJob(wf, id);"
        "console.log(JSON.stringify(out));"
    )
    done = subprocess.run(
        ["node", "--import", "tsx", "--input-type=module", "-e", code, str(workflow)],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout)


def test_outposts_reader_finds_the_jobs_their_labels_and_the_matrix_first_values(fixture_repo):
    repo, _, _ = fixture_repo
    plans = _node_reads(repo / ".github/workflows/ci.yml")
    assert set(plans) == JOBS
    assert plans["matrix"]["matrix"]["chosen"] == {"color": "red", "size": "small"}
    assert plans["matrix"]["matrix"]["notRun"] == 3
    assert plans["pass"]["matrix"] is None
    assert plans["unmapped"]["runsOn"] == ["windows-latest"]
    assert all(plans[j]["runsOn"] == ["ubuntu-latest"] for j in JOBS - {"unmapped"})


def test_the_container_jobs_name_the_image_the_host_maps_and_one_that_does_not_exist(fixture_repo):
    repo, _, _ = fixture_repo
    workflow = (repo / ".github/workflows/ci.yml").read_text()
    assert f"image: {IMAGE}" in workflow
    assert "--privileged" in workflow and "-v /:/host" in workflow
    assert "container: docker.io/library/outpost-e2e-no-such-image" in workflow


def test_the_probe_job_names_both_listeners_on_the_hosts_loopback(fixture_repo):
    repo, _, _ = fixture_repo
    workflow = (repo / ".github/workflows/ci.yml").read_text()
    assert "7401" in workflow and "7402" in workflow


def test_a_second_repo_with_another_name_builds_beside_the_first(tmp_path, fixture_repo):
    _, ssh, sentinel = fixture_repo
    other = build_fixture_repo(tmp_path / "app2", image=IMAGE, ssh=ssh, sentinel=sentinel, ports=(1, 2), operator_home=ssh.parent)
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
        "Mounts": [{"Type": "volume", "Name": "act-toolcache", "Source": "/v/act-toolcache", "Destination": "/opt/hostedtoolcache"}],
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
        ({"Mounts": [{"Type": "volume", "Source": "/run/docker.sock", "Destination": "/var/run/docker.sock"}]}, "socket"),
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
