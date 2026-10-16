The `initials` branch is ready to merge. The reviewer said SHIP and found nothing Critical or Important. All 3 tests pass with `python3 -m unittest discover -s . -t .`.

`textutil/words.py:7` does what CHANGE.md asks. It splits the name on any whitespace, takes the first letter of each word and upper-cases it, and returns `""` for an empty name. Because of how it splits, a name that is only spaces also gives `""`, and extra spaces, tabs and newlines between words don't cause an error. The other two commits are small supporting changes: `tests/__init__.py` lets test discovery import the tests, and `.gitignore` now ignores bytecode. Nothing here touches security, so I didn't run a separate security audit.

## Rough edges, nothing to decide

None of these block the merge.

1. **Name made only of whitespace isn't tested** (`tests/test_words.py:13`). The code handles `"   "` correctly today, but no test pins it. If someone changed the split to `split(" ")`, `initials("   ")` would crash with an `IndexError` and the tests would still pass.
   Fix: add a test that `initials("   ")` and `initials("\t\n")` return `""`.

2. **Tabs and newlines between words aren't tested** (`tests/test_words.py:10`). CHANGE.md says words are separated by whitespace, but the only test for extra whitespace uses spaces. A change that only split on spaces would turn `"ada\tking"` into `"A"` without a test failing.
   Fix: add a test that `initials("ada\tking\nlovelace")` returns `"AKL"`.

3. **Mixed-case input isn't tested** (`tests/test_words.py:7`). Every test name is all lowercase, so nothing shows that input already in capitals or mixed case comes out right.
   Fix: add a test that `initials("Ada lOVELACE")` returns `"AL"`.

4. **Some unusual names give odd initials** (`textutil/words.py:7`). Upper-casing `"ß"` gives `"SS"`, so one word adds two letters. A word that starts with punctuation adds that punctuation: `"(ada)"` gives `"("`.
   Fix: if names like these can come up, handle them. Otherwise say in the docstring that this is how it behaves.

5. **Passing something that isn't a string fails with an unclear error** (`textutil/words.py:5`). `initials(None)` raises `AttributeError`. `word_count` next to it behaves the same way, so this matches the module.
   Fix: optionally say in the docstring that `name` must be a string.

If you want them, items 1–3 are three one-line tests and I can add them before you merge.
