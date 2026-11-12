Status: INVALIDATED

## Evidence
Unknown: the word counter can read the target file through `io_utils.read_text` and count whitespace-separated tokens.
Test written: `test_wordcount_probe.py::test_counts_three_words` feeds it a file of three words, then a Latin-1 file from the shared fixtures.
Result: the UTF-8 file passes, the Latin-1 file FAILED. `io_utils.read_text` raises UnicodeDecodeError on bytes that are not valid UTF-8, so the word counter as written cannot count the Latin-1 fixtures.
Scope: the line counter child reads its lines through its own helper and is not affected. The loader itself works and stays as the slice's mechanism. A local change fixes the word counter: have it open the file with `errors="replace"` instead of calling `read_text`. No other child, and no acceptance criterion, changes.

## Test files to clean up
`test_wordcount_probe.py` lines 1-14

## Surprises
None beyond the above.

## Recommendation
Adjust the word counter's task: it opens the file itself with a tolerant decode.
