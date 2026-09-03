# Consistency pass output — spec/document-review-sign-off

Captured verbatim from a treatment-arm run against `spec-compound-criterion.md`,
2026-09-03, `agents/consistency-auditor.md` @ `e44b49c`. Used as the adjudication
arm's input so severity is judged against a real pass output, not a paraphrase.

---

## Verdict
**gaps**

## Coverage matrix

| Objective | Criteria covering it | Coverage |
|---|---|---|
| "Give the contracts team a single authoritative answer to 'is this document cleared?'" | C1 (approve → clears), C4 (badge shows state) | partial — no criterion covers the "blocked" state named in Required Interfaces |
| "give reviewers a way to record sign-off without leaving the app" | C1 (approve), C3 (comment — ambiguous) | partial — "sign-off" is only demonstrated by approval |

| Required Interface | Criteria covering it | Coverage |
|---|---|---|
| "The review-state field on a document — must express cleared, in-review, and blocked, and must be readable by the document list without a second request" | C1 (cleared), C4 (readable via badge) | partial — nothing establishes or tests the "blocked" state, and no criterion asserts the no-second-request property |

**Criteria serving no objective:** C2 ("A reviewer can change a document's retention date, with validation against the workspace's retention policy.") traces to neither objective.

## Compound criteria

- **"A reviewer can approve a document, and approving it notifies every subscriber by email."** (Acceptance Criteria #1) — splits into (a) *a reviewer can approve a document* and (b) *approving a document notifies every subscriber by email*. Approval alone is independently useful — it changes review state, which is what O1 needs — and shipping it without the notification is a complete, shippable slice. The notification is a separate deliverable layered on top. This is the compound shape the lens describes (approve/notify in different phases, either useful alone).

## Untestable criteria

None of the four criteria are unobservable on their face, but two rely on undefined terms that make the pass/fail boundary unclear:
- **"…notifies every subscriber by email."** — "subscriber" is never defined.
- **"…with validation against the workspace's retention policy."** — "the workspace's retention policy" is referenced but never defined.

## Contradictions

- **"No new runtime dependencies; the workspace ships stdlib only."** (Constraints) vs **"…notifies every subscriber by email."** (Acceptance Criteria #1) — flagged as a contradiction candidate rather than confirmed.

## Misplaced requirements

None found. The Open Questions item records a decision already made ("Accepted risk" + stated mitigation), so it falls under the settled-decision exception.

## Definitional collisions

- **"sign-off" (Objectives) vs "approve" (Acceptance Criteria #1)** — never tied together explicitly.
- **"subscriber"** (Acceptance Criteria #1) — used once, never defined.
- **"blocked"** (Required Interfaces) — named as a required state value but never produced by any criterion.

## Confidence

**high** — the spec is short and cleanly sectioned.
