# close-defers-an-unconfirmed-check

## Under test

When a slice's closing session reaches Phase 6's manual-check discharge and the operator has confirmed one covered `manual-check` criterion and answered "Not yet" for another, the session records the confirmation in the operator's own words for the first criterion only, puts the second on the spec's check list through `pending_check.py` (once, naming only that criterion, before any close write), runs `pending_check.py verify`, and only then runs the observation gate and the close write. The completion report names the waiting check, says the other was confirmed at close, and points to `/craft:checks`, saying that answering a check from the list arrives with a later command. The prose under test is `skills/_shared/execute.md`, Phase 6's manual-check discharge and completion report, as it stands when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `schedule-courier-pickups` with `- **ACn.**` criteria AC1 to AC3 (AC1 automated, AC2 and AC3 `*Verified by: manual check.*`) and an `in-progress` slice parent `pickups-can-be-booked-and-confirmed` (label `craft/slice-parent`, `craft/branch`, related to the spec) that covers AC1, AC2 and AC3, carries `## Criterion observations` with `automated-assertion` for AC1 and `manual-check` for AC2 and AC3, no `## Operator attestations`, and an `## End Phases` list ticked through Phase 5. The vault is named `eval`.

Two things stand in for the plugin, because the runner's sandbox hides the operator's home (the lore interpreter and the operator's `lore` on `PATH` do not run inside it): `./lore` is the self-contained wrapper the sibling waiting-checks cases use, logging every call's arguments to `lore-calls.log`; and `./scripts/pending_check.py` and `./scripts/observation_gate.py` are thin wrappers around **real copies** of craft's scripts (`real-scripts/`), run against a `bin/lore` that reaches the same seeded vault and logs to a separate `script-lore-calls.log`. The two wrappers add a line to `lore-calls.log` for each call (`pending_check.py <args>`), and the `pending_check.py` wrapper also copies the `--entries` file's contents into `pending-check-entries.log` at call time. So `lore-calls.log` is the ordered log of everything the agent ran, and the writer's effects on the spec and the parent are real vault writes.

How the operator's answers arrive: the runner is headless, so the prompt states the operator's replies per criterion, in their own words, and says to treat them as the replies to the procedure's question. The prompt also says where the plugin's scripts directory is for this run (`./scripts`), in the same way the sibling cases redirect `lore` to `./lore`. It resumes the run at Phase 6's discharge (every task done, flow-out ticked, push settled, no `## Enumerated states`) and omits the run-metrics row and the measurement tally. **Limit of this choice:** the question is never put to a person, so the case cannot show that the session shows the draft `check` and `how` in the "Not yet" choice, that the question uses `describe`'s criterion text, or that it asks one question per criterion with these three answers. It pins what the session writes, in what order and with what content, given the replies. The `mktemp` entries file's removal is not pinned either: the runner grants no `rm`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` runner block.
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 40`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the unedited prose before any prose edit and is expected red: that prose asks one confirmation question, writes no attestation for an unconfirmed criterion, and lets the observation gate refuse, so `pending_check.py` never runs.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The writer ran: `lore-calls.log` holds `pending_check.py --spec` | `writer-ran` | `regex` on `lore-calls.log`, contains |
| The writer ran once | `writer-ran-once` | `regex` on `lore-calls.log`, `--entries` twice, not_contains |
| Its entries file names AC3 | `entries-name-ac3` | `regex` on `pending-check-entries.log`, contains |
| Its entries file does not name AC2 | `entries-omit-ac2` | `regex` on `pending-check-entries.log`, not_contains |
| Order: writer, then verify, then the close write | `writer-then-verify-then-close` | `regex` on `lore-calls.log`, contains, dot-all |
| The parent is `done` | `parent-is-done` | `regex` on `vault/task/pickups-can-be-booked-and-confirmed.json`, contains |
| AC2's attestation carries the operator's own words | `ac2-attested-in-their-words` | `regex` on the parent's body, contains |
| No attestation for AC3 | `no-ac3-attestation` | `regex` on the parent's body, not_contains |
| The spec carries AC3's waiting entry | `spec-carries-the-waiting-entry` | `regex` on the spec's body, contains |
| The reply points to the check list | `hands-off-to-checks` | `regex` over the final message, `/craft:checks` |
| The reply says the slice closed, names the waiting check by its words with AC3 after, says AC2 was confirmed at close, says answering arrives with a later command, and does not call AC3 confirmed | `reply-reports-the-waiting-check` | `llm`, `focus: trace` |

### Each pass line against each branch, checked before any arm ran

The branches are the ones the prose can take: today's (one confirmation question; an unconfirmed criterion gets no attestation; the gate refuses; the run stops), the edited prose done right, and three ways to get the edit wrong (attest the "Not yet" criterion as well, name both criteria in the entries, close without waiting for the writer and verify).

| Branch | writer-ran | writer-ran-once | entries-name-ac3 | entries-omit-ac2 | order | parent-is-done | ac2-attested | no-ac3-attestation | spec entry | hands-off | reply | Whole run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Today's: stop at the refused gate | **fail** | pass | **fail** | pass | **fail** | **fail** | pass | pass | **fail** | **fail** | **fail** | fails |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Attests AC3 with the "Not yet" words | pass | pass | pass | pass | pass | pass | pass | **fail** | pass | pass | pass if it still says AC3 waits, else **fail** | fails |
| Names AC2 and AC3 in the entries | pass | pass | pass | **fail** (writer also refuses AC2, which carries no check to defer once attested, or writes it) | pass | pass | pass | pass | pass | pass | pass | fails |
| Closes without the writer or verify | **fail** | pass | **fail** | pass | **fail** | pass if the gate lets it | pass | pass | **fail** | **fail** | **fail** | fails |

Only the edited prose done right satisfies every clause; every other branch fails at least one mechanical clause that reads the call log or the vault, not the reply.

## Limitations

- One spec, one phrasing, one model tier, three runs, one arm of the question (yes for one criterion, "Not yet" for the other). The other arms are `stale-write-leaves-the-slice-open` and `one-failed-check-saves-nothing`.
- The replies are stated in the prompt, not asked; see the fixture section.
- The call log records arguments, not piped bodies; the attestation and the entry are graded from the vault files the run leaves.
- `observation_gate.py` runs from the stand-in; the runner block's grants decide whether the agent may call it, and a refused call is an error of the harness, logged apart.
- The reply clause is an `llm` judgement over the first and last 12 events of the run; the clauses that decide what was written are mechanical.
