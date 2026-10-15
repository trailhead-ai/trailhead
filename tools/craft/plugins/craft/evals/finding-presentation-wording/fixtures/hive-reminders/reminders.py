from datetime import date, timedelta

INSPECTION_GAP_DAYS = 14


def next_due(last_inspected: date) -> date:
    return last_inspected + timedelta(days=INSPECTION_GAP_DAYS)


def overdue_hives(hives: list[dict], today: date) -> list[str]:
    overdue = []
    for hive in hives:
        if next_due(hive["last_inspected"]) < today:
            overdue.append(hive["name"])
    return overdue


def reminder_lines(hives: list[dict], today: date) -> list[str]:
    lines = []
    for name in overdue_hives(hives, today):
        lines.append(f"{name}: inspection overdue")
    return lines
