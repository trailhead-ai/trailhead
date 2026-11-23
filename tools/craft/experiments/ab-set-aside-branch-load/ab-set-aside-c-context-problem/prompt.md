---
allowed_tools: [Skill, Read, Write, Glob, Grep]
max_turns: 60
timeout_seconds: 540
---
I am running `/craft:execute task/members-can-download-invoices-with-sales-tax`. The parent is a slice parent, in progress, linked to `spec/invoice-members` (vault `eval`), covering AC1 and AC2. It has one child, `task/invoice-pdf-with-a-sales-tax-line`. The executor was dispatched for that child and came back with the report below; you are resuming at "Handling Executor Status". The build's code is not in this directory and you may not dispatch agents or run git: where the procedure would dispatch an agent, say in one line what you would send and go on. Carry out the procedure for this report, including whatever it requires of you on the records, until the build is settled or the procedure stops. Do not run the end phases and do not close the parent. Say in your final message what you did and why.

The executor's report, verbatim:

> Status: BLOCKED
> files: invoices/pdf.py (1 file changed, 38 insertions(+))
> commits: none
> blocking: I cannot tell which of the two existing invoice layouts the PDF should follow. `invoices/templates/classic.html` and `invoices/templates/compact.html` both render a past invoice, and the task body names neither. Nothing else is missing: the rest is built and tested, and I need only the answer to pick one.
> unknowns: none

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
