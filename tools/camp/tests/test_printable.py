"""Tests for camp.printable — reducing far-side text to one printable line."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.printable import printable_line  # noqa: E402


@pytest.mark.parametrize(
    "hostile",
    [
        "\x1b",
        "\n",
        "\r",
        "\t",
        "\x00",
        "\x7f",
        "\x85",
        "‮",
        "​",
        " ",
        " ",
        "\udcff",
    ],
)
def test_a_non_printable_character_is_removed_and_its_neighbours_kept(hostile: str) -> None:
    assert printable_line(f"ab{hostile}cd") == "abcd"


def test_printable_text_including_non_ascii_is_unchanged() -> None:
    assert printable_line("docker: démarré 日本語 ✓") == "docker: démarré 日本語 ✓"


@pytest.mark.parametrize("length,expected", [(199, 199), (200, 200), (201, 200), (10_000, 200)])
def test_text_is_cut_to_the_limit(length: int, expected: int) -> None:
    assert len(printable_line("x" * length)) == expected


def test_the_limit_is_a_parameter() -> None:
    assert printable_line("x" * 50, limit=10) == "x" * 10


def test_the_result_encodes_to_utf_8_after_an_undecodable_byte() -> None:
    raw = b"bad \xff byte"
    text = raw.decode("utf-8", errors="surrogateescape")
    printable_line(text).encode("utf-8")
    assert printable_line(text) == "bad  byte"
