---
allowed_tools: [Skill, Bash, Write, Read]
max_turns: 30
timeout_seconds: 420
---
What's next on my docket?

The docket tool is installed in this directory as `./docket`, lore as `./lore`, camp as `./camp` and the Outpost gateway command as `./outpost-gateway`. Wherever a document or instruction says `docket`, `lore` or `camp`, run the `./` one. For `outpost-gateway`, a document that gives the shim's absolute path means the absolute path of the `./outpost-gateway` file in this directory; the file itself tells you that path when run with `--help`. Make each Bash call a single command: no `;`, `&&` or `|`.
