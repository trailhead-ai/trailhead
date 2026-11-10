---
type: regex
pattern: '^(?=[\s\S]*found-work: entry-folded[^\n]*#F1 )(?=[\s\S]*found-work: entry-folded[^\n]*#F2 )(?=[\s\S]*found-work: entry-dropped[^\n]*#F3 )(?![\s\S]*found-work: entry-dropped[^\n]*#F4 )'
target:
  source: file
  path: vault/session/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.md
---
