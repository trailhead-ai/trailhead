---
type: regex
pattern: '^\{\s*"version":\s*1,\s*"entries":\s*\[\s*\{\s*"kind":\s*"spec",\s*"slug":\s*"tide-table-import"\s*\},\s*\{\s*"kind":\s*"task",\s*"slug":\s*"fix-the-ferry-delay-banner"\s*\},\s*\{\s*"kind":\s*"task",\s*"slug":\s*"archive-2024-timetables"\s*\},\s*\{\s*"kind":\s*"task",\s*"slug":\s*"crew-roster-export"\s*\},\s*\{\s*"kind":\s*"spec",\s*"slug":\s*"mooring-fee-tiers"\s*\},\s*\{\s*"kind":\s*"task",\s*"slug":\s*"replace-the-gangway-sensor"\s*\},\s*\{\s*"kind":\s*"task",\s*"slug":\s*"harbour-map-tiles",\s*"workspace":\s*\{\s*"group":\s*"eval",\s*"slug":\s*"harbour-map-tiles"\s*\}\s*\},\s*\{\s*"kind":\s*"task",\s*"slug":\s*"dock-camera-firmware"\s*\},\s*\{\s*"kind":\s*"task",\s*"slug":\s*"update-the-pier-signage-text"\s*\}\s*\]\s*\}\s*$'
target:
  source: file
  path: vault/sites/docket/lists/eval.json
---
