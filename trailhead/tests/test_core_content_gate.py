"""The core-vault content gate, run as a subprocess against temporary repo roots.

Every forbidden token is assembled at runtime from parts: this file is itself
scanned by the leak gate and must never hold one as a literal.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
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


def _run(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GATE), "--repo-root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(**env),
    )


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "core-vault").mkdir()
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
    out = result.stdout + result.stderr
    assert f"core-vault/note.md:1:{cls}" in out
    echoed = [s for s in _substrings(token) if s in out]
    assert echoed == []


def test_no_echo_of_a_deny_list_term(tmp_path):
    root = _vault(tmp_path)
    _write(root, "note.md", f"mentions {DENY_TERM}\n")
    denylist = tmp_path / "deny.txt"
    denylist.write_text(DENY_TERM + "\n", encoding="utf-8")

    result = _run(root, "--denylist", str(denylist))

    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert [s for s in _substrings(DENY_TERM) if s in out] == []


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


def test_a_utf16_file_is_exit_2(tmp_path):
    root = _vault(tmp_path)
    (root / "core-vault" / "note.md").write_bytes(f"mail {EMAIL}\n".encode("utf-16"))

    result = _run(root)

    assert result.returncode == 2
    assert any(line.startswith("gate could not run:") for line in _lines(result))


@pytest.mark.parametrize(
    "payload",
    [b"\x89PNG\r\n\x1a\n\x00\x00\xff\xfe", b"valid text\x00with a NUL\n", b"caf\xe9\n"],
    ids=["not-utf8", "nul-in-utf8", "latin1"],
)
def test_a_binary_file_is_exit_2(tmp_path, payload):
    root = _vault(tmp_path)
    (root / "core-vault" / "image.bin").write_bytes(payload)

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
    out = result.stdout + result.stderr
    assert "core-vault/sub/<redacted-name>:1:commit-id" in _lines(result)
    assert [s for s in _substrings(EMAIL) if s in out] == []


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
    assert [s for s in _substrings(DENY_TERM) if s in out] == []
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


def _load_gate():
    loader = importlib.machinery.SourceFileLoader("core_content_gate_under_test", str(_GATE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
