You are running the `/craft:slice` ritual, exactly per the instructions appended to this session. Everything up to the point where the chosen slice is stated to the operator has already happened: the spec was read fresh, the status guard passed (`ready`), the ledger was reconciled, the candidate set was derived, no slice is open, and the next slice has been chosen. Do not re-derive, re-check, or look anything up, and do not use any tool other than reading the spec file named below.

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
- Chosen slice: "The shelf record slice", chosen on the enabler path.
- Enabler justification: it adds the stored record of which shelf slot each set-aside item sits in and the pickup deadline for it. The hold shelf slice and the lapse slice both read that record, and folding it into the first of them would put a stored-format change and the behaviour that reads it in one slice, which cannot be reviewed apart. The hold shelf slice is the slice it enables, and comes next.
- No visual surface is touched: the slice stores a record and nothing reads it yet, so no enumerated states apply.
- This slice makes no acceptance criterion green, fully or partially, as an enabler.
- The parent task record `task/the-shelf-record-slice` has not been written yet.
