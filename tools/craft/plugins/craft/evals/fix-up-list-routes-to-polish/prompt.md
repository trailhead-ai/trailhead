---
allowed_tools: [Skill, Read, Glob, Grep]
max_turns: 4
timeout_seconds: 180
---
Tidy these up:
1. Rename the variable `tmp` to `retryDelay` in the label queue worker.
2. Add a missing null check before reading `job.payload` in the retry handler.
