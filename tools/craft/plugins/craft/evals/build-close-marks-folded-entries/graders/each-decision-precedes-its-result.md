---
type: regex
pattern: '^(?=(?:(?!found-work-result[^\n]*#F1 )[\s\S])*found-work: entry-done[^\n]*#F1 [\s\S]*found-work-result: written[^\n]*#F1 )(?=(?:(?!found-work-result[^\n]*#F2 )[\s\S])*found-work: entry-reopened[^\n]*#F2 [\s\S]*found-work-result: written[^\n]*#F2 )'
target:
  source: file
  path: vault/session/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.md
---
