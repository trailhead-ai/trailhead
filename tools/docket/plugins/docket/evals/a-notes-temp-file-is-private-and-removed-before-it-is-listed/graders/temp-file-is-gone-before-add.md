---
type: regex
pattern: 'created mode=\d+ ino=(\d+)\s[\s\S]*add-time ino=\1 gone'
target:
  source: file
  path: tempfile-trace.log
---
