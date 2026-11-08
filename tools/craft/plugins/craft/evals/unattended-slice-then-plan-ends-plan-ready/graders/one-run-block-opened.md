---
type: llm
focus: trace
---
Pass if, across the whole session, the slice record ended up with exactly one `## Unattended run` heading: slice opened a block when it created the record, and plan continued that same block rather than adding a second one. A write that was rejected (for example a bad diff hunk header) and then rebuilt and applied once counts as one block, not two. Fail if the session successfully added a second `## Unattended run` heading to the slice record (an applied write that adds the heading when one already exists), or never added one at all.
