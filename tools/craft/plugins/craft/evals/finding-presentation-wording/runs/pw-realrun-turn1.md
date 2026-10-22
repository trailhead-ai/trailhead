I've finished the spec review (gauntlet) of *A telescope loan desk for the Hollin Hill astronomy club*. All eight checks ran. The spec is rated production maturity, which its own Maturity section sets. All my drafted edits pass the criterion gate.

**What the checks found.** The spec fixes the double-booking problem but not the other two problems it names. Nothing records who physically held a telescope or what state it was in when it went out. So when a mirror turns up scratched, the history still can't say whose loan did it. The scratched mirror is the main reason the spec exists. Nothing gives the loan officer a view of which telescopes are out, which was the second problem. Under those gaps sit some missing decisions:
- How long a loan lasts, so the return date that four criteria rely on is never set.
- Who a "member" is: anyone can type any name and email, then borrow or cancel as someone else.
- How anyone joins the waiting list, or what happens when an offered member never replies.
- How the loans already in the paper ledger get online.
- What happens when an email fails.

**Whether that holds.** The findings are strong. Checks that couldn't see each other's work reached the same core issues: the scratched-mirror gap came up in six checks, the waiting list in six, the loan length in five. I checked each claim against the spec text myself.
- The fact check confirmed only that nothing exists yet to build on. The six facts about the club appear only in the spec, so they can't be confirmed or refuted; that is not evidence either way.
- The right-problem check went furthest: it asked whether this is detection or plain scarcity, and whether a six-telescope club needs production-grade software. Those are fair questions but they don't stop the build, so I logged them as worth fixing rather than as framing errors.
- The security check alone raised personal-data retention. Because members' names and emails would stay in a permanent history, I kept it at the top level.
- The self-consistency check found four criteria that bundle outcomes which could ship separately, so I split them.

**What to do.** Edits can fix every finding, so none needs a rewrite. I propose one combined edit:
- Add a handover step with a condition note.
- Record who made each history entry, and never edit or delete entries.
- Set a fixed club loan length (I drafted 7 days; your call).
- Confirm each borrower's email by link, and give loan officers their own sign-in.
- Spell out the waiting-list offer, held 24 hours before passing on.
- Add an officer view of what's out and overdue.
- Add a go-live import of the paper ledger and a printable list for falling back to paper.
- Make failed emails visible to the officer, with a retry.
- Set a retention rule for personal data.
- Split the bundled criteria.

The criteria grow from 6 to 24, and the spec gains a Dependencies section and one Non-Goal.

**Recommended outcome:** apply all nine edits below. The spec then becomes `ready` this round, with no rewrite round needed.

| finding | proposed outcome | proposed edit |
|---|---|---|
| *Scratched mirror still can't be traced to a borrower* (C1) | fixed in the spec | *add the handover with condition note (AC12) and the return note (AC13); history records who acted (AC17) and is never edited (AC18); widen the Objectives* |
| *Loan length and return date never defined* (C3) | fixed in the spec | *add the club loan length of 7 days, changeable by the officer (AC4); overlap rule (AC5); next free date fits a whole loan (AC6)* |
| *Waiting-list offers can hang forever; no way to join the list* (C4) | fixed in the spec | *add joining after a refusal (AC7); offer held 24h, then passed to the next person or the dates freed (AC11)* |
| *Paper-ledger loans never imported; no way back to paper* (C5) | fixed in the spec | *add the go-live import of telescopes and open loans (AC23); printable list of open loans for going back to paper (AC24)* |
| *Failed emails go unnoticed* (C6) | fixed in the spec | *add saving the loan before emailing, plus retry and a resend list for the officer (AC22); new Dependencies section naming email, a daily scheduled run and officer accounts* |
| *Loan officer still can't see what's out* (C7) | fixed in the spec | *add the officer's view of free, held, out and overdue (AC14); the member calendar without names (AC15)* |
| *Four criteria bundle outcomes that could ship separately* (C9) | fixed in the spec | *split borrowing, confirmation, refusal, next-free-date, cancellation, offer, return and calendar into separate criteria (AC1–AC16)* |

**Anyone can borrow or cancel as someone else (C2)**, from the security check. Proposed: fixed in the spec. Borrowers are identified only by a name and email they type, and nothing says who may cancel, record returns or read the history. So anyone can cancel another member's loan, or borrow in a stranger's name. This also breaks the "right member" objective.
Fix (actual text):
- **AC2.** "A requested loan is held only once the member opens the link emailed to that address. A request not confirmed within 24 hours is dropped and holds no dates."
- **AC8.** "Only someone holding a loan's emailed link can open the loan's page, which shows its status: requested, held, out, returned, overdue or cancelled."
- **AC9.** "From the loan's page a member can cancel the loan until 48 hours before midnight, club local time, at the start of its start date. A later cancellation is refused with that reason and the loan officer's contact details."
- **AC19.** "Recording handovers, returns and corrections, changing the loan length, and viewing the loan history or borrowers' names are open only to loan officers, each signing in with their own account."
- New Non-Goal: "Checking borrowers against a membership roll. A borrower is identified by a confirmed email address and by the loan officer at handover."

**Members' personal data has no retention rule (C8)**, from the security check. Proposed: fixed in the spec. Names and emails would sit permanently in a history that can never be edited. A member who asks to be removed couldn't be, and addresses could leak into logs.
Fix (actual text):
- **AC20.** "Members' names and email addresses are shown only to loan officers and, on a loan's own page, to the holder of its link, and are never written to the desk's logs."
- **AC21.** "On a member's request, or two years after their last loan, their name and email address in the history are replaced with a placeholder; the events and condition notes stay."

Reply go, or dispute C2: <why>, accept C2 as a risk: <why>, answer C2: <what you know>.

**Lower-severity findings:** these need no decision and will be kept in the spec's review record.
- **6 worth fixing, your call:**
  - Is this a scarcity problem rather than a visibility one?
  - Production rigour for six telescopes, with no named owner.
  - The waiting list and the case packing list trace to no stated problem.
  - Form input validation and rate limits.
  - Backup and restore.
  - Offering other telescopes when a request is refused.
- **5 rough edges:**
  - A per-telescope kit list.
  - Making the calendar accessible and usable on a phone.
  - How condition notes display.
  - The unused unit name in the Maturity section.
  - Every criterion is checked by hand only.

The full detail on any finding is one request away.
