---
name: watch-preview
description: >
  Run the post-merge soak health probe for a camp group deploy. Executes the
  configured health command once via soak_health.py; escalates to diagnose-preview
  on regression; exits clean when no health command is configured (inert by default).
  Use for /watch-preview, "soak the deploy", "check health after merge", or when
  watch-pr emits a post_merge_handoff marker.
---

# Watch Preview (Soak)

**Implementation:** this skill is a thin wrapper around the `watch-preview` subagent
(Sonnet/medium). That agent definition is canonical — it runs soak_health.py,
interprets the result, and dispatches diagnose-preview on regression.

**Agents:** `watch-preview`, `diagnose-preview`
**Scripts:** `soak_health.py`

## Normal trigger

After `watch-pr` finishes its merge loop it emits a JSON marker as the LAST line of
its completion summary:

```json
{"post_merge_handoff": {"merge_pairs": [{"repo": "<name>", "sha": "<sha>", "pr": <n>}, ...], "manifest_path": "<abs-path>", "sidecar_path": "<abs-path>"}}
```

Parse this marker from the last non-empty line of watch-pr's completion summary.
If it parses and contains `post_merge_handoff`, dispatch `watch-preview` in the
background with the `manifest_path` and `sidecar_path` values.

## Dispatch

**Dispatch from the top-level session** (not inside a subagent — notification channel
is lost if nested):

```
Agent({
  subagent_type: "watch-preview",
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
3. Exit clean if healthy or not configured; escalate to `diagnose-preview` if the
   health command fails or times out.

## Configuration

Health command is configured in the group TOML `[release]` block:

```toml
[release]
soak_health_command = "/path/to/health-check.sh"
```

Default: none — `watch-preview` exits clean, reporting:
`soak: n/a — no health command configured`

No observability provider configured — configure `[release].soak_health_command`
in the group TOML to wire a real health probe.
