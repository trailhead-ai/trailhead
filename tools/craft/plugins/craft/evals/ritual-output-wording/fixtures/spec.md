# Spec: Overdue handling for the Fernhill lending library

Status: ready

## Acceptance criteria

**AC1.** A borrower whose loan passes its due date is shown as overdue on the desk screen within one minute of the date rolling over.

**AC2.** The daily late fee is charged per overdue item at the rate set for that item's category, and never exceeds the replacement price of the item.

**AC3.** A borrower owing more than ten pounds in late fees cannot check out further items until the balance is cleared or a librarian overrides.

**AC4.** A librarian override records who granted it and the reason given, and shows in the borrower's history.

**AC5.** Returning an overdue item stops the fee from growing the same day, even if the return is scanned after closing time.

**AC6.** A borrower receives one reminder message three days before a due date and one on the day after, and no other automatic messages about the same loan.

**AC7.** The monthly fees report lists each category's total charged and total waived, and the two totals reconcile with the per-borrower ledgers.

## Non-goals

Online payment of fees.
