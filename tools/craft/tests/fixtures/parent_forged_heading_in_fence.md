**Value claim:** A `## Criterion observations` heading appears inside a fenced code
block — an example of the grammar, not a real section — and must be invisible to the
heading search, so it does not collide with the real, unmasked heading below it.

**Covers:** AC9

Here is what the grammar looks like:

```
## Criterion observations

- **AC9** — automated-assertion — decoy, this line is masked too
```

## Criterion observations

- **AC9** — automated-assertion — tools/craft/tests/test_observation_gate.py::test_ac9
