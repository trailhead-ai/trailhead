---
type: llm
focus: trace
---
Pass if the question parked as `Q1` in the slice record's `## Unattended run` block says the slice is too large for one reviewable pull request, names the slice's criteria involved by number, says why no stopping point exists (no subset of the criteria could be built and shipped alone, because each depends on the others), and gives options that include building it as one large pull request, reshaping the slice or the spec, and dropping the slice, each with what it costs, one marked `(recommended)`. Fail if the parked question is about anything else, if the run cut the slice anyway and planned a subset, or if it planned the whole slice without parking.
