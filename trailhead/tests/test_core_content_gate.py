"""The core-vault content gate, run as a subprocess against temporary repo roots.

Every forbidden token is assembled at runtime from parts: this file is itself
scanned by the leak gate and must never hold one as a literal.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "core-content-gate"
_LORE_CLI = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "cli" / "lore"

EMAIL = "ann" + "@" + "example" + ".org"
COMMIT = "a1b2c3d" + "4e5f6"
FINGERPRINT = "0123456789abcdef" * 2 + "01234567"
SHA256 = "0123456789abcdef" * 4
CREDENTIAL = "gh" + "p_" + "A1b2" * 9
DENY_TERM = "Orchid" + "Quartz"
REDACTED = "<redacted-name>"

# class label, offending text
CASES = {
    "email": ("email", EMAIL),
    "commit-id": ("commit-id", COMMIT),
    "fingerprint": ("commit-id", FINGERPRINT),
    "home-linux": ("home-path", "/ho" + "me/alice"),
    "home-mac": ("home-path", "/Us" + "ers/bob"),
    "home-root": ("home-path", "/ro" + "ot"),
    "home-drive-backslash": ("home-path", "C:" + "\\Us" + "ers\\carol"),
    "home-drive-slash": ("home-path", "D:" + "/Us" + "ers/dave"),
    "credential": ("credential", CREDENTIAL),
}


def _env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY", "CORE_CONTENT_DENYLIST")
    }
    env.update(extra)
    return env


def _run_in(checkout: Path, root: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(checkout / "scripts" / "core-content-gate"), "--repo-root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(**env),
    )


def _run(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return _run_in(_REPO_ROOT, root, *args, **env)


def _vault(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "core-vault").mkdir()
    (tmp_path / "tools" / "t" / "plugins" / "p").mkdir(parents=True)
    return tmp_path


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / "core-vault" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    return (result.stdout + result.stderr).splitlines()


def _substrings(text: str, minimum: int = 6):
    for i in range(len(text)):
        for j in range(i + minimum, len(text) + 1):
            yield text[i:j]


def _no_echo(result, token: str) -> None:
    out = result.stdout + result.stderr
    assert [s for s in _substrings(token) if s in out] == []


def _deny(tmp_path: Path) -> list[str]:
    denylist = tmp_path / "deny.txt"
    denylist.write_text(DENY_TERM + "\n", encoding="utf-8")
    return ["--denylist", str(denylist)]


def _load_gate():
    loader = importlib.machinery.SourceFileLoader("core_content_gate_under_test", str(_GATE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize("case", CASES)
def test_each_class_fails_with_file_line_and_class(tmp_path, case):
    cls, token = CASES[case]
    root = _vault(tmp_path)
    _write(root, "note.md", f"first line\nsecond line\nsee {token} here\n")

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"core-vault/note.md:3:{cls}" in _lines(result)


def test_a_sidecar_is_scanned_too(tmp_path):
    root = _vault(tmp_path)
    _write(root, "blob/x.json", '{\n  "created-by": "' + EMAIL + '"\n}\n')

    result = _run(root)

    assert result.returncode == 1
    assert "core-vault/blob/x.json:2:email" in _lines(result)


def test_deny_list_term_fails_with_the_list_line_number(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"one\nmentions {DENY_TERM.lower()} here\n")
    denylist = tmp_path / "deny.txt"
    denylist.write_text(f"# comment\nnever-present-term\n{DENY_TERM}\n", encoding="utf-8")

    result = _run(root, "--denylist", str(denylist))

    assert result.returncode == 1
    assert "core-vault/note.md:2:deny-list#3" in _lines(result)


@pytest.mark.parametrize("case", CASES)
def test_no_echo_of_matched_text_or_its_substrings(tmp_path, case):
    cls, token = CASES[case]
    root = _vault(tmp_path)
    _write(root, "note.md", f"see {token} here\n")

    result = _run(root)

    assert result.returncode == 1
    assert f"core-vault/note.md:1:{cls}" in result.stdout + result.stderr
    _no_echo(result, token)


def test_no_echo_of_a_deny_list_term(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"mentions {DENY_TERM}\n")

    result = _run(root, *_deny(tmp_path))

    assert result.returncode == 1
    _no_echo(result, DENY_TERM)


@pytest.mark.seam_real
def test_a_real_lore_written_record_passes(tmp_path):
    state = {name: tmp_path / "fence" / name for name in ("home", "cfg", "state", "cache", "data")}
    for path in state.values():
        path.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LORE_", "TRAILHEAD_"))}
    env.update(
        HOME=str(state["home"]),
        XDG_CONFIG_HOME=str(state["cfg"]),
        XDG_STATE_HOME=str(state["state"]),
        XDG_CACHE_HOME=str(state["cache"]),
        XDG_DATA_HOME=str(state["data"]),
        LORE_GROUPS_DIR=str(tmp_path / "fence" / "groups"),
        LORE_EMAIL="trailhead",
        LORE_USER="trailhead",
        LORE_PUBLISH_DISABLE="1",
    )

    def lore(*args: str, stdin: str | None = None) -> None:
        done = subprocess.run(
            [sys.executable, str(_LORE_CLI), *args],
            input=stdin,
            env=env,
            cwd=state["home"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr

    lore("init")
    lore("record", "create", "--kind", "blob", "--title", "Test doctrine", stdin="Plain doctrine body.\n")
    vault = state["state"] / "lore" / "vaults" / "default" / "blob"
    root = _vault(tmp_path)
    (root / "core-vault" / "blob").mkdir()
    for suffix in (".md", ".json"):
        (root / "core-vault" / "blob" / f"test-doctrine{suffix}").write_bytes(
            (vault / f"test-doctrine{suffix}").read_bytes()
        )

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "deadbeef is a word\n",
        "a1b2c3 short run\n",
        "see home/x for it\n",
        "see docs/home/x for it\n",
        "ticket 20260616 and 1234567890\n",
        "see ~/x for it\n",
        "see tools/craft/plugins/craft/scripts for it\n",
        "plain prose\n",
    ],
    ids=["hex-word-no-digit", "six-hex", "relative-home", "nested-home", "digits-only", "tilde", "repo-path", "prose"],
)
def test_near_misses_pass(tmp_path, text):
    root = _vault(tmp_path)
    _write(root, "note.md", text)

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_64_hex_digest_is_not_a_commit_id(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"digest {SHA256}\n")

    result = _run(root)

    assert not [line for line in _lines(result) if line.endswith(":commit-id")]


@pytest.mark.parametrize(
    "name, payload",
    [
        ("note.md", f"mail {EMAIL}\n".encode("utf-16")),
        ("image.bin", b"\x89PNG\r\n\x1a\n\x00\x00\xff\xfe"),
        ("image.bin", b"valid text\x00with a NUL\n"),
        ("image.bin", b"caf\xe9\n"),
    ],
    ids=["utf16", "not-utf8", "nul-in-utf8", "latin1"],
)
def test_a_file_that_is_not_utf8_text_is_exit_2(tmp_path, name, payload):
    root = _vault(tmp_path)
    (root / "core-vault" / name).write_bytes(payload)

    result = _run(root)

    assert result.returncode == 2
    assert any(line.startswith("gate could not run:") for line in _lines(result))


def test_a_symlink_is_a_finding_and_is_not_followed(tmp_path):
    root = _vault(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("clean\n", encoding="utf-8")
    (root / "core-vault" / "link.md").symlink_to(outside)

    result = _run(root)

    assert result.returncode == 1
    assert "core-vault/link.md:0:symlink" in _lines(result)


def test_a_symlinked_core_vault_is_a_finding(tmp_path):
    real = tmp_path / "real-core"
    real.mkdir()
    (real / "note.md").write_text(f"mail {EMAIL}\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tools" / "t" / "plugins" / "p").mkdir(parents=True)
    (repo / "core-vault").symlink_to(real, target_is_directory=True)

    result = _run(repo)

    assert result.returncode == 1
    assert "core-vault:0:symlink" in _lines(result)


def test_a_symlinked_directory_is_a_finding(tmp_path):
    root = _vault(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "core-vault" / "sub").symlink_to(outside, target_is_directory=True)

    result = _run(root)

    assert result.returncode == 1
    assert "core-vault/sub:0:symlink" in _lines(result)


def test_a_filename_holding_an_email_is_redacted(tmp_path):
    root = _vault(tmp_path)
    _write(root, f"sub/{EMAIL}.md", f"commit {COMMIT}\n")

    result = _run(root)

    assert result.returncode == 1
    assert "core-vault/sub/<redacted-name>:1:commit-id" in _lines(result)
    _no_echo(result, EMAIL)


def test_a_clean_filename_is_printed_as_is(tmp_path):
    root = _vault(tmp_path)
    _write(root, "sub/plain-name.md", f"commit {COMMIT}\n")

    result = _run(root)

    assert "core-vault/sub/plain-name.md:1:commit-id" in _lines(result)


def test_github_actions_annotations_and_summary(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"ok\nmail {EMAIL}\n")
    summary = tmp_path / "summary.md"

    result = _run(root, GITHUB_ACTIONS="true", GITHUB_STEP_SUMMARY=str(summary))

    lines = _lines(result)
    assert "::error file=core-vault/note.md,line=2::email" in lines
    assert any(line.startswith("::warning::no deny-list supplied; generic patterns only") for line in lines)
    assert "no deny-list supplied; generic patterns only" in summary.read_text(encoding="utf-8")


def test_without_github_actions_there_are_no_annotations(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"mail {EMAIL}\n")

    result = _run(root)

    assert not [line for line in _lines(result) if line.startswith("::")]


def test_no_deny_list_prints_the_notice_and_still_fails_on_a_finding(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"mail {EMAIL}\n")

    result = _run(root)

    assert result.returncode == 1
    assert "no deny-list supplied; generic patterns only" in _lines(result)


def test_a_clean_run_with_no_deny_list_exits_0(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", "plain\n")

    assert _run(root).returncode == 0


@pytest.mark.parametrize("via", ["flag", "env"])
def test_a_deny_list_applies_and_prints_its_pattern_count(tmp_path, via):
    root = _vault(tmp_path)
    _write(root, "note.md", f"mentions {DENY_TERM}\n")
    denylist = tmp_path / "deny.txt"
    denylist.write_text(f"{DENY_TERM}\nsecond-term\n", encoding="utf-8")

    if via == "flag":
        result = _run(root, "--denylist", str(denylist))
    else:
        result = _run(root, CORE_CONTENT_DENYLIST=str(denylist))

    assert result.returncode == 1
    lines = _lines(result)
    assert "core-vault/note.md:1:deny-list#1" in lines
    assert "deny-list: 2 patterns loaded" in lines


def _bad_denylist(tmp_path: Path, kind: str) -> str:
    path = tmp_path / "deny.txt"
    if kind == "missing":
        return str(path)
    if kind == "comments-only":
        path.write_text("# only " + DENY_TERM + " here\n\n", encoding="utf-8")
    else:
        path.write_text(f"fine-term\n{DENY_TERM}(unclosed\n", encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("kind", ["missing", "comments-only", "invalid-regex"])
def test_a_bad_deny_list_is_exit_2_without_printing_the_list(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = _vault(repo)
    _write(root, "note.md", "plain\n")

    result = _run(root, "--denylist", _bad_denylist(tmp_path, kind))

    assert result.returncode == 2
    out = result.stdout + result.stderr
    assert any(line.startswith("gate could not run:") for line in out.splitlines())
    _no_echo(result, DENY_TERM)
    assert "unterminated" not in out and "missing )" not in out


def test_an_invalid_regex_is_reported_by_line_number(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = _vault(repo)
    denylist = tmp_path / "deny.txt"
    denylist.write_text("fine-term\n(unclosed\n", encoding="utf-8")

    result = _run(root, "--denylist", str(denylist))

    assert result.returncode == 2
    assert any("line 2" in line for line in _lines(result))


def test_a_missing_core_vault_is_exit_2(tmp_path):
    result = _run(tmp_path)

    assert result.returncode == 2
    assert any(line.startswith("gate could not run:") for line in _lines(result))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unexpected_exception_is_exit_2_not_a_traceback(tmp_path):
    root = _vault(tmp_path)
    locked = root / "core-vault" / "locked"
    locked.mkdir()
    (locked / "note.md").write_text("plain\n", encoding="utf-8")
    locked.chmod(0)
    try:
        result = _run(root)
    finally:
        locked.chmod(0o755)

    assert result.returncode == 2
    assert any(line.startswith("gate could not run:") for line in _lines(result))
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, "core-vault/<redacted-name>:1:commit-id"),
        ({"GITHUB_ACTIONS": "true"}, "::error file=core-vault/<redacted-name>,line=1::commit-id"),
    ],
    ids=["plain", "annotation"],
)
def test_a_deny_list_term_in_a_filename_is_never_echoed(tmp_path, env, expected):
    root = _vault(tmp_path / "r")
    _write(root, f"note-{DENY_TERM.swapcase()}.md", f"commit {COMMIT}\n")

    result = _run(root, *_deny(tmp_path), **env)

    assert result.returncode == 1
    assert expected in _lines(result)
    _no_echo(result, DENY_TERM)


def test_a_deny_list_term_in_an_undecodable_filename_is_not_echoed_in_exit_2(tmp_path):
    root = _vault(tmp_path / "r")
    (root / "core-vault" / f"note-{DENY_TERM.swapcase()}.bin").write_bytes(b"caf\xe9\n")

    result = _run(root, *_deny(tmp_path))

    assert result.returncode == 2
    assert any(line.startswith("gate could not run:") and REDACTED in line for line in _lines(result))
    _no_echo(result, DENY_TERM)


@pytest.mark.parametrize("case", ["email", "home-drive-backslash", "credential", "commit-id"])
def test_a_filename_that_trips_a_class_is_itself_a_finding(tmp_path, case):
    cls, token = CASES[case]
    root = _vault(tmp_path / "r")
    name = token
    _write(root, f"sub/{name}.md", "clean\n")

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"core-vault/sub/{REDACTED}:0:{cls}" in _lines(result)
    _no_echo(result, name)


def test_a_filename_holding_a_deny_list_term_is_itself_a_finding(tmp_path):
    root = _vault(tmp_path / "r")
    _write(root, f"{DENY_TERM}.md", "clean\n")

    result = _run(root, *_deny(tmp_path))

    assert result.returncode == 1
    assert f"core-vault/{REDACTED}:0:deny-list#1" in _lines(result)


def test_a_clean_filename_with_clean_content_still_passes(tmp_path):
    root = _vault(tmp_path / "r")
    _write(root, "sub/plain-name.md", "clean\n")

    assert _run(root, *_deny(tmp_path)).returncode == 0


def test_a_hostile_filename_yields_one_well_formed_annotation_per_finding(tmp_path):
    root = _vault(tmp_path / "r")
    _write(root, "a\n::error::injected,x.md", f"commit {COMMIT}\n")

    result = _run(root, GITHUB_ACTIONS="true")

    assert result.returncode == 1
    annotations = [line for line in _lines(result) if line.startswith("::error")]
    assert annotations == ["::error file=core-vault/a%0A%3A%3Aerror%3A%3Ainjected%2Cx.md,line=1::commit-id"]
    assert [
        line for line in _lines(result) if line.startswith("::") and not line.startswith("::warning")
    ] == annotations


def test_a_percent_in_a_filename_is_escaped_in_the_annotation(tmp_path):
    root = _vault(tmp_path / "r")
    _write(root, "100%.md", f"commit {COMMIT}\n")

    result = _run(root, GITHUB_ACTIONS="true")

    assert "::error file=core-vault/100%25.md,line=1::commit-id" in _lines(result)


def test_a_missing_capture_scan_is_exit_2_not_a_traceback(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts" / "core-content-gate").write_bytes(_GATE.read_bytes())

    result = _run_in(checkout, _vault(tmp_path / "r"))

    assert result.returncode == 2
    assert any(line.startswith("gate could not run:") for line in _lines(result))
    assert "Traceback" not in result.stderr


def test_the_scrub_functions_apply_a_supplied_deny_list():
    gate = _load_gate()
    denylist = [(4, re.compile(DENY_TERM, re.IGNORECASE))]

    assert gate.scrub_line(f"a {DENY_TERM.upper()}", denylist=denylist) == ["deny-list#4"]
    assert gate.scrub_line("plain", denylist=denylist) == []
    assert gate.scrub_line(f"a {DENY_TERM}") == []
    assert gate.scrub_text(f"ok\n{DENY_TERM}", denylist=denylist) == [(2, "deny-list#4")]


@pytest.mark.parametrize(
    "line, expected",
    [
        (f"mail {EMAIL}", ["email"]),
        (f"id {COMMIT} and mail {EMAIL}", ["commit-id", "email"]),
        ("nothing to see", []),
    ],
    ids=["email", "two-classes", "clean"],
)
def test_the_scrub_function_returns_classes_without_the_text(line, expected):
    gate = _load_gate()

    hits = gate.scrub_line(line)

    assert sorted(hits) == expected


def test_the_repos_own_core_vault_passes_the_gate():
    result = _run(_REPO_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr


# --- validation and pointer check -------------------------------------------

_VALIDATOR_REL = Path("tools/lore/plugins/lore/bin/lore")
_CAPTURE_SCAN_REL = Path("tools/craft/plugins/craft/scripts/capture_scan.py")


def _sidecar(kind: str, name: str, **extra) -> str:
    return json.dumps(
        {
            "kind": kind,
            "title": name,
            "status": "active",
            "created-at": "2026-01-01T00:00:00Z",
            "updated-at": "2026-03-01T00:00:00Z",
            **extra,
        }
    )


def _record(base: Path, kind: str, name: str, body: str = "text\n", **extra) -> None:
    folder = base / kind
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(body, encoding="utf-8")
    (folder / f"{name}.json").write_text(_sidecar(kind, name, **extra), encoding="utf-8")


def _core_record(root: Path, kind: str, name: str, body: str = "text\n", **extra) -> None:
    _record(root / "core-vault", kind, name, body, **extra)


def _ship(root: Path, rel: str, text: str) -> Path:
    path = root / "tools" / "t" / "plugins" / "p" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fake_checkout(tmp_path: Path, lore: str | None) -> Path:
    """A checkout holding the gate, capture_scan and a stand-in lore wrapper (None: no lore at all)."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts" / "core-content-gate").write_bytes(_GATE.read_bytes())
    scan = checkout / _CAPTURE_SCAN_REL
    scan.parent.mkdir(parents=True)
    scan.write_bytes((_REPO_ROOT / _CAPTURE_SCAN_REL).read_bytes())
    if lore is not None:
        wrapper = checkout / _VALIDATOR_REL
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text(lore, encoding="utf-8")
    return checkout


