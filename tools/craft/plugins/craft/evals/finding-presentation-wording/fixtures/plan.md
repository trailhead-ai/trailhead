# Plan: allotment waiting list, first build

Spec: FIXTURES_DIR/spec.md

Goal: people join the waiting list online, freed plots are offered in order, and the queue's
history can be read by the committee.

Tasks:
1. Queue table, join form and confirmation email. A row holds the person, the site, and a `place`
   number; joining takes the next number for that site. Leaving closes the gap by subtracting one
   from every later `place` (AC1, AC5).
2. Nightly offer job at 02:00: for each freed plot, email the row with the lowest `place` for that
   site, and mark the offer open for 14 days. Offers older than 14 days are marked lapsed, the
   person's `place` is set to the highest number plus one, and the next offer is sent (AC2, AC3).
3. Warden screen: pick a freed plot and a person, type a reason, confirm. The grant is stored with
   the reason and the warden's name (AC4).
4. Import: load the paper list from the committee's spreadsheet, about 340 rows, in one run from
   the warden's laptop. The importer inserts one queue row per spreadsheet row and writes nothing
   else.
5. History page: one table of events for each site, newest first (AC6).

Notes: the offer email carries a link that accepts the offer when opened. The confirmation email
and the offer email are sent from the society's shared mailbox.
