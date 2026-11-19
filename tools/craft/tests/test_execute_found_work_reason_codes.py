"""Every `reason-code:` the execute procedure's "Found work" section names is one
`found_work.py` can emit. The expected set is `found_work.REASON_CODES`, read from the
module, so a renamed or retired code leaves the prose naming a token the script never
prints."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "craft"
_EXECUTE = _PLUGIN / "skills" / "_shared" / "execute.md"
_SCRIPT = _PLUGIN / "scripts" / "found_work.py"

sys.path.insert(0, str(_SCRIPT.parent))
_spec = importlib.util.spec_from_file_location("found_work_for_doc_consistency", _SCRIPT)
found_work = importlib.util.module_from_spec(_spec)
sys.modules["found_work_for_doc_consistency"] = found_work
_spec.loader.exec_module(found_work)

_NAMED = re.compile(r"`reason-code:\s+([^`\s]+)`")


def named_reason_codes(markdown: str, anchor: str | None = None) -> list[str]:
    """The reason-codes named in the Found work section, or, given `anchor`, in the one paragraph or
    list item that starts at it."""
    if anchor is None:
        flat = re.sub(r"\s+", " ", markdown)
        return _NAMED.findall(flat[flat.index("## Found work") : flat.index("## Blocked-Path Write")])
    start = markdown.index(anchor)
    end = re.search(r"\n\n|\n- ", markdown[start:])
    stop = start + end.start() if end else len(markdown)
    return _NAMED.findall(re.sub(r"\s+", " ", markdown[start:stop]))


def test_every_reason_code_the_prose_names_is_one_the_script_emits():
    named = named_reason_codes(_EXECUTE.read_text(encoding="utf-8"))
    assert named, "the Found work section names no reason-code"
    assert set(named) <= found_work.REASON_CODES, sorted(set(named) - found_work.REASON_CODES)


def test_a_code_the_script_does_not_emit_is_caught():
    text = "## Found work\n\nsee `reason-code: no-spec` and `reason-code:\nmade-up-code`.\n## Blocked-Path Write\n"
    named = named_reason_codes(text)
    assert named == ["no-spec", "made-up-code"]
    assert set(named) - found_work.REASON_CODES == {"made-up-code"}


_NON_UTF8 = b"\xff\xfe not utf-8"


def _reserved_prefix_document() -> str:
    return json.dumps(
        {
            "decision": "folded",
            "parent": "task/X",
            "spec": "spec/s",
            "finding": {
                "title": f"{found_work.SECURITY_TITLE_PREFIX} src/a.py:1",
                "what": "w",
                "where": ["src/a.py"],
                "found": "f",
                "security": False,
            },
            "reason": "r",
        }
    )


def _run_decide_reserved_prefix(monkeypatch):
    return found_work.main(["decide", "--session", "build"], stdin=_reserved_prefix_document(), runner=None)


def _run_non_utf8(subcommand):
    def run(monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(_NON_UTF8), encoding="utf-8"))
        return found_work.main([subcommand, "--session", "build"], runner=None)

    return run


def _run_report_with_an_unreadable_session(monkeypatch):
    def lore(argv, stdin=None):
        return subprocess.CompletedProcess(argv, 1, "", "error: vault config unreadable")

    return found_work.main(["report", "--parent", "task/p", "--session", "build"], runner=lore)


# The exit-2 refusals each subcommand can reach, with the input that triggers each. `entry` cannot
# reach `reserved-security-prefix`; `report` reaches `session-unreadable` only.
_TRIGGERS = {
    "decide": {
        "reserved-security-prefix": _run_decide_reserved_prefix,
        "non-utf8-stdin": _run_non_utf8("decide"),
    },
    "entry": {"non-utf8-stdin": _run_non_utf8("entry")},
    "report": {"session-unreadable": _run_report_with_an_unreadable_session},
}

# Where execute.md documents each subcommand's exit-2 refusals that print no finding.
_ANCHORS = {
    "decide": "Exit codes: `0` done",
    "entry": "Settling the entries folded into the parent (Phase 5)",
}


@pytest.mark.parametrize("code_name", sorted(found_work._EXIT_2_REFUSALS))
def test_an_exit_2_refusal_that_prints_no_finding_is_named_where_its_subcommand_is_documented(
    code_name, monkeypatch, capsys
):
    reaching = [sub for sub, codes in _TRIGGERS.items() if code_name in codes]
    assert reaching, f"no subcommand is known to reach {code_name}"
    text = _EXECUTE.read_text(encoding="utf-8")
    for subcommand in reaching:
        code = _TRIGGERS[subcommand][code_name](monkeypatch)
        out = capsys.readouterr()
        assert code == 2 and out.out == "" and f"reason-code: {code_name}" in out.err, subcommand
        if subcommand in _ANCHORS:
            named = named_reason_codes(text, anchor=_ANCHORS[subcommand])
            assert code_name in named, (subcommand, code_name, named)
