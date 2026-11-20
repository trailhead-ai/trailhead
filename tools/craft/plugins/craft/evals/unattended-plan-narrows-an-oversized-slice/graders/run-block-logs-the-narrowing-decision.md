---
type: regex
pattern: '\n\d+\. \*\*(?:[^\n]*(?:[Nn]arrow|[Cc]ut|[Dd]rop|[Ss]iz|too large|oversiz)[^\n]*\n\s*- Question: [^\n]*|[^\n]*\n\s*- Question: [^\n]*(?:[Nn]arrow|[Cc]ut|[Dd]rop|[Ss]iz|too large|oversiz)[^\n]*)\n\s*- Choice: [^\n]*AC4[^\n]*AC5[^\n]*\n\s*- Reasoning: [^\n]*\S'
target:
  source: file
  path: vault/task/export-the-orders-to-a-csv-file.md
---
