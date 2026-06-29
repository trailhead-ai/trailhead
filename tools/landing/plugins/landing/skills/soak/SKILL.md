---
name: soak
description: >
  Run the post-merge deploy soak health probe for a camp group deploy. Executes the
  configured health command once via soak_health.py; escalates to doctor on
  regression; exits clean when no health command is configured.
  Use for /landing:soak, "soak the deploy", "check health after merge", or when
  portage monitor emits a post_merge_handoff marker.
---

# Soak

**Implementation:** this skill dispatches the `soaker` subagent (Sonnet/medium),
which runs soak_health.py, interprets the result, and dispatches doctor on
regression.

**Agents:** `soaker`, `doctor`
**Scripts:** `soak_health.py`

## Normal trigger

After portage's `monitor` finishes its merge loop it emits a JSON marker as the LAST
line of its completion summary:

```json
{"post_merge_handoff": {"merge_pairs": [{"repo": "<name>", "sha": "<sha>", "pr": <n>}, ...], "manifest_path": "<abs-path>", "sidecar_path": "<abs-path>", "group_toml_path": "<abs-path>"}}
```

Parse this marker from the last non-empty line of monitor's completion summary.
If it parses and contains `post_merge_handoff`, dispatch `soaker` in the
background with the `manifest_path`, `merge_pairs`, and `group_toml_path` values.

## Dispatch

**Dispatch from the top-level session** (not inside a subagent — notification channel
is lost if nested):

```
Agent({
  subagent_type: "soaker",
  description: "soak health check for <group>/<slug>",
  run_in_background: true,
  prompt: |
    manifest_path: <absolute path to camp manifest JSON>
    group_toml_path: <absolute path to group TOML>
    merge_pairs: <repo>:<sha>:<pr>[, ...]
})
```

## Direct invocation

For manual soak runs or debugging, provide the same inputs directly:

```
manifest_path: /abs/path/to/camp/manifest.json
group_toml_path: /abs/path/to/group.toml
merge_pairs: <repo>:<sha>:<pr>
```

The agent will:
1. Emit the config-summary line showing the configured health command (or none).
2. Run `soak_health.py --toml <group_toml_path>`.
3. Exit clean if healthy or not configured; escalate to `doctor` if the health
   command fails or times out.

## Configuration

Health command is configured in the group TOML `[release]` block:

```toml
[release]
soak_health_command = "/path/to/health-check.sh"
```

Default: none — `soaker` exits clean, reporting:
`soak: n/a — no health command configured`

No observability provider configured — configure `[release].soak_health_command`
in the group TOML to wire a real health probe.
