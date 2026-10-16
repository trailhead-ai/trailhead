# Plan: kiln booking sheet, first build

Spec: FIXTURES_DIR/fs-spec.md

Goal: members book kiln firings online, reminders go out the evening before, and the calendar can be
read by the technician and the committee.

Tasks:
1. Booking table, booking form and confirmation email. A row holds the member, the kiln, the day and
   a `turn` number; booking takes the next number for that kiln. Cancelling closes the gap by
   subtracting one from every later `turn` (AC1, AC2, AC3).
2. Nightly reminder job at 18:00: for each booking whose day is tomorrow, email the member a reminder
   with the list of what goes on the shelves (AC5).
3. Cancellation: the confirmation email carries a link that cancels the booking when it is opened,
   frees the day and offers it to the first member on the standby list (AC3).
4. Technician screen: pick a kiln, a range of days and a reason, then confirm. The lock is stored
   with the reason and the technician's name (AC4).
5. Calendar page: one table of events for each kiln, newest first (AC6).

Notes: the confirmation and reminder emails are sent from the studio's shared mailbox. The 90 members
are loaded from the studio's membership spreadsheet before launch.
