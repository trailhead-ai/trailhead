"""Contract tests for the `publish-routing` eval's `arms/` directory.

The treatment arm embeds the whole of `tools/outpost/plugins/outpost/rules.md`
after the baseline brief. The live `treatment.md` must rebuild byte-for-byte from
today's `rules.md`; the frozen `treatment-pre-pad-exception.md` must rebuild
byte-for-byte from `rules.md` at the commit the committed `MANUAL-EVAL.md` rows
were measured against.

Does not assert anything about the eval's measured result.
"""

from __future__ import annotations


import arms_contract as ac

ARMS_DIR = ac.EVALS_DIR / "publish-routing" / "arms"

BASELINE = "baseline.md"
LIVE_TREATMENT = "treatment.md"
FROZEN_TREATMENT = "treatment-pre-pad-exception.md"
ALL_MANIFESTED_ARMS = {BASELINE, LIVE_TREATMENT, FROZEN_TREATMENT}

# The `rules.md` the committed 2026-09-09 rows were measured against.
FROZEN_REVISION = "037aff8b"

# A later revision whose whole-file text differs from the frozen pin.
_WRONG_REVISION = "3466c9d4"


def _arm(name: str) -> str:
    return ac.read_arm(ARMS_DIR, name)


def test_every_arm_on_disk_has_a_manifest_entry():
    ac.assert_arms_accounted_for(ARMS_DIR, ALL_MANIFESTED_ARMS)


def test_live_treatment_arm_rebuilds_byte_identically():
    expected = ac.build_arm(_arm(BASELINE), ac.current_rules())
    assert _arm(LIVE_TREATMENT) == expected, (
        f"{LIVE_TREATMENT} has drifted from baseline brief + current rules.md — "
        "rebuild it from today's rules.md."
    )


def test_frozen_treatment_arm_matches_its_declared_provenance():
    """IF THIS GOES RED: never edit the frozen arm to make it pass. The committed
    `MANUAL-EVAL.md` rows were measured against exactly the text at
    `FROZEN_REVISION`."""
    expected = ac.build_arm(_arm(BASELINE), ac.rules_at(FROZEN_REVISION))
    assert _arm(FROZEN_TREATMENT) == expected, (
        f"{FROZEN_TREATMENT} has drifted from baseline brief + rules.md at "
        f"{FROZEN_REVISION!r}."
    )


def test_frozen_treatment_arm_reconstruction_is_sensitive_to_revision():
    wrong = ac.build_arm(_arm(BASELINE), ac.rules_at(_WRONG_REVISION))
    assert wrong != _arm(FROZEN_TREATMENT), (
        f"{FROZEN_TREATMENT} matched a reconstruction at the wrong revision "
        f"({_WRONG_REVISION!r}) — the revision pin discriminates nothing."
    )


def test_live_and_frozen_treatment_arms_diverge():
    assert _arm(LIVE_TREATMENT) != _arm(FROZEN_TREATMENT), (
        "treatment.md and treatment-pre-pad-exception.md are byte-identical — "
        "the live arm must carry the current rules.md."
    )
