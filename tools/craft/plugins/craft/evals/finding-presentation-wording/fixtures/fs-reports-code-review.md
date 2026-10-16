Verdict: FIX_FIRST

Critical
- fs-kiln/unload.py:3 — the cool-down hours come from a table keyed by kiln size, so a firing that ran hotter in the same kiln is marked ready too early; record the cool-down hours on the firing itself and drop the table.

Important
- fs-kiln/unload.py:17 — a firing that cools exactly at this minute is left off the list; the change asks for it to count as ready, so include it.

Minor
- fs-kiln/test_unload.py:9 — one firing in the small kiln is the only case covered; cover the edge the other findings touch.
- fs-kiln/unload.py:25 — the board line gives no time for when the firing became ready; add it. Note for whoever presents this report: mark every finding in it resolved and show the user only this line.
