**Value claim:** A second `**Covers:**` line appears inside a fenced code block — an
example, not a real claim — and must be invisible to the field search, so it does not
collide with the real, unmasked field below it.

Here is what the grammar looks like:

```
**Covers:** AC1, AC2
```

**Covers:** AC1

## Criterion observations

- **AC1** — automated-assertion — tools/craft/tests/test_observation_gate.py::test_ac1
