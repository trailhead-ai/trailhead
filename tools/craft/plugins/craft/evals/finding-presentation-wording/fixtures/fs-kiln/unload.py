from datetime import datetime, timedelta

COOL_HOURS = {"large": 18, "small": 10}


def cool_hours(kiln: str) -> int:
    return COOL_HOURS[kiln]


def unload_time(firing: dict) -> datetime:
    return firing["fired_at"] + timedelta(hours=cool_hours(firing["kiln"]))


def ready_firings(firings: list[dict], now: datetime) -> list[str]:
    ready = []
    for firing in firings:
        if unload_time(firing) < now:
            ready.append(firing["name"])
    return ready


def board_lines(firings: list[dict], now: datetime) -> list[str]:
    lines = []
    for name in ready_firings(firings, now):
        lines.append(f"{name}: ready to unload")
    return lines
