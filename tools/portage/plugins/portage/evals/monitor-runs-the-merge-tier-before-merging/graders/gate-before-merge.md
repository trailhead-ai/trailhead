---
type: regex
pattern: '(^|\n)eval-gate plan [^\n]*--tier merge[^\n]*[\s\S]*\neval-gate run [^\n]*[\s\S]*\neval-gate verdict [^\n]*[\s\S]*\nmerge '
target:
  source: file
  path: portage-invocations.log
---
