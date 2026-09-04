**Value claim:** Evidence text shaped like a command substitution and a path traversal must
be treated as inert text — displayed and pattern-matched only, never opened as a path and
never passed to a subprocess.

**Covers:** AC9

## Criterion observations

- **AC9** — automated-assertion — $(touch /tmp/observation_gate_inertness_marker_test) ../../etc/passwd
