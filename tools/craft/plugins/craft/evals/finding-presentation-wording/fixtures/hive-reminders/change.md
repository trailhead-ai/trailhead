# Change: inspection reminders for the apiary society

Branch `hive-reminders`, two files under `hive-reminders/`: `reminders.py` and `test_reminders.py`.

## What it was meant to do

The society's apiary keeps a list of hives. Each hive is a record with a `name` and a `last_inspected` date. A
hive that has just been installed has no inspection yet, so its `last_inspected` is `None`.

The committee asked for a morning reminder list on the clubhouse board:

- A hive is due for a visit 14 days after its last inspection.
- A hive that is due today or earlier appears on the reminder list as `<name>: inspection overdue`.
- A hive with no inspection yet appears on the list too, since it has never been checked.
- The list is built from the hive records and today's date alone.

## What was built

`overdue_hives` returns the names of the hives whose next due date has passed. `reminder_lines` turns each name
into a board line. One test covers a hive last inspected a month ago.
