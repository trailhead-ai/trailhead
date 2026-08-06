"""Tests for camp.bookmark.resume — the `camp resume <ref>` command.

`camp resume` never runs anything. It prints a two-line machine contract on
stdout for the shell wrapper to act on:

    line 1  the absolute directory the wrapper must cd into
    line 2  the command to exec there, quoted for a POSIX shell

Test contract:
- The happy path emits EXACTLY those two lines; line 2 round-trips through
  shlex.split back to the harness's argv token list.
- Metacharacter-bearing session ids and workspace paths still produce a single
  safe line each — nothing a shell would re-interpret escapes quoting.
- Every failure mode (shell integration absent, unknown ref, workspace gone,
  transcript pruned, harness cannot resume) exits non-zero having printed NO
  machine line, so a wrapper can never act on a half-answer.
- Resuming does not mutate the bookmark.

Every test injects CAMP_STATE_DIR so the real ~/.local/state/camp is never
touched.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def group() -> dict:
    return {"group": {"name": "demo"}}


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    """A hermetic env with the shell integration marker present.

    CAMP_CONFIG_DIR is injected too: resume resolves the harness from the config
    of the group named on the bookmark, and no test may reach the developer's own
    group configs to do it.
    """
    return {
        "CAMP_STATE_DIR": str(tmp_path / "state"),
        "CAMP_CONFIG_DIR": str(tmp_path / "config"),
        "CAMP_SHELL_INTEGRATION": "1",
    }


def _write_group_config(env: dict[str, str], name: str, *, binary: str) -> None:
    """Configure the group *name* to run the harness binary *binary*."""
    groups_dir = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    (groups_dir / f"{name}.toml").write_text(
        f'[group]\nname = "{name}"\n\n'
        '[[members]]\nname = "repo_a"\nrepo_root = "/nonexistent/repo"\n\n'
        f'[harness]\nbinary = "{binary}"\n'
    )


def _workspace(env: dict[str, str], group: str, slug: str) -> Path:
    from camp.group.manifest import workspace_dir

    return workspace_dir(group, slug, env=env)


def _seed(
    env: dict[str, str],
    *,
    ref: str = "alpha",
    group: str = "demo",
    slug: str = "alpha",
    session_id: str = "sess-alpha",
    tmp_path: Path,
) -> dict:
    """Store a bookmark whose workspace dir and transcript both exist on disk."""
    from camp.bookmark.store import upsert

    workspace = _workspace(env, group, slug)
    workspace.mkdir(parents=True, exist_ok=True)
    transcript = tmp_path / f"{session_id}.jsonl"
    transcript.write_text("{}\n")
    return upsert(
        {
            "ref": ref,
            "group": group,
            "slug": slug,
            "session_id": session_id,
            "transcript_path": str(transcript),
            "note": "",
            "created_at": "2026-08-03T00:00:00Z",
            "updated_at": "2026-08-03T00:00:00Z",
        },
        env=env,
    )


# ---------------------------------------------------------------------------
# Happy path — the two-line machine contract
# ---------------------------------------------------------------------------


def test_emits_exactly_two_lines(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    cmd_resume(["alpha"], env)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2


def test_first_line_is_the_absolute_workspace_root(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    cmd_resume(["alpha"], env)

    first = capsys.readouterr().out.splitlines()[0]
    assert Path(first).is_absolute()
    assert Path(first) == _workspace(env, "demo", "alpha").resolve()


def test_second_line_round_trips_to_the_harness_argv(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Line 2 is a shell-quoted rendering of the seam's argv and nothing else."""
    from trailhead.harness import get_harness

    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    cmd_resume(["alpha"], env)

    second = capsys.readouterr().out.splitlines()[1]
    assert shlex.split(second) == get_harness("claude").session_resume("sess-alpha")


def test_resume_leaves_the_bookmark_unmodified(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume
    from camp.bookmark.store import get_by_ref

    before = _seed(env, tmp_path=tmp_path)
    cmd_resume(["alpha"], env)
    assert get_by_ref("alpha", env=env) == before


# ---------------------------------------------------------------------------
# Injection — a hostile id or path stays inside one quoted token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    ["a b", "a;touch pwned", "a$(whoami)", "a'b", 'a"b', "a`id`", "a|b", "a&b", "a>b"],
)
def test_metacharacter_workspace_path_stays_one_safe_line(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture, slug: str
) -> None:
    """A workspace path full of shell metacharacters still emits ONE cd line that
    a shell reading it as a literal path resolves back to the same directory."""
    from camp.bookmark.resume import cmd_resume

    _seed(env, slug=slug, tmp_path=tmp_path)
    cmd_resume(["alpha"], env)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert Path(lines[0]) == _workspace(env, "demo", slug).resolve()


