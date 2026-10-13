# Plan: overdue handling, first build

Goal: the desk screen flags overdue loans and the system charges and blocks on late fees.

Tasks:
1. Nightly job marks loans past their due date as overdue and writes the flag to the loan table.
2. Fee calculator: per-category daily rate, capped at replacement price; run by the same nightly job and again when an item is returned.
3. Checkout guard: refuse a checkout when the borrower's fee balance exceeds ten pounds; a librarian can override with a typed reason, stored as a note on the borrower.
4. Desk screen shows the overdue flag and the fee balance, refreshed on each screen load.

Notes: the nightly job runs at 00:05 local time. Returns scanned after closing are stamped with the next morning's date.
