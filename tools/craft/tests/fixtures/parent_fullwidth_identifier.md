**Value claim:** The `**Covers:**` value and the observation identifier both use the
fullwidth digit U+FF19 (`９`) rather than ASCII `9` — Python's Unicode-aware `\d` matches
it, so an unanchored identifier pattern would parse `AC９` as a well-formed
identifier and certify it, even though it is not the canonical ASCII `AC9`.

**Covers:** AC９

## Criterion observations

- **AC９** — automated-assertion — tools/craft/tests/test_observation_gate.py::test_x
