---
allowed_tools: [Skill, Read, Glob, Grep, Agent]
max_turns: 100
timeout_seconds: 900
---
Run `/portage:pull_request create --unattended task/count-words --deadline 900`.

The command-line tools here are local stand-ins in the working directory. Wherever an instruction calls for `portage`, run `./portage`; for `gh`, run `./gh`; for `lore`, run `./lore`; for `git`, run `./git` (use `./git -C <path>`); always in that relative form, from the working directory root. Pass this same note on in the prompt of every agent you dispatch. The camp group is `g`, the worktree slug is `s`, the camp manifest is `manifest.json` and the group TOML is `group.toml`, both in your current working directory (pass their absolute paths).

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables. This sandbox denies a command that contains a newline or a redirect to a file, so keep each command on one line: give `gh pr create` a one-line body, and when you dispatch the `updater` pass it `pr_body_file: <absolute path of pr-description.md in the working directory>` (that file holds the pull request's current body). Pass this paragraph on in the prompt of every agent you dispatch.