def test_a_core_record_with_an_unparseable_sidecar_fails_with_the_validators_line(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "blob", "torn")
    (root / "core-vault" / "blob" / "torn.json").write_text("{not json", encoding="utf-8")

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert any(line.startswith("path blob/torn: sidecar-malformed: ") for line in _lines(result))


def test_a_related_link_that_exists_only_outside_core_vault_is_missing_target(tmp_path):
    root = _vault(tmp_path)
    _record(tmp_path / "elsewhere", "blob", "outside")
    _core_record(root, "blob", "src", related={"blob": ["outside"]})

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert any(line.startswith("path blob/src: missing-target: ") for line in _lines(result))


def test_a_readme_only_core_vault_passes_and_says_so(tmp_path):
    root = _vault(tmp_path)
    _write(root, "README.md", "# core\n")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok: core vault holds no records yet" in _lines(result)


def test_records_only_in_a_misfiled_folder_fail_with_both_codes(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "lessons", "good")

    result = _run(root)

    codes = {line.split(": ")[1] for line in _lines(result) if line.startswith("path")}
    assert result.returncode == 1, result.stdout + result.stderr
    assert {"misfiled-folder", "no-records"} <= codes
    assert "ok: core vault holds no records yet" not in _lines(result)


def test_a_valid_record_with_a_valid_link_passes(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "blob", "target")
    _core_record(root, "blob", "src", body="see [[blob/target]]\n")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok: core vault holds no records yet" not in _lines(result)


