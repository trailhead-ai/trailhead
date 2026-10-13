"""Round-trip, start-line refusal and stored-diff replay for the unified-diff appliers."""

import pytest

from lore.record import store as rs

CORPUS = [
    ("", "a\nb\n"),
    ("a\nb\n", ""),
    ("a\nb\n", "a\nb\n"),
    ("a\nb", "a\nc"),
    ("a\nb\n", "a\nb"),
    ("a\nb", "a\nb\n"),
    ("a\n-- x\n++ y\nz\n", "a\n-- q\n++ y\nz\n"),
    ("a\nb\n", "a\n-- b\n"),
    ("a\r\nb\r\nc\r\n", "a\r\nX\r\nc\r\n"),
    ("a\r\nb", "a\r\nc"),
    ("a\rb\nc\n", "A\rb\nc\n"),
    ("a\rb\nc\n", "a\rB\nc\n"),
    ("a\r", "b\r"),
    ("x\ny\r", "x\nz\r"),
    ("p\rq\rr\n", "p\rQ\rr\n"),
    ("a\r\nb\rc\n", "a\r\nb\rd\n"),
    ("a b\nc\n", "a b\nC\n"),
    ("a\x0cb\nc\n", "a\x0cB\nc\n"),
    ("a\x85b\nc\n", "a\x85b\nC\n"),
    ("a\x1eb\nc\n", "a\x1eb\nC\n"),
    ("a\x0c", "a\x0cb"),
    (
        "".join(f"l{i}\n" for i in range(40)),
        "".join(f"l{i}\nextra{i}\n" for i in range(5)) + "".join(f"l{i}\n" for i in range(5, 38)),
    ),
    (
        "".join(f"l{i}\n" for i in range(40)),
        "".join(f"l{i}\n" if i not in (2, 20, 37) else f"L{i}\n" for i in range(40)),
    ),
]


@pytest.mark.parametrize("old,new", CORPUS)
def test_apply_and_reverse_round_trip(old, new):
    diff = rs.make_unified_diff(old, new)
    assert rs.apply_unified_diff(old, diff) == (new, [])
    assert rs.reverse_unified_diff(new, diff) == old


def test_reverse_refuses_body_that_is_not_post_image():
    old, new = "a\nb\nc\nd\n", "a\nB\nc\nd\n"
    diff = rs.make_unified_diff(old, new)
    altered = "a\nB\nX\nd\n"
    with pytest.raises(rs.DiffRejectError):
        rs.reverse_unified_diff(altered, diff)
    assert altered == "a\nB\nX\nd\n"


def test_reverse_refuses_pre_image():
    old, new = "a\nb\nc\n", "a\nB\nc\n"
    with pytest.raises(rs.DiffRejectError):
        rs.reverse_unified_diff(old, rs.make_unified_diff(old, new))


ZERO_START = "--- a\n+++ b\n@@ -0,1 +0,1 @@\n-c\n+X\n"


def test_apply_refuses_zero_start_hunk():
    with pytest.raises(rs.DiffRejectError):
        rs.apply_unified_diff("a\nb\nc\n", ZERO_START)


def test_reverse_refuses_zero_start_hunk():
    with pytest.raises(rs.DiffRejectError):
        rs.reverse_unified_diff("a\nb\nX\n", ZERO_START)


def test_apply_zero_start_does_not_touch_last_line():
    body = "a\nb\nc\n"
    with pytest.raises(rs.DiffRejectError):
        rs.apply_unified_diff(body, ZERO_START)
    assert body == "a\nb\nc\n"


def test_pure_insertion_into_empty_body_applies_and_reverses():
    diff = "--- a\n+++ b\n@@ -0,0 +1,1 @@\n+hello\n"
    assert rs.apply_unified_diff("", diff) == ("hello\n", [])
    assert rs.reverse_unified_diff("hello\n", diff) == ""


def test_pure_deletion_to_empty_body_reverses_via_mirror_carve_out():
    diff = "--- a\n+++ b\n@@ -1,1 +0,0 @@\n-hello\n"
    assert rs.apply_unified_diff("hello\n", diff) == ("", [])
    assert rs.reverse_unified_diff("", diff) == "hello\n"


def test_zero_start_with_nonempty_old_side_refused_even_when_pure_insertion_shape():
    diff = "--- a\n+++ b\n@@ -0,0 +1,1 @@\n+hello\n"
    # carve-out applies to the diff's shape, so an insertion at start 0 into a
    # non-empty body must still not corrupt: it is only valid for the empty side.
    with pytest.raises(rs.DiffRejectError):
        rs.apply_unified_diff("x\n", diff)


# Literal diffs emitted by the producer before the hardening.
STORED = [
    (
        "a\nb\nc\n",
        "a\nB\nc\n",
        "--- a\n+++ b\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n",
    ),
    (
        "a\x0cb\nc d\ne\n",
        "a\x0cb\nC d\ne\n",
        "--- a\n+++ b\n@@ -1,5 +1,5 @@\n a\x0c\n\\ No newline at end of file\n b\n"
        "-c \n\\ No newline at end of file\n+C \n\\ No newline at end of file\n d\n e\n",
    ),
]


@pytest.mark.parametrize("old,new,diff", STORED)
def test_previously_stored_diffs_apply_and_reverse(old, new, diff):
    assert rs.apply_unified_diff(old, diff) == (new, [])
    assert rs.reverse_unified_diff(new, diff) == old


# Context-only hunks match the body on both the old and the new side, so each
# passes its own verbatim check and only the relation between hunks can refuse it.
_LINES10 = "".join(f"l{i}\n" for i in range(1, 11))
_OVERLAPPING = (
    "--- a\n+++ b\n@@ -2,3 +2,3 @@\n l2\n l3\n l4\n@@ -3,3 +3,3 @@\n l3\n l4\n l5\n"
)
_OUT_OF_ORDER = (
    "--- a\n+++ b\n@@ -5,2 +5,2 @@\n l5\n l6\n@@ -2,2 +2,2 @@\n l2\n l3\n"
)
_ADJACENT = (
    "--- a\n+++ b\n@@ -2,2 +2,2 @@\n l2\n l3\n@@ -4,2 +4,2 @@\n l4\n l5\n"
)


@pytest.mark.parametrize("replay", [rs.apply_unified_diff, rs.reverse_unified_diff])
def test_overlapping_hunks_are_refused(replay):
    with pytest.raises(rs.DiffRejectError) as exc:
        replay(_LINES10, _OVERLAPPING)
    assert [h for h, _ in exc.value.rejected] == ["@@ -3,3 +3,3 @@"]
    assert "overlaps" in exc.value.rejected[0][1]


@pytest.mark.parametrize("replay", [rs.apply_unified_diff, rs.reverse_unified_diff])
def test_out_of_order_hunks_are_refused(replay):
    with pytest.raises(rs.DiffRejectError) as exc:
        replay(_LINES10, _OUT_OF_ORDER)
    assert [h for h, _ in exc.value.rejected] == ["@@ -2,2 +2,2 @@"]
    assert "out of order" in exc.value.rejected[0][1]


@pytest.mark.parametrize("replay", [rs.apply_unified_diff, rs.reverse_unified_diff])
def test_touching_hunks_are_not_overlapping(replay):
    result = replay(_LINES10, _ADJACENT)
    assert (result[0] if isinstance(result, tuple) else result) == _LINES10
