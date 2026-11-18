---
type: regex
pattern: 'call 2: vault=levr spec=spec/export-invoices criterion=AC4 slice=task/export-pdf answer=failed base=dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd words=the PDF has no footer result=saved\n'
target:
  source: file
  path: answer.log
---
