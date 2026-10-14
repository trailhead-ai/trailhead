"""The slice skill names every reason-code the candidate-set gate can emit.

The gate prints ``reason-code: <token>`` on stderr for each refusal, and the slice
skill's step 4 tells the agent which remedy each token calls for. A token the gate
emits and the skill never names leaves the agent without a remedy for that refusal.

The expected set is derived from the module: every module-level ``_*_REASON_CODE``
name ``candidate_set`` exposes, imported ones included, so a new code the gate
grows is demanded of the skill automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SLICE_SKILL = CRAFT / "skills" / "slice" / "SKILL.md"
sys.path.insert(0, str(CRAFT / "scripts"))
import candidate_set  # noqa: E402


def _emitted_reason_codes(module) -> set[str]:
    return {
        getattr(module, name)
        for name in dir(module)
        if name.startswith("_") and name.endswith("_REASON_CODE")
    }


def _unnamed_by(skill_text: str, module) -> list[str]:
    return sorted(code for code in _emitted_reason_codes(module) if code not in skill_text)


def test_skill_names_every_reason_code_the_gate_emits():  # inert-gate: allow code-vs-document consistency seam
    missing = _unnamed_by(SLICE_SKILL.read_text(encoding="utf-8"), candidate_set)
    assert not missing, f"slice/SKILL.md never names gate reason-code(s): {missing}"


def test_a_reason_code_the_skill_omits_is_reported():
    grown = SimpleNamespace(**{n: getattr(candidate_set, n) for n in dir(candidate_set)})
    grown._X_REASON_CODE = "zz-new-code"
    assert _unnamed_by("zz-other", SimpleNamespace(_A_REASON_CODE="a-code")) == ["a-code"]
    assert "zz-new-code" in _unnamed_by(SLICE_SKILL.read_text(encoding="utf-8"), grown)
