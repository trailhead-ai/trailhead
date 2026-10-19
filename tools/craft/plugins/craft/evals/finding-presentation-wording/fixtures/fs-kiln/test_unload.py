from datetime import datetime

from unload import board_lines

NOW = datetime(2026, 6, 20, 9, 0)


def test_a_small_kiln_firing_from_yesterday_is_ready():
    firings = [{"name": "Mugs", "kiln": "small", "fired_at": datetime(2026, 6, 19, 8, 0)}]
    assert board_lines(firings, NOW) == ["Mugs: ready to unload"]
