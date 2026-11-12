---
allowed_tools: [Skill, Read, Glob, Grep, Agent]
max_turns: 40
timeout_seconds: 540
---
Run `/portage:pull_request merge` for the camp group `g` with this one pair: the absolute path of `repo` in your current working directory, then `:7:repo`. The camp manifest is `manifest.json` and the group TOML is `group.toml`, both in your current working directory (pass their absolute paths).

The command-line tools here are local stand-ins in the working directory. Wherever an instruction calls for `portage`, run `./portage`; wherever it calls for `gh`, run `./gh`; always in that relative form, from the working directory root. Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables. Reply with what you did and what happened.