@pytest.mark.parametrize("leak", ["commit-id", "deny-term"])
def test_a_validator_line_carrying_a_leak_is_relayed_cut_down(tmp_path, leak):
    root = _vault(tmp_path)
    token = COMMIT if leak == "commit-id" else DENY_TERM.lower()
    _core_record(root, "blob", "src", body=f"see [[blob/{token}]]\n")

    result = _run(root, *_deny(tmp_path), GITHUB_ACTIONS="true")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "blob/src: missing-target" in _lines(result)
    _no_echo(result, token)


def test_a_validator_line_carrying_an_email_is_relayed_cut_down(tmp_path):
    lore = f"#!/bin/sh\necho 'path blob/src: missing-target: related → {EMAIL}'\nexit 1\n"
    checkout = _fake_checkout(tmp_path, lore)

    result = _run_in(checkout, _vault(tmp_path / "r"), GITHUB_ACTIONS="true")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "blob/src: missing-target" in _lines(result)
    _no_echo(result, EMAIL)


def test_a_record_id_that_itself_trips_a_class_is_redacted_in_the_cut_line(tmp_path):
    lore = f"#!/bin/sh\necho 'path blob/{COMMIT}: missing-target: related → {EMAIL}'\nexit 1\n"
    checkout = _fake_checkout(tmp_path, lore)

    result = _run_in(checkout, _vault(tmp_path / "r"))

    assert f"{REDACTED}: missing-target" in _lines(result)
    _no_echo(result, COMMIT)


