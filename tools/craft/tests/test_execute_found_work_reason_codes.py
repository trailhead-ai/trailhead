"""Every `reason-code:` the execute procedure's "Found work" section names is one
`found_work.py` can emit. The expected set is `found_work.REASON_CODES`, read from the
module, so a renamed or retired code leaves the prose naming a token the script never
prints."""

from __future__ import annotations

import importlib.util
import io
import json
import re
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


def named_reason_codes(markdown: str) -> list[str]:
    flat = re.sub(r"\s+", " ", markdown)
    start = flat.index("## Found work")
    end = flat.index("## Blocked-Path Write")
    return _NAMED.findall(flat[start:end])


def test_every_reason_code_the_prose_names_is_one_the_script_emits():
    named = named_reason_codes(_EXECUTE.read_text(encoding="utf-8"))
    assert named, "the Found work section names no reason-code"
    assert set(named) <= found_work.REASON_CODES, sorted(set(named) - found_work.REASON_CODES)


def test_a_code_the_script_does_not_emit_is_caught():
    text = "## Found work\n\nsee `reason-code: no-spec` and `reason-code:\nmade-up-code`.\n## Blocked-Path Write\n"
    named = named_reason_codes(text)
    assert named == ["no-spec", "made-up-code"]
    assert set(named) - found_work.REASON_CODES == {"made-up-code"}


def _refused_without_a_finding(code_name, monkeypatch, capsys):
    """Run the script on input that triggers `code_name`; return (exit code, stdout, stderr)."""
    if code_name == "reserved-security-prefix":
        doc = {
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
        code = found_work.main(["decide", "--session", "build"], stdin=json.dumps(doc), runner=None)
    else:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff\xfe not utf-8"), encoding="utf-8"))
        code = found_work.main(["decide", "--session", "build"], runner=None)
    out = capsys.readouterr()
    return code, out.out, out.err


def _windows(markdown: str, anchor: str, size: int = 1500) -> list[str]:
    flat = re.sub(r"\s+", " ", markdown)
    return [flat[m.start():m.start() + size] for m in re.finditer(re.escape(anchor), flat)]


@pytest.mark.parametrize("code_name", ["reserved-security-prefix", "non-utf8-stdin"])
def test_an_exit_2_refusal_that_prints_no_finding_is_named_where_the_prose_says_exit_2_prints_one(
    code_name, monkeypatch, capsys
):
    code, out, err = _refused_without_a_finding(code_name, monkeypatch, capsys)
    assert code == 2 and out == "" and f"reason-code: {code_name}" in err
    text = _EXECUTE.read_text(encoding="utf-8")
    for anchor in ("Exit codes: `0` done", "scrubbed finding on exit `2`"):
        windows = _windows(text, anchor)
        assert windows, anchor
        named = re.compile(rf"(?<![\w-]){re.escape(code_name)}(?![\w-])")
        assert all(named.search(w) for w in windows), (anchor, code_name)
