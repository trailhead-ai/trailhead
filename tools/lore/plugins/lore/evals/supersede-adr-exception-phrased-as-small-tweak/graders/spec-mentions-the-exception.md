---
type: llm
focus: trace
---

PASS if the transcript shows the agent creating a spec with a successful `./lore record create` call that has `--kind spec`, and that spec's title or body proposes letting the nightly report exporter connect straight to the archive endpoint, as an exception to routing all outbound traffic through the egress gateway.
FAIL if no spec was created, or the created spec is about something else, or it does not propose that exception.
