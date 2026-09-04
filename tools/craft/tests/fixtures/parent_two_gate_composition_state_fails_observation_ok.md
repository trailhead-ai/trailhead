**Value claim:** Composition fixture — the enumerated state below names a design-doc section
that does not exist, which the state-coverage gate would refuse on. The observation gate reads
only its own grammar and must certify this parent regardless.

**Covers:** AC9

## Enumerated states

- a-state-with-no-matching-design-doc-section

## Criterion observations

- **AC9** — automated-assertion — tools/craft/tests/test_close_gate_observation_contract.py::test_state_coverage_mismatch_does_not_mask_a_clean_observation_set
