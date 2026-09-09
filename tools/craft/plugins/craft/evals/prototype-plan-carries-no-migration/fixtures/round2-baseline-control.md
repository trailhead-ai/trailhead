# Fixture — round2-baseline-control

A planning session paused at step 7 ("Define Tasks"), immediately before the feature is
broken into tasks. The spec under review follows. Read this file in full, then perform
step 7 on it.

This is the corrected `control.md`'s spec, byte-identical, but carries **no** "Migration bar
(rendered)" section — the second-round baseline-arm material for the control fixture. The
treatment arm keeps reading `control.md` unchanged.

## Spec under review

# Spec — replace the widget's category field with a tags list

**Problem.** Every widget currently carries one free-text `category` field. Users want to
apply several tags to a widget instead of being limited to one category.

**Objectives.** Add a `tags: list[str]` field to every widget and stop reading `category`
for new writes.

## Acceptance Criteria

- **AC1.** New widgets are created with a `tags` list instead of a `category` value.
- **AC2.** The widget list view groups by tag instead of by category.
- **AC3.** Existing widgets may keep an empty `tags` list after this ships, and a user may
  re-tag them by hand later. No automated preservation of prior `category` values is
  required.

## Maturity

- trailhead: production
