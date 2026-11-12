I am running `/craft:execute task/count-words --unattended --then-pr`. The task branch `count-words` is checked out in this directory, which is the one repository the run builds in.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables. Git is installed in this directory as `./git`. Wherever a document or instruction says `git`, run `./git` instead.
