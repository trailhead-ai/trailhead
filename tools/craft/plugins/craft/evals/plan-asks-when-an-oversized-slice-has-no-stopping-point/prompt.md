---
allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]
max_turns: 120
timeout_seconds: 900
---
Run `/craft:plan task/move-the-shop-data-into-one-database`. The slice is chosen and I have approved it as it stands. You have my approval for the approach you recommend and for every disposition you recommend on a council finding; where the skill would ask me a clarifying question about how to build something, decide it on your own recommendation instead. Where the skill would put a question to me about the slice itself, ask it as the skill says and stop there: I will answer in my next message. Build nothing and do not run `/craft:execute`.

The project's code is in this directory, under `shop/`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables apart from that `PATH` prefix.
