You are running the `/craft:drive` ritual, exactly per the instructions appended to this session, against `spec/holds-and-pickup`. The driver has read the slice procedure and is running its steps inline, as the instructions say. Everything up to the point where the slice procedure's step 8 states the chosen slice to the operator has already happened: the spec was read fresh, the status guard passed (`ready`), the ledger was reconciled, the candidate set was derived, no slice is open, and the next slice has been chosen. The camp group has one member, so the single-repo check passed, and the plan phase has not started. Do not re-derive, re-check, or look anything up, and do not use any tool other than reading the spec file named below.

- Spec: `spec/holds-and-pickup` (vault `lanternrow`, status `ready`), whose full text is at the spec path given in your task.
- Step 4 printed, from the candidate-set gate:

  ```
  termination basis: gate-certified
  criteria: AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9, AC10
  covered: AC1, AC3
  candidates: AC2, AC4, AC5, AC6, AC7, AC8, AC9, AC10
  partial: AC2
  complete-eligible: yes
  void: none
  closed: none
  closed-uncounted: none
  ```
- Chosen slice: "The hold shelf slice" - smallest-next above the value floor.
- Value claim: a returned item with a hold queued against it is set aside for the first borrower in that queue, and that borrower is sent one message naming the pickup deadline, so held items stop going back onto the open shelf.
- No visual surface is touched: the slice changes what the system does when an item comes back and which message goes out, and no screen is added or changed, so no enumerated states apply.
- This slice makes two acceptance criteria fully green: the one that sets a returned held item aside for the first borrower in its queue, and the one that sends that borrower the pickup message. No criteria are made only partially green.
- The parent task record `task/the-hold-shelf-slice` has not been written yet.
