"""followup_confirm: the short confirmation code a preview prints and a write recomputes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts" / "followup_confirm.py"
_spec = importlib.util.spec_from_file_location("followup_confirm", _SCRIPT)
confirm = importlib.util.module_from_spec(_spec)
sys.modules["followup_confirm"] = confirm
_spec.loader.exec_module(confirm)

PAYLOAD = {
    "reason": "retired line",
    "drops": [{"name": "alpha", "revision": "r1"}, {"name": "beta", "revision": "r2"}],
    "merges": [
        {
            "survivor": {"name": "sur", "revision": "r3"},
            "absorbed": [{"name": "abs", "revision": "r4", "intent": "adds the cache"}],
        }
    ],
    "included": ["inc-a", "inc-b"],
}
BASE = ("drop-and-merge", "spec-one", PAYLOAD)


def edited(**changes):
    payload = {**PAYLOAD, **changes}
    return ("drop-and-merge", "spec-one", payload)


def code(kind, spec, payload):
    return confirm.code_for(kind, spec, payload)


def test_the_code_is_four_characters_from_an_alphabet_without_look_alikes():
    for i in range(300):
        value = code("k", "s", {"n": i})
        assert len(value) == 4
        assert set(value) <= set(confirm.ALPHABET)
    assert not set("01ilo") & set(confirm.ALPHABET)
    assert confirm.ALPHABET == confirm.ALPHABET.lower()


def test_the_same_inputs_give_the_same_code():
    assert code(*BASE) == code(*BASE)


@pytest.mark.parametrize(
    "changed",
    [
        ("other-kind", "spec-one", PAYLOAD),
        ("drop-and-merge", "spec-two", PAYLOAD),
        edited(drops=[{"name": "alphx", "revision": "r1"}, {"name": "beta", "revision": "r2"}]),
        edited(drops=[{"name": "alpha", "revision": "r9"}, {"name": "beta", "revision": "r2"}]),
        edited(reason="another reason"),
        edited(
            merges=[
                {
                    "survivor": {"name": "sur", "revision": "r3"},
                    "absorbed": [{"name": "abs", "revision": "r4", "intent": "adds the index"}],
                }
            ]
        ),
        edited(included=["inc-a"]),
    ],
    ids=["kind", "spec", "name", "revision", "reason", "intent", "included"],
)
def test_the_code_changes_when_any_one_input_changes(changed):
    assert code(*changed) != code(*BASE)


def test_key_order_does_not_change_the_code():
    shuffled = dict(reversed(list(PAYLOAD.items())))
    shuffled["drops"] = [dict(reversed(list(d.items()))) for d in PAYLOAD["drops"]]
    assert code("drop-and-merge", "spec-one", shuffled) == code(*BASE)


def test_the_order_of_an_unordered_list_does_not_change_the_code():
    assert code(*edited(included=["inc-b", "inc-a"])) == code(*BASE)
    assert code(*edited(drops=list(reversed(PAYLOAD["drops"])))) == code(*BASE)


def test_a_name_cannot_borrow_a_neighbouring_field_through_a_delimiter():
    a = code("k", "s", {"drops": ["ab", "c"]})
    b = code("k", "s", {"drops": ["a", "bc"]})
    assert a != b


def test_a_typed_code_is_accepted_in_any_case_and_with_surrounding_whitespace():
    real = code(*BASE)
    assert confirm.matches(*BASE, real)
    assert confirm.matches(*BASE, real.upper())
    assert confirm.matches(*BASE, f"  \t{real}\n ")


@pytest.mark.parametrize("position", range(4))
def test_a_code_one_character_different_is_refused_at_every_position(position):
    real = code(*BASE)
    other = next(c for c in confirm.ALPHABET if c != real[position])
    assert not confirm.matches(*BASE, real[:position] + other + real[position + 1 :])


def test_a_code_with_extra_or_missing_characters_is_refused():
    real = code(*BASE)
    assert not confirm.matches(*BASE, real + real[0])
    assert not confirm.matches(*BASE, real[:-1])


def test_an_absent_or_blank_code_never_matches():
    assert not confirm.matches(*BASE, None)
    assert not confirm.matches(*BASE, "   ")
    assert not confirm.matches(*BASE, "")


def test_a_code_for_other_content_is_refused():
    assert not confirm.matches(*BASE, code(*edited(reason="x")))
