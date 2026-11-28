# a-session-that-cannot-run-camp-reads-host-state-from-outpost

## Under test

The claim: when camp cannot list other hosts (as inside the sandbox), the docket skill asks Outpost through `outpost-gateway docket workspaces` run by the shim's absolute path as its own command (the form the sandbox exclusion matches), re-reads the docket with `--workspace-state` naming a temp file holding the reply, and tells the operator where the hosts it names came from. The prose under test is `skills/docket/SKILL.md`, loaded from the plain request the prompt gives. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`): a throwaway lore vault named `eval`, a docket list of eight entries (one attached to a workspace), `./docket` as the real package reached through a test-only runner, a `./camp` stand-in that fails every `list`, and a `./outpost-gateway` stand-in answering one host running the workspace and one host that did not answer. Scaffold settings for this case: CAMP=down.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Bash, Write, Read]`, `max_turns` 30, `timeout_seconds` 420.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The red baseline runs against the skill before the Outpost step was added to it. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The docket was read with bare ./docket | `bare-docket-read-ran` | `tool_used` |
| The gateway verb ran by the shim's absolute path as its own command | `gateway-verb-ran-bare` | `tool_used` |
| The docket was read again with --workspace-state naming a file | `docket-reread-with-the-state-file` | `tool_used` |
| No docket change command was run | `no-docket-change-command` | `tool_used` |
| The reply names the host the workspace runs on | `reply-names-the-host-running-it` | `regex` |
| The reply says the state came from Outpost | `reply-says-the-state-came-from-outpost` | `regex` |
| The reply names the host that did not answer | `reply-names-the-silent-host` | `regex` |

## Limitations

- One fixture, one model tier, three runs. The graders read the tool calls and the reply's wording; a reply that is right in different words can fail a wording grader. The stand-in gateway cannot refuse a piped or redirected call the way the real command does, so the single-command form is graded from the command line the agent sent.
- The temp file's removal is not graded here; another case grades the note flow's `rm`.
