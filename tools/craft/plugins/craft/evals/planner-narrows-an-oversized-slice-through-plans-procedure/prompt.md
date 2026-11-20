---
allowed_tools: [Agent, Read, Glob, Grep]
max_turns: 60
timeout_seconds: 900
---
Use the `craft:planner` agent to plan the slice parent `task/export-the-orders-to-a-csv-file`: it is the chosen slice of the ready spec `order-export`, already materialized as an in-progress slice parent. Tell the agent that the slice is approved, that no one is available to answer its questions, so it decides each on its own recommendation, and that it writes the plan and the child tasks into the vault and builds nothing. When it returns, give me its summary verbatim.

Pass the agent these facts about the environment: the project's code is in this directory, under `shop/`; the lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`. Each Bash call it makes is a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables apart from that `PATH` prefix; it writes a record body to a `mktemp` file with the file-writing tool and feeds it in by redirect, never on a command line.
