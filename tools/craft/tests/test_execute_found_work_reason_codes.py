"""Every `reason-code:` the execute procedure's "Found work" section names is one
`found_work.py` can emit. The expected set is `found_work.REASON_CODES`, read from the
module, so a renamed or retired code leaves the prose naming a token the script never
prints."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

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
