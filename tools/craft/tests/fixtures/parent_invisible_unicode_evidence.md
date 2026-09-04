**Value claim:** The observation's evidence text is a single U+200B ZERO WIDTH SPACE —
invisible in any editor, but not `str.isspace()`, so `str.strip()` alone treats it as
non-empty content.

**Covers:** AC9

## Criterion observations

- **AC9** — automated-assertion — ​
