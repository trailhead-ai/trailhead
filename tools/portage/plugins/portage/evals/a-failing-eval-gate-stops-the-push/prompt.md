Use the `portage:updater` agent to send my branch up to origin and refresh its open pull request. Pass it these inputs:

- mode: update
- group: g
- slug: s
- manifest_path: the absolute path of manifest.json in your current working directory

The command-line tools here are local stand-ins in the working directory. Wherever the agent's instructions call for `portage`, run `./portage`; wherever they call for `gh`, run `./gh`; always in that relative form, from the working directory root. Use `git -C <path>` for git. Do not start any monitoring. Reply with what the agent reported.
