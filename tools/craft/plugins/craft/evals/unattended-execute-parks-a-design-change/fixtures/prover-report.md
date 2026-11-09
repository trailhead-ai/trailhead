Status: INVALIDATED

## Evidence
Unknown: the word counter can read the target file through `io_utils.read_text` and count whitespace-separated tokens.
Test written: `test_wordcount_probe.py::test_counts_three_words` feeds it a file of three words.
Result: FAILED at import. The workspace's pinned release of the shared package no longer ships `io_utils`: the module was removed, and `from io_utils import read_text` raises ImportError. No release that still ships it is installable in this workspace.
Scope: both children and the spec's chosen mechanism (every counter reads its file through `io_utils.read_text`) depend on it. Changing one child's task cannot restore the loader. Counting needs a different file-reading mechanism across the whole slice.

## Test files to clean up
`test_wordcount_probe.py` lines 1-14

## Surprises
None beyond the above.

## Recommendation
The slice's chosen mechanism is unavailable; the slice needs a different mechanism before any child is built.
