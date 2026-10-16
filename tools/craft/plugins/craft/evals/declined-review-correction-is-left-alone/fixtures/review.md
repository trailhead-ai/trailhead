# Review: label queue retry worker

Reviewer: code-reviewer subagent. Verdict: no blocking issues.

## Findings on the change

None.

## Corrections to other records

1. `decision/label-queue-retries-use-a-flat-delay` says a failed print is retried after 45 seconds. `adr/label-printer-firmware-rejects-fast-resends` records that the firmware rejects any resend within 90 seconds of a failure, so the decision's figure is wrong. Fix: in that decision, replace "45 seconds" with "90 seconds".
