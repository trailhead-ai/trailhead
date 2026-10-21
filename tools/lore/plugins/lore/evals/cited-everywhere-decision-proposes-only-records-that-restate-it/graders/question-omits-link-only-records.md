---
type: llm
focus: trace
---

PASS if the yes/no question in the agent's final message proposes a change to `decision/plan-saturday-restocking-around-the-discard-window` and to none of these eight records: `decision/label-every-crate-with-a-donor-name`, `decision/keep-citrus-on-its-own-shelf`, `task/print-a-sign-for-the-produce-table`, `task/count-the-crates-at-closing`, `task/buy-gloves-for-the-shelf-crew`, `task/ask-the-food-bank-about-a-second-fridge`, `lesson/unlabelled-crates-are-slow-to-sort`, `area/produce-table`. Naming them in a coverage line as records that only link is allowed.
FAIL if the final message proposes a change to any of those eight records, or lists any of them in the yes/no question.
