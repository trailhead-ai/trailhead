"""Contract tests for the `publish-routing` eval's `arms/` directory.

The treatment arm embeds the whole of `tools/outpost/plugins/outpost/rules.md`
after the baseline brief. The live `treatment.md` must rebuild byte-for-byte from
today's `rules.md`. The frozen `treatment-pre-pad-exception.md` is a committed copy
of the arm the `MANUAL-EVAL.md` rows were measured against, with `rules.md` as it
stood at 037aff8b; nothing here rebuilds it.

Does not assert anything about the eval's measured result.
"""

from __future__ import annotations

import arms_contract as ac

ARMS_DIR = ac.EVALS_DIR / "publish-routing" / "arms"

BASELINE = "baseline.md"
LIVE_TREATMENT = "treatment.md"
FROZEN_TREATMENT = "treatment-pre-pad-exception.md"
ALL_MANIFESTED_ARMS = {BASELINE, LIVE_TREATMENT, FROZEN_TREATMENT}


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


def test_live_and_frozen_treatment_arms_diverge():
    assert _arm(LIVE_TREATMENT) != _arm(FROZEN_TREATMENT), (
        "treatment.md and treatment-pre-pad-exception.md are byte-identical — "
        "the live arm must carry the current rules.md."
    )
