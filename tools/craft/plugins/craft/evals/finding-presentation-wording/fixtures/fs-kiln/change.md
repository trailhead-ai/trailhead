# Change: unload notices for the pottery studio

Branch `kiln-unload`, two files under `fs-kiln/`: `unload.py` and `test_unload.py`.

## What it was meant to do

The studio's kilns are fired by the technician. Each firing is a record with a `name`, a `kiln` (`large`
or `small`) and a `fired_at` time. A kiln cools more slowly the longer its firing ran, so a firing that
ran to a higher temperature needs longer before it is safe to open.

The committee asked for a notice on the studio board listing the firings that are ready to unload:

- A firing is ready once its cool-down time has passed since `fired_at`.
- A firing that cools exactly at this minute appears on the board as ready.
- The cool-down depends on how the firing ran, not only on which kiln it was in.
- The board line reads `<name>: ready to unload`, and gives the time it became ready.
- The list is built from the firing records and the current time alone.

## What was built

`ready_firings` returns the names of the firings whose cool-down has passed. `board_lines` turns each
name into a board line. The cool-down hours come from a table keyed by the kiln. One test covers a
firing in the small kiln.
