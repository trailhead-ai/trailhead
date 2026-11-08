# flush-leaves-found-work-decisions

## Under test

`skills/flush/SKILL.md`, Step 3: a candidate whose header reads `kind=found-work` or `kind=found-work-result` records a decision a build already carried out. Flush never promotes it to a record of any kind, never sends it down the record-fix route, and leaves it in the session log; the flush still stamps the session clean. Every other candidate is evaluated as before. Baseline is the flush prose before the rule; treatment is the prose with it.

The fixture seeds a dirty session (key `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee`, through `fixtures/session.json` and the real `lore session candidate`) holding a `found-work` deferred line, its `found-work-result` line, a `found-work` folded line, its `found-work-result` line, and one ordinary `kind=lesson` candidate. The vault holds no other record. The prompt asks for `/lore:flush` and names the session key. Wording cue: none beyond the request; the found-work lines read as open findings ("Export drops items with no expiry date"), which the baseline is expected to turn into a task.

## Pass condition

After the run the lesson is a record, no record exists that came from a found-work line, and the session is clean. Every clause holds; a run scores the fraction of graders that pass and passes only at 1.0. A case passes at 3 of 3 runs on the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| The ordinary lesson candidate became a record | `lesson-became-a-record` | `file_exists` on `vault/lesson/*.md` |
| No task was created | `no-task-from-found-work` | `file_exists` on `vault/task/*.md`, `exists: false` |
| No decision was created | `no-decision-from-found-work` | `file_exists` on `vault/decision/*.md`, `exists: false` |
| No spec was created | `no-spec-from-found-work` | `file_exists` on `vault/spec/*.md`, `exists: false` |
| No area was created | `no-area-from-found-work` | `file_exists` on `vault/area/*.md`, `exists: false` |
| No `record create/update/amend/supersede` call of any kind carries a found-work finding's text | `no-record-carries-found-work-text` | `tool_used` on Bash, `min: 0` `max: 0`, `input_match` on `ExpiryNullDrop`, `unit_header_label`, `pantry/export.py` and the two finding phrases |
| The session is stamped clean | `session-is-clean` | `regex` on the session sidecar, `"status": "clean"` |
| The found-work lines are still in the session log | `found-work-lines-stay-in-the-session-log` | `regex` on the session body, `kind=found-work-result phase=Build` |

## Baseline expectation

The baseline arm is expected to fail `no-task-from-found-work` in at least one run, because the current flush prose evaluates every candidate by judgment and promotes open work to a task.

## Limitations

- `file_exists` counts only files created during the run, which is what these clauses need: the vault is seeded with no records.
- The four kind graders check that no task, decision, spec or area exists. `no-record-carries-found-work-text` covers every other kind: it reads every Bash call (a `regex` grader on `target: files` was tried and sees only files the agent created through its file tools, so it passed vacuously on a lesson written from a found-work line), and fails when a record-writing `./lore` call carries one of the planted identifiers or finding phrases. A record that restates a finding in words that avoid all of them is not caught.
- A single fixture and phrasing; the rule's wording for other candidate shapes is not measured.
