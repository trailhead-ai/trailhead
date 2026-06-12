# Soak & Incident Rituals

landing's `deploy` capability handles the post-merge phase: soak a freshly
merged deploy with a configurable health command, and diagnose a deploy-log
regression when one surfaces. The deterministic plumbing lives in the shared
`trailhead.vcs` provider library and landing's own soak script; this doc records
the rituals the plugin's agents follow.

## Soak config

Per-group soak behavior is read from the `[release]` block of the group TOML:

```toml
[release]
# soak_health_command = "curl -f https://app.example.com/healthz"
# external_tracker = { kind = "..." }   # reserved; no connector
```

All keys are optional:

- Omitting `soak_health_command` → soak reports
  `soak: n/a — no health command configured` and exits clean (inert by default,
  no vendor baked in).
- `external_tracker` is a reserved, null-defaulting seam — no incident-tracker
  connector is built.

## Soak invocation safety

The soak health command runs `shell=False` — the configured command string is
split with `shlex.split` and passed as an arg-list to `Popen`, never through a
shell. On timeout, the soak kills the **process group** (`os.killpg`) so a
health command that spawns grandchildren does not leak background processes.

## Incident handling

On a clean deploy the soak exits clean. On a regression it escalates to the
deploy-health diagnostic, which interrogates the GitHub Actions deploy log
(`deploy.logs` / `deploy.status` / `deploy.workflow_runs`), triages the failure,
and produces a recommendation. The user then drives the resolve ritual to
revert, forward-fix, recheck, or dismiss.
