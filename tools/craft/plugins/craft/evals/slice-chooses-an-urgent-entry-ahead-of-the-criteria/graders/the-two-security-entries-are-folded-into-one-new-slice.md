---
type: regex
pattern: '\*\*F1\.\*\*[^\n]*\n\s*- status: folded into task/((?!print-)[a-z0-9-]+)\n[\s\S]*?\*\*F2\.\*\*[^\n]*\n\s*- status: folded into task/\1\n'
target:
  source: file
  path: vault/spec/route-planner.md
---
