"""The tidy-followups skill names every reason-code the gathering script can emit.

``followup_gather.py`` prints ``reason-code: <token>`` on stderr for each refusal or
failure, and the skill tells the agent what each token means and whose side it is on.
A token the script emits and the skill never names leaves the agent to improvise the
sentence for that failure.

The expected set is the script's own ``REASON_CODES``, loaded from the file by path,
so a code the script grows is demanded of the skill automatically.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SKILL = CRAFT / "skills" / "tidy-followups" / "SKILL.md"
SCRIPT = CRAFT / "scripts" / "followup_gather.py"

_spec = importlib.util.spec_from_file_location("followup_gather_for_reason_codes", SCRIPT)
gather = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gather)


def test_skill_names_every_reason_code_the_script_emits():  # inert-gate: allow code-vs-document consistency seam
    text = SKILL.read_text(encoding="utf-8")
    missing = sorted(code for code in gather.REASON_CODES if code not in text)
    assert not missing, f"tidy-followups/SKILL.md never names reason-code(s): {missing}"

