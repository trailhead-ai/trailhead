---
type: regex
pattern: '## Non-Goals\n\n- The tool never writes a file of any kind: no report file, no cache, no log file\. Terminal output is the only output\.\n- The tool takes no output-location option\.\s*$'
target:
  source: file
  path: vault/spec/report-export.md
---
