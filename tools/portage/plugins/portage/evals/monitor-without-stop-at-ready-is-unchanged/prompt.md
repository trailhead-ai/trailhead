Use the `portage:monitor` agent to watch my pull request. Pass it these inputs:

- group: g
- slug: s
- manifest_path: the absolute path of manifest.json in your current working directory
- group_toml_path: the absolute path of group.toml in your current working directory
- pr_pairs: the absolute path of repo in your current working directory, then `:7:repo`
- outcome_file: the absolute path of outcome/outcome in your current working directory

The command-line tools here are local stand-ins in the working directory. Wherever the agent's instructions call for `portage`, run `./portage`; wherever they call for `gh`, run `./gh`; always in that relative form, from the working directory root, one command per call. Use `git -C <path>` for git. Run the agent in the foreground and wait for it. Use `2026-01-01T00:00:00Z` wherever the agent needs a starting timestamp. Reply with what the agent reported.
