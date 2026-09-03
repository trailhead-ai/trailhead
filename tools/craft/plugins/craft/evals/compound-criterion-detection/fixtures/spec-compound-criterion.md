# spec/document-review-sign-off

status: draft

## Problem

Legal review of contract documents happens over email. A reviewer receives a PDF,
marks it up, and replies; the document owner reconciles replies by hand and keeps a
spreadsheet of who has signed off. Nobody can answer "is this document cleared?"
without reading the spreadsheet, and the spreadsheet is wrong often enough that the
contracts team re-asks reviewers by hand before every filing.

The review surface now exists in the web app: a reviewer can open a document and see
its current review state, served end to end against the `documents` service.

## Objectives

Give the contracts team a single authoritative answer to "is this document cleared?",
and give reviewers a way to record sign-off without leaving the app.

## Acceptance Criteria

1. A reviewer can approve a document, and approving it notifies every subscriber by
   email.
2. A reviewer can change a document's retention date, with validation against the
   workspace's retention policy.
3. A reviewer can leave a comment on a document.
4. The document list shows each document's review state as a coloured badge rather
   than a text label.

## Required Interfaces

- **The review-state field on a document** — must express cleared, in-review, and
  blocked, and must be readable by the document list without a second request.

## Non-goals

- Reviewer-to-reviewer threaded discussion.
- Integration with the external filing system.

## Constraints

- No new runtime dependencies; the workspace ships stdlib only.

## UI Direction

n/a

## Open Questions / Risks

- Accepted risk: a reviewer who loses access mid-review leaves a document in
  in-review indefinitely. Mitigation is the existing stale-document sweep.
