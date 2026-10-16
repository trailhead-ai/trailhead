The `late-fees` branch shouldn't merge yet. Early returns get a negative fee, and the receipt text doesn't match the format CHANGE.md asks for. The one test passes but covers neither problem. I reran both problems myself and they reproduce.

I followed the review steps: ran the suite first (1 test, passes), then sent the full `1aa8964..625da3f` diff and CHANGE.md to the code reviewer. Its verdict is FIX_FIRST.

## Would build the wrong thing, needs your decision

**1. A book returned early gets a negative fee instead of costing nothing.** I recommend fixing this before merge, or the receipt shows money owed to the borrower. — `loans/fees.py:11-12`
Worst case: a book returned 10 days after borrowing comes out at `late_fee == -1.1` (`-0.55` on a child card), and the receipt prints `$-1.1` for a book that was on time. The days late are never floored at zero.
Fix: clamp the days late to zero, e.g. `max(days_late(...), 0)`.
*Raised by: code review*

## Would cause trouble later, worth fixing, your call

**2. The receipt drops the trailing zero, so it doesn't match the spec's own example.** — `loans/fees.py:21`
Worst case: `fee_line("Dune", 1.5)` prints `Dune: $1.5` where CHANGE.md says `Dune: $1.50`. A capped fee prints `$5.0`.
Fix: format as `f"{title}: ${fee:.2f}"`.
*Raised by: code review*

**3. The tests miss every rule CHANGE.md states.** — `tests/test_fees.py:8-9`
Worst case: both bugs above passed the suite. These cases have no test:
- an early return
- the day 21 / day 22 boundary
- the $5 cap
- the child card on its own
- the child card and the cap together
- anything in `fee_line`

Fix: add a test for each of those cases.
*Raised by: code review*

**4. Whether a child card is capped before or after halving isn't decided anywhere.** — `loans/fees.py:13-16`
Today the code caps first and then halves, so a child card never pays more than $2.50. CHANGE.md could also mean "halve, then cap at $5". The current reading seems reasonable, but if someone swaps the order later, nothing will catch it.
Fix: confirm the $2.50 child cap is what you want, and add a test that fixes it in place.
*Raised by: code review*

## Rough edges, nothing to decide

- **Money is stored as float dollars.** `round()` hides the error at today's rate, and no wrong outputs were found. A rate like 7.5¢ would start rounding wrong. Consider integer cents or `Decimal`. — `loans/fees.py:2-3,12,17`
- **A return date earlier than the borrow date (a data-entry error) is accepted silently.** Once item 1 is fixed it will cost 0 rather than raise an error. Decide whether it should fail instead. — `loans/fees.py:10`
- **A compiled bytecode file is committed.** `loans/__pycache__/__init__.cpython-312.pyc` is tracked and there's no `.gitignore`. Remove it and ignore `__pycache__/`. — `loans/__pycache__/`

The change has no security-sensitive code, so no security audit was needed.

Do you want me to fix 1–3 and add the tests now, or will you do it? For item 4, tell me if you'd rather the cap apply after halving. Otherwise I'll keep cap-then-halve and add the test for it.
