"""The tidy-followups write-path eval scrubs every capture it writes, so a committed
`runs/` tree passes the repo-wide credential scan.

The scrub is exercised on a synthetic capture carrying each shape that tripped the
scan: snapshot and run-dir paths, the developer's repo path, git SHAs and lore
revision hashes, `SECRET_KEY=`-style assignments, and the phrase "bearer tokens".
Its output is put to `capture_scan.py`, the same consumer the committed-captures
test uses.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


_CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
_CASE = _CRAFT / "evals" / "tidy-followups-builds-one-parent"
_SCANNER = _CRAFT / "scripts" / "capture_scan.py"

_spec = importlib.util.spec_from_file_location("tidy_followups_scrub", _CASE / "scrub.py")
scrub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scrub)

_SHA = "a58e11b9b8f70c14c28db374dbdb01af3eb4ace77ce6ffe3d622ade33aa3e166"
_GIT = "ce19d0cec6b8913e7c12ccbb159abe7d926b72d9"
_SKILL = "tools/craft/plugins/craft/skills/tidy-followups/SKILL.md"

_CAPTURE = "\n".join(
    [
        f"[main] BASH cat $WORK/snap-base/{_SKILL}",
        f"[main] BASH cat $WORK/snap-treat/{_SKILL}",
        "[main] BASH python3 $WORK/snap-treat/tools/craft/plugins/craft/scripts/followup_gather.py spec",
        "[main] BASH $WORK/snap-base/tools/lore/plugins/lore/bin/lore; ls $WORK/snap-treat/trailhead/area",
        "[main] BASH ls $WORK/runs/chosen-treat/fixture/vault/task/distilled-fixture-follow-up",
        "$HOME/.local/state/camp/trailhead/worktrees/slice-cleanup/trailhead/scripts/eval-sandbox"
        " $WORK/runs/chosen-base -- env -i",
        f"revision {_SHA} and base {_GIT}",
        "Read _shared/security.md: pattern `SECRET_KEY=`, then SECRET = \"hunter2-signing-secret\"",
        "follow-up: stop writing bearer tokens to the request trace",
    ]
)


def _scan(tmp_path: Path, text: str) -> subprocess.CompletedProcess:
    f = tmp_path / "capture.txt"
    f.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_SCANNER), str(f)], capture_output=True, text=True, timeout=60
    )


def test_the_unscrubbed_synthetic_capture_trips_the_scan(tmp_path):
    result = _scan(tmp_path, _CAPTURE)
    assert result.returncode == 1, result.stdout


def test_a_scrubbed_capture_with_every_flagged_shape_passes_the_scan(tmp_path):
    result = _scan(tmp_path, scrub.scrub_text(_CAPTURE))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_scrub_keeps_the_arm_distinguishable():
    out = scrub.scrub_text(_CAPTURE).splitlines()
    assert out[0] != out[1]
    assert "$CRAFT[base]" in out[0] and "$CRAFT[treat]" in out[1]
    assert "$LORE[base]" in out[3] and "$SNAP[treat]:trailhead/area" in out[3]
    assert "$RUN[chosen-treat]/fixture" in out[4]
    assert "$RUN[chosen-base]" in out[5]


def test_the_scrub_leaves_a_plugin_relative_path_a_reader_can_follow():
    out = scrub.scrub_text(_CAPTURE).splitlines()[1]
    assert "$CRAFT[treat]/skills/tidy-followups/SKILL.md" in out


def test_a_hash_becomes_a_stable_placeholder_that_still_tells_two_hashes_apart():
    first = scrub.scrub_text(f"rev {_SHA}")
    again = scrub.scrub_text(f"rev {_SHA} twice")
    other = scrub.scrub_text(f"rev {_GIT}")
    assert _SHA not in first
    assert first.split()[1] == again.split()[1]
    assert first.split()[1] != other.split()[1]


def test_a_credential_class_the_rewrites_do_not_cover_is_redacted_with_its_class(tmp_path):
    text = "Authorization: Bearer abc123.def456-ghi789 and leaked ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    out = scrub.scrub_text(text)
    assert "[redacted:bearer]" in out
    assert "[redacted:vendor-prefix]" in out
    assert _scan(tmp_path, out).returncode == 0


def test_text_with_nothing_to_scrub_comes_back_unchanged():
    text = "[main] TEXT\nrefining task/some-follow-up (1 of 3)\nusing $WORK and $HOME as written\n"
    assert scrub.scrub_text(text) == text


def test_real_prefixes_are_rewritten_to_the_dollar_forms():
    out = scrub.scrub_text(
        "/private/tmp/w/snap-treat/tools/craft/x /Users/me/.local/bin/claude me",
        work="/private/tmp/w",
        home="/Users/me",
        user="me",
    )
    assert "/private/tmp" not in out and "/Users/me" not in out
    assert "$HOME/.local/bin/claude $USER" in out


def test_scrub_value_scrubs_string_leaves_and_keeps_structure(tmp_path):
    doc = {"order": {"scope": 12}, "ok": True, "lines": [{"line": f"saw {_SHA}", "n": 1}], "k": None}
    out = scrub.scrub_value(doc)
    assert out["order"] == {"scope": 12} and out["ok"] is True and out["k"] is None
    assert out["lines"][0]["n"] == 1
    assert _SHA not in json.dumps(out)
    assert _scan(tmp_path, json.dumps(out, indent=1)).returncode == 0


def test_extract_writes_scrubbed_captures(tmp_path):
    meta, out = tmp_path / "meta", tmp_path / "out"
    meta.mkdir()
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": f"saw revision {_SHA}"},
            {"type": "tool_use", "name": "Bash", "input": {"command": f"cat {tmp_path}/snap-treat/{_SKILL}"}},
        ]}},
        {"type": "result", "result": "follow-up: stop writing bearer tokens to the trace"},
    ]
    (meta / "c-treat.stream.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    state = {"vault": {}, "repo": {"head": _GIT, "status": "", "tree": {}}}
    for phase in ("before", "after"):
        (meta / f"c-treat.{phase}.json").write_text(json.dumps(state))
    done = subprocess.run(
        [sys.executable, str(_CASE / "extract.py"), str(meta), "c-treat", str(out)],
        capture_output=True,
        text=True,
        env={"HOME": "/Users/nobody", "PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr
    captures = sorted(out.glob("c-treat.*"))
    assert len(captures) == 4
    scan = subprocess.run(
        [sys.executable, str(_SCANNER), str(out)], capture_output=True, text=True, timeout=60
    )
    assert scan.returncode == 0, scan.stdout
    assert "$CRAFT[treat]/skills/tidy-followups/SKILL.md" in (out / "c-treat.commands.txt").read_text()
    assert str(tmp_path) not in (out / "c-treat.commands.txt").read_text()
