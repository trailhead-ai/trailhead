Verdict: FIX_FIRST

Critical
- hive-reminders/reminders.py:13 — a hive whose last_inspected is None raises TypeError inside next_due, so one newly installed hive stops the whole morning list; list such hives as overdue instead.

Important
- hive-reminders/reminders.py:13 — the comparison uses < so a hive due exactly today is left off the list; the change asks for due today or earlier, so use <=.

Minor
- hive-reminders/test_reminders.py:8 — only a hive inspected a month ago is covered; add a case for a hive with no inspection and one due exactly today.
- hive-reminders/reminders.py:21 — the board line says only "inspection overdue"; say since which date so the committee can tell a day late from a month late.
