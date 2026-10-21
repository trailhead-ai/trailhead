Hand the `portage:updater` agent the job of shipping my branch and bringing its pull request description up to date. Give it these inputs:

- mode: update
- group: g
- slug: s
- manifest_path: the absolute path of manifest.json in your current working directory

The command-line tools here are local stand-ins in the working directory. Wherever the agent's instructions call for `portage`, run `./portage`; wherever they call for `gh`, run `./gh`; always in that relative form, from the working directory root. Use `git -C <path>` for git. The shell here cannot redirect output into a file, so the pull request's current description is already saved at ./pr-description.md (in the working directory). Do not start any monitoring. Reply with what the agent reported.
