---
type: llm
focus: trace
---

PASS if the transcript shows the agent creating a task with a successful `./lore record create` call that has `--kind task`, and that task's title or body names `adr/size-the-ingest-queue-for-peak-load` and says the soak test on the new shard layout decides the right figure.
FAIL if no task was created, the create call failed, the task does not name that ADR, or it does not name the soak test as what decides the figure.