def test_a_clean_validator_line_is_relayed_whole(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "blob", "src", body="see [[blob/absent]]\n")

    result = _run(root)

    assert "path blob/src: missing-target: wikilink → blob/absent: no such record" in _lines(result)


_OK_LORE = (
    "#!/bin/sh\n"
    "echo 'path: no-records: the directory holds no records'\n"
    "echo 'lore: validated 0 records in 1 vaults: 1 findings (no-records 1)'\n"
    "exit 1\n"
)


def test_the_stand_in_lore_with_only_no_records_passes(tmp_path):
    checkout = _fake_checkout(tmp_path, _OK_LORE)

    result = _run_in(checkout, _vault(tmp_path / "r"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok: core vault holds no records yet" in _lines(result)


@pytest.mark.parametrize(
    "extra",
    ["path: misfiled-folder: lessons holds records", "path blob/x: invalid-sidecar: bad"],
    ids=["vault-level", "record-level"],
)
def test_no_records_plus_any_other_code_fails(tmp_path, extra):
    summary = "lore: validated 0 records in 1 vaults: 2 findings"
    lore = f"#!/bin/sh\necho 'path: no-records: x'\necho '{extra}'\necho '{summary}'\nexit 1\n"
    checkout = _fake_checkout(tmp_path, lore)

    result = _run_in(checkout, _vault(tmp_path / "r"))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ok: core vault holds no records yet" not in _lines(result)


def test_a_record_level_no_records_line_is_not_the_carve_out(tmp_path):
    lore = "#!/bin/sh\necho 'path blob/x: no-records: x'\nexit 1\n"
    checkout = _fake_checkout(tmp_path, lore)

    result = _run_in(checkout, _vault(tmp_path / "r"))

    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "lore",
    [
        "#!/bin/sh\necho 'path: no-records: x'\nexit 2\n",
        "#!/bin/sh\necho 'path: no-records: x'\nexit 127\n",
        "#!/bin/sh\nexit 127\n",
        None,
        "#!/bin/sh\necho 'something unexpected'\nexit 1\n",
        "#!/bin/sh\nexit 1\n",
        "#!/bin/sh\necho 'path: no-records: x'\nexit 0\n",
        "#!/bin/sh\nsleep 0\nprintf 'path: no-records: x\\n\\377\\n'\nexit 1\n",
    ],
    ids=[
        "exit-2",
        "exit-127",
        "not-runnable",
        "missing",
        "unrecognised",
        "exit-1-no-lines",
        "exit-0-with-finding",
        "undecodable",
    ],
)
def test_a_validator_that_cannot_be_trusted_is_gate_exit_2(tmp_path, lore):
    checkout = _fake_checkout(tmp_path, lore)

    result = _run_in(checkout, _vault(tmp_path / "r"))

    assert result.returncode == 2, result.stdout + result.stderr
    assert any(line.startswith("gate could not run:") for line in _lines(result))
    assert "Traceback" not in result.stderr


def test_lore_runs_with_a_fenced_environment(tmp_path):
    seen = tmp_path / "seen.txt"
    lore = f"#!/bin/sh\nenv > '{seen}'\necho 'path: no-records: x'\nexit 1\n"
    checkout = _fake_checkout(tmp_path, lore)
    home = tmp_path / "live-home"

    result = _run_in(
        checkout,
        _vault(tmp_path / "r"),
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "cfg"),
        LORE_VAULT="/live/vault",
        LORE_GROUPS_DIR="/live/groups",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    seen_env = dict(line.split("=", 1) for line in seen.read_text().splitlines() if "=" in line)
    assert not any(k.startswith("LORE_") and k != "LORE_PUBLISH_DISABLE" for k in seen_env)
    assert seen_env["HOME"] != str(home)
    assert seen_env["XDG_CONFIG_HOME"] != str(home / "cfg")


def test_a_shipped_pointer_to_a_missing_record_fails(tmp_path):
    root = _vault(tmp_path)
    _ship(root, "doc.md", "intro\nrun `trailhead core show adr/missing` now\n")

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "tools/t/plugins/p/doc.md:2:pointer-missing:adr/missing" in _lines(result)


def test_a_shipped_pointer_to_an_existing_record_passes_and_is_counted(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "adr", "real")
    _ship(root, "doc.md", "run trailhead core show adr/real.\n")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pointers: 1 checked in 1 files" in _lines(result)


def test_the_count_covers_every_text_file_scanned_and_skips_binaries(tmp_path):
    root = _vault(tmp_path)
    _ship(root, "a.md", "nothing here\n")
    _ship(root, "sub/b.txt", "nothing here either\n")
    (root / "tools" / "t" / "plugins" / "p" / "blob.bin").write_bytes(b"\x00\x01trailhead core show adr/x\n")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pointers: 0 checked in 2 files" in _lines(result)


def test_a_record_with_only_one_of_its_two_files_is_a_missing_pointer(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "adr", "half")
    (root / "core-vault" / "adr" / "half.json").unlink()
    _ship(root, "doc.md", "trailhead core show adr/half\n")

    result = _run(root)

    assert "tools/t/plugins/p/doc.md:1:pointer-missing:adr/half" in _lines(result)


def test_the_documented_placeholder_is_not_a_pointer(tmp_path):
    root = _vault(tmp_path)
    _ship(root, "doc.md", "trailhead core show <kind>/<name>\nand a bare `trailhead core show`\n")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pointers: 0 checked in 1 files" in _lines(result)


@pytest.mark.parametrize(
    "text",
    [
        "trailhead core show ../outside/x",
        "trailhead core show Adr/real",
        "trailhead core show adr/real_two",
        "trailhead core show adr/",
        "trailhead core show adr/missing.md",
        "trailhead core show adr/Missing",
        "trailhead core show adr/foo_bar",
    ],
    ids=["traversal", "uppercase", "underscore-tail", "no-name", "md-suffix", "capital-name", "underscore"],
)
def test_a_pointer_the_runtime_grammar_refuses_is_a_finding(tmp_path, text):
    root = _vault(tmp_path)
    _ship(root, "doc.md", "intro\n" + text + "\n")

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "tools/t/plugins/p/doc.md:2:pointer-invalid" in _lines(result)
    assert "pointers: 1 checked in 1 files" in _lines(result)
    assert text.split("show ")[1] not in result.stdout + result.stderr


def test_the_gate_and_the_runtime_agree_on_the_id_grammar():
    from trailhead import core_vault

    gate = _load_gate()
    corpus = [
        "adr/real",
        "adr/a-b-c",
        "a1/b2",
        "adr/0-lead",
        "adr/-lead",
        "Adr/x",
        "adr/X",
        "adr",
        "adr/",
        "/x",
        "1adr/x",
        "adr/x/y",
        "adr/x_y",
        "adr/x.y",
        "../x/y",
        "adr/..",
        "",
        "adr/x\n",
    ]

    for candidate in corpus:
        assert bool(gate._RECORD_ID.fullmatch(candidate)) == bool(core_vault._RECORD_ID.fullmatch(candidate)), candidate


def test_a_pointer_through_a_symlinked_kind_folder_leaving_core_vault_fails(tmp_path):
    root = _vault(tmp_path)
    _record(tmp_path / "outside", "adr", "real")
    (root / "core-vault" / "adr").symlink_to(tmp_path / "outside" / "adr")
    _ship(root, "doc.md", "trailhead core show adr/real\n")

    result = _run(root)

    assert result.returncode == 1
    assert "tools/t/plugins/p/doc.md:1:pointer-missing:adr/real" in _lines(result)


def test_a_pointer_whose_files_are_symlinks_to_files_outside_core_vault_fails(tmp_path):
    root = _vault(tmp_path)
    _record(tmp_path / "outside", "adr", "real")
    (root / "core-vault" / "adr").mkdir()
    for suffix in (".md", ".json"):
        (root / "core-vault" / "adr" / f"real{suffix}").symlink_to(tmp_path / "outside" / "adr" / f"real{suffix}")
    _ship(root, "doc.md", "trailhead core show adr/real\n")

    result = _run(root)

    assert "tools/t/plugins/p/doc.md:1:pointer-missing:adr/real" in _lines(result)


def test_a_pointer_id_holding_a_deny_list_term_is_not_echoed(tmp_path):
    root = _vault(tmp_path)
    _ship(root, "doc.md", f"trailhead core show adr/{DENY_TERM.lower()}\n")

    result = _run(root, *_deny(tmp_path), GITHUB_ACTIONS="true")

    assert result.returncode == 1
    assert f"tools/t/plugins/p/doc.md:1:pointer-missing:{REDACTED}" in _lines(result)
    _no_echo(result, DENY_TERM.lower())


def test_a_pointer_finding_is_annotated_under_github_actions(tmp_path):
    root = _vault(tmp_path)
    _ship(root, "doc.md", "trailhead core show adr/missing\n")

    result = _run(root, GITHUB_ACTIONS="true")

    assert "::error file=tools/t/plugins/p/doc.md,line=1::pointer-missing%3Aadr/missing" not in _lines(result)
    assert "::error file=tools/t/plugins/p/doc.md,line=1::pointer-missing:adr/missing" in _lines(result)


def test_a_relayed_validator_line_is_annotated_under_github_actions(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "blob", "src", body="see [[blob/absent]]\n")

    result = _run(root, GITHUB_ACTIONS="true")

    annotations = [line for line in _lines(result) if line.startswith("::error")]
    assert any("blob/src: missing-target" in line for line in annotations)


def test_a_scrub_finding_and_a_pointer_finding_are_both_reported_in_one_run(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"mail {EMAIL}\n")
    _ship(root, "doc.md", "trailhead core show adr/missing\n")

    result = _run(root)

    assert result.returncode == 1
    assert "core-vault/note.md:1:email" in _lines(result)
    assert "tools/t/plugins/p/doc.md:1:pointer-missing:adr/missing" in _lines(result)


def test_a_scrub_finding_a_validator_finding_and_a_pointer_finding_are_all_reported(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "blob", "src", body=f"mail {EMAIL} [[blob/absent]]\n")
    _ship(root, "doc.md", "trailhead core show adr/missing\n")

    result = _run(root)

    lines = _lines(result)
    assert result.returncode == 1
    assert "core-vault/blob/src.md:1:email" in lines
    assert any(line.startswith("path blob/src: missing-target") for line in lines)
    assert "tools/t/plugins/p/doc.md:1:pointer-missing:adr/missing" in lines


def test_a_pointer_whose_file_is_a_symlink_staying_inside_core_vault_still_fails(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "adr", "other")
    for suffix in (".md", ".json"):
        (root / "core-vault" / "adr" / f"real{suffix}").symlink_to(f"other{suffix}")
    _ship(root, "doc.md", "trailhead core show adr/real\n")

    result = _run(root)

    assert "tools/t/plugins/p/doc.md:1:pointer-missing:adr/real" in _lines(result)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unreadable_plugin_folder_is_exit_2(tmp_path):
    root = _vault(tmp_path)
    locked = root / "tools" / "x" / "plugins" / "x" / "locked"
    locked.mkdir(parents=True)
    (locked / "doc.md").write_text("trailhead core show adr/missing\n", encoding="utf-8")
    locked.chmod(0)
    try:
        result = _run(root)
    finally:
        locked.chmod(0o755)

    assert result.returncode == 2, result.stdout + result.stderr
    assert any(line.startswith("gate could not run:") for line in _lines(result))
    assert "pointer-missing" not in result.stdout


def test_a_line_number_counts_only_newlines(tmp_path):
    root = _vault(tmp_path)
    _write(root, "a.md", "one\x0c\u2028\x85two\r\nthree " + EMAIL + "\n")

    result = _run(root)

    assert "core-vault/a.md:2:email" in _lines(result)


@pytest.mark.parametrize("env", [{}, {"GITHUB_ACTIONS": "true"}], ids=["plain", "github"])
def test_a_deny_term_spanning_a_path_separator_is_not_echoed(tmp_path, env):
    root = _vault(tmp_path)
    left, right = "Orchid", "Quartz"
    _write(root, f"{left.lower()}/{right.lower()}.md", EMAIL + "\n")
    denylist = tmp_path / "deny.txt"
    denylist.write_text(f"{left}.?{right}\n", encoding="utf-8")

    result = _run(root, "--denylist", str(denylist), **env)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "core-vault/<redacted-path>" in result.stdout
    _no_echo(result, left.lower() + right.lower())


def test_a_misnested_record_pair_blocks_the_no_records_pass(tmp_path):
    root = _vault(tmp_path)
    _record(root / "core-vault" / "blob", "sub", "y")
    lore = _OK_LORE
    checkout = _fake_checkout(tmp_path, lore)

    result = _run_in(checkout, root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "core-vault/blob/sub/y.md:0:unvalidated-record" in _lines(result)
    assert "ok: core vault holds no records yet" not in _lines(result)


def test_a_record_pair_directly_in_core_vault_blocks_the_no_records_pass(tmp_path):
    root = _vault(tmp_path)
    _write(root, "y.md", "text\n")
    _write(root, "y.json", "{}")
    checkout = _fake_checkout(tmp_path, _OK_LORE)

    result = _run_in(checkout, root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "core-vault/y.md:0:unvalidated-record" in _lines(result)


def test_a_readme_beside_nothing_still_passes_the_no_records_carve_out(tmp_path):
    root = _vault(tmp_path)
    _write(root, "README.md", "# core\n")
    _write(root, "notes/lone.md", "no sidecar\n")
    checkout = _fake_checkout(tmp_path, _OK_LORE)

    result = _run_in(checkout, root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok: core vault holds no records yet" in _lines(result)


def _ship_at(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_clean_deep_shipped_path_is_printed_verbatim_on_a_pointer_finding(tmp_path):
    root = _vault(tmp_path)
    rel = "tools/craft/plugins/craft/skills/_shared/slice.md"
    _ship_at(root, rel, "trailhead core show adr/missing\n")

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{rel}:1:pointer-missing:adr/missing" in _lines(result)


def test_a_shipped_path_that_trips_the_deny_list_only_across_segments_is_redacted(tmp_path):
    root = _vault(tmp_path)
    left, right = "Orchid", "Quartz"
    _ship_at(root, f"tools/t/plugins/p/{left.lower()}/{right.lower()}.md", "trailhead core show adr/missing\n")
    denylist = tmp_path / "deny.txt"
    denylist.write_text(f"{left}.?{right}\n", encoding="utf-8")

    result = _run(root, "--denylist", str(denylist))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "<redacted-path>:1:pointer-missing:adr/missing" in _lines(result)
    _no_echo(result, left.lower() + right.lower())


def test_misplaced_record_pairs_are_findings_beside_a_valid_record(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "adr", "real")
    _record(root / "core-vault" / "blob", "sub", "deep")
    _write(root, "top.md", "text\n")
    _write(root, "top.json", "{}")

    result = _run(root)

    lines = _lines(result)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "core-vault/top.md:0:unvalidated-record" in lines
    assert "core-vault/blob/sub/deep.md:0:unvalidated-record" in lines
    assert not any("adr/real.md" in line for line in lines)


def test_a_valid_record_alone_is_not_an_unvalidated_record(tmp_path):
    root = _vault(tmp_path)
    _core_record(root, "adr", "real")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("unvalidated-record" in line for line in _lines(result))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.parametrize("locked", ["tools/x", "tools/x/plugins"])
def test_an_unreadable_tool_or_plugins_folder_is_exit_2(tmp_path, locked):
    root = _vault(tmp_path)
    (root / "tools" / "x" / "plugins" / "y").mkdir(parents=True)
    target = root / locked
    target.chmod(0)
    try:
        result = _run(root)
    finally:
        target.chmod(0o755)

    assert result.returncode == 2, result.stdout + result.stderr
    assert any(line.startswith("gate could not run:") and "listing a tools/ folder" in line for line in _lines(result))
    assert not any(line.startswith("pointers:") for line in _lines(result))


def test_a_repo_root_with_no_plugins_is_exit_2(tmp_path):
    (tmp_path / "core-vault").mkdir()

    result = _run(tmp_path)

    assert result.returncode == 2, result.stdout + result.stderr
    assert any(line.startswith("gate could not run:") for line in _lines(result))
    assert not any(line.startswith("pointers:") for line in _lines(result))


@pytest.mark.parametrize(
    "token",
    [
        "run trailhead core show to read the record",
        "trailhead core show <id> prints it",
        "trailhead core show $id",
        "trailhead core show {record_id}",
        "trailhead core show `<kind>/<name>`",
        'trailhead core show "adr/real"',
        "trailhead core show `adr/real`",
        "trailhead core show [adr/real]",
        "trailhead core show (adr/real)",
        "trailhead core show 'adr/real'.",
    ],
    ids=["prose", "angle", "dollar", "brace", "wrapped", "dquote", "backtick", "bracket", "paren", "squote"],
)
def test_prose_placeholders_and_wrapped_valid_pointers_are_not_invalid(tmp_path, token):
    root = _vault(tmp_path)
    _core_record(root, "adr", "real")
    _ship(root, "doc.md", token + "\n")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("pointer-invalid" in line for line in _lines(result))


@pytest.mark.parametrize("wrap", ["{}", "`{}`", '"{}"', "[{}]"], ids=["bare", "backtick", "dquote", "bracket"])
def test_a_wrapped_pointer_is_still_checked_for_existence(tmp_path, wrap):
    root = _vault(tmp_path)
    _ship(root, "doc.md", "trailhead core show " + wrap.format("adr/absent") + "\n")

    result = _run(root)

    assert "tools/t/plugins/p/doc.md:1:pointer-missing:adr/absent" in _lines(result)


@pytest.mark.parametrize("attempt", ["Lesson/Foo_Bar", "`Lesson/Foo_Bar`", "adr/a/b"], ids=["bare", "wrapped", "deep"])
def test_a_malformed_attempted_id_still_fails(tmp_path, attempt):
    root = _vault(tmp_path)
    _ship(root, "doc.md", "trailhead core show " + attempt + "\n")

    result = _run(root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "tools/t/plugins/p/doc.md:1:pointer-invalid" in _lines(result)


@pytest.mark.parametrize("pin", ["actions/checkout@v4.1.0", "pytest@8.3.2", "pkg@1.2.3-rc.1"])
def test_a_version_pin_is_not_an_email(tmp_path, pin):
    root = _vault(tmp_path)
    _write(root, "note.md", f"pin {pin} here\n")

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "domain", ["example.org", "mail.example.co", "ex.io"], ids=["two-label", "three-label", "two-letter-final"]
)
def test_a_runtime_built_email_is_still_an_email(tmp_path, domain):
    root = _vault(tmp_path)
    _write(root, "note.md", "mail " + "bo" + "b" + "@" + domain + " now\n")

    result = _run(root)

    assert "core-vault/note.md:1:email" in _lines(result)
