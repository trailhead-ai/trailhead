"""The tidy-followups skill names every code the follow-up scripts can emit.

``followup_gather.py``, ``followup_parent.py`` and ``followup_dispose.py`` print ``reason-code: <token>`` on
stderr for each refusal or failure, and ``followup_parent.py check`` reports a
``reason`` per left-out follow-up. The skill tells the agent what each token means
and whose side it is on. A token a script emits and the skill never names leaves the
agent to improvise the sentence for it.

The expected sets are the scripts' own ``REASON_CODES`` and ``LEFT_OUT_REASONS``,
loaded from the files by path, so a code a script grows is demanded of the skill
automatically.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SKILL = CRAFT / "skills" / "tidy-followups" / "SKILL.md"
SCRIPT = CRAFT / "scripts" / "followup_gather.py"

_spec = importlib.util.spec_from_file_location("followup_gather_for_reason_codes", SCRIPT)
gather = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gather)

PARENT_SCRIPT = CRAFT / "scripts" / "followup_parent.py"
_pspec = importlib.util.spec_from_file_location("followup_parent_for_reason_codes", PARENT_SCRIPT)
parent = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(parent)

DISPOSE_SCRIPT = CRAFT / "scripts" / "followup_dispose.py"
_dspec = importlib.util.spec_from_file_location("followup_dispose_for_reason_codes", DISPOSE_SCRIPT)
dispose = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(dispose)

CONFIRM_SCRIPT = CRAFT / "scripts" / "followup_confirm.py"
_cspec = importlib.util.spec_from_file_location("followup_confirm_for_reason_codes", CONFIRM_SCRIPT)
confirm = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(confirm)


def test_skill_names_every_reason_code_the_script_emits():  # inert-gate: allow code-vs-document consistency seam
    text = SKILL.read_text(encoding="utf-8")
    missing = sorted(code for code in gather.REASON_CODES if code not in text)
    assert not missing, f"tidy-followups/SKILL.md never names reason-code(s): {missing}"



def test_skill_names_every_reason_code_the_parent_script_emits():  # inert-gate: allow code-vs-document consistency seam
    text = SKILL.read_text(encoding="utf-8")
    missing = sorted(code for code in parent.REASON_CODES if code not in text)
    assert not missing, f"tidy-followups/SKILL.md never names followup_parent reason-code(s): {missing}"


def test_skill_names_every_reason_code_dispose_emits():  # inert-gate: allow code-vs-document consistency seam
    text = SKILL.read_text(encoding="utf-8")
    missing = sorted(code for code in dispose.REASON_CODES if code not in text)
    assert not missing, f"tidy-followups/SKILL.md never names followup_dispose reason-code(s): {missing}"


def test_skill_names_every_left_out_reason_check_can_report():  # inert-gate: allow code-vs-document consistency seam
    text = SKILL.read_text(encoding="utf-8")
    missing = sorted(code for code in parent.LEFT_OUT_REASONS if code not in text)
    assert not missing, f"tidy-followups/SKILL.md never names left-out reason(s): {missing}"


def test_skill_shape_rule_names_the_codes_length_and_alphabet():  # inert-gate: allow code-vs-document consistency seam
    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    rule = re.search(r"exactly (\d+) characters, each one of `([^`]+)`", text)
    assert rule, "tidy-followups/SKILL.md has no shape rule naming the code's length and characters"
    assert (int(rule.group(1)), rule.group(2)) == (confirm.CODE_LENGTH, confirm.ALPHABET)
