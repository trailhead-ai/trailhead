from datetime import date

from reminders import reminder_lines

TODAY = date(2026, 6, 20)


def test_a_hive_inspected_a_month_ago_is_flagged():
    hives = [{"name": "Foxglove", "last_inspected": date(2026, 5, 20)}]
    assert reminder_lines(hives, TODAY) == ["Foxglove: inspection overdue"]