def test_a_newline_in_the_workspace_path_is_refused(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A newline is the one character the line-oriented contract cannot carry:
    emitting it would silently turn one cd target into two lines. Refuse rather
    than hand a wrapper a path it would truncate."""
    from camp.bookmark.resume import cmd_resume

    _seed(env, slug="a\nb", tmp_path=tmp_path)
    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""


def test_a_metacharacter_session_id_is_refused_not_quoted(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The seam rejects an id that is not a plain token, so it never reaches
    line 2 at all — the strongest form of the injection guarantee."""
    from camp.bookmark.resume import cmd_resume

    _seed(env, session_id="sess;touch pwned", tmp_path=tmp_path)
    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""


# ---------------------------------------------------------------------------
# Failure modes — non-zero, and never a machine line on stdout
# ---------------------------------------------------------------------------


def test_missing_shell_integration_errors_with_shellenv_guidance(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    del env["CAMP_SHELL_INTEGRATION"]

    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "trailhead shellenv" in captured.err
    assert "eval" in captured.err


def test_unknown_ref_errors_naming_it(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    with pytest.raises(SystemExit) as exc:
        cmd_resume(["nope"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "nope" in captured.err


def test_missing_workspace_errors(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    _workspace(env, "demo", "alpha").rmdir()

    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "workspace" in captured.err


def test_pruned_transcript_errors_hinting_bookmark_rm(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    record = _seed(env, tmp_path=tmp_path)
    Path(record["transcript_path"]).unlink()

    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "camp bookmark rm alpha" in captured.err


def test_harness_without_resume_support_says_unsupported(
    env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A group whose harness binary camp does not recognize cannot be resumed.

    The harness is read off the group RECORDED ON THE BOOKMARK — resolved from
    that group's own config — so this holds no matter where the command is run.
    """
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    _write_group_config(env, "demo", binary="some-other-agent")

    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "resume unsupported for this harness" in captured.err


def test_seam_returning_none_says_unsupported(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recognized harness that answers None degrades to the same message —
    camp never falls back to composing an argv of its own."""
    from trailhead.harness import ClaudeCodeHarness

    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    monkeypatch.setattr(ClaudeCodeHarness, "session_resume", lambda self, sid: None)

    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "resume unsupported for this harness" in captured.err


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------


def test_requires_a_ref_argument(
    env: dict[str, str], group: dict, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    with pytest.raises(SystemExit) as exc:
        cmd_resume([], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""


def test_rejects_an_unexpected_extra_argument(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha", "extra"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "extra" in captured.err


def test_the_already_resolved_group_flag_is_not_a_stray_argument(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`camp resume <ref> --group <name>` reaches the handler with --group still
    in argv (the dispatcher resolved it but does not strip it). It selects the
    group; it is not an extra positional."""
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    cmd_resume(["alpha", "--group", "demo"], env)

    assert len(capsys.readouterr().out.splitlines()) == 2


def test_the_group_flag_is_accepted_in_equals_form_too(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from camp.bookmark.resume import cmd_resume

    _seed(env, tmp_path=tmp_path)
    cmd_resume(["--group=demo", "alpha"], env)

    assert len(capsys.readouterr().out.splitlines()) == 2


# ---------------------------------------------------------------------------
# A hand-edited record's slug/group cannot walk resume outside the state tree
# ---------------------------------------------------------------------------


def _seed_raw(env: dict[str, str], *, ref: str = "alpha", group: str = "demo", slug: str, tmp_path: Path) -> dict:
    """Store a bookmark whose (group, slug) is NOT re-derived through
    workspace_dir first — simulating a hand-edited store.json record whose
    slug never passed capture-time validation."""
    from camp.bookmark.store import upsert

    transcript = tmp_path / "sess.jsonl"
    transcript.write_text("{}\n")
    return upsert(
        {
            "ref": ref,
            "group": group,
            "slug": slug,
            "session_id": "sess-alpha",
            "transcript_path": str(transcript),
            "note": "",
            "created_at": "2026-08-03T00:00:00Z",
            "updated_at": "2026-08-03T00:00:00Z",
        },
        env=env,
    )


def test_a_traversal_slug_on_a_stored_record_is_a_clean_resume_refusal(
    env: dict[str, str], group: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A stored record with slug='../../../etc' must not resolve resume's cd
    target outside the camp state tree, and must not crash with a raw
    traceback — it is a clean, non-zero, no-stdout refusal like any other."""
    from camp.bookmark.resume import cmd_resume

    _seed_raw(env, slug="../../../etc", tmp_path=tmp_path)

    with pytest.raises(SystemExit) as exc:
        cmd_resume(["alpha"], env)

    captured = capsys.readouterr()
    assert exc.value.code != 0
    assert captured.out == ""
    assert "alpha" in captured.err
