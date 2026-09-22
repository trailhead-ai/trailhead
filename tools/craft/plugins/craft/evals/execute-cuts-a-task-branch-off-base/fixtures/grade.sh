#!/usr/bin/env bash
# Grade one finished run from the fixture repo's on-disk git state.
#
#   ./grade.sh <fresh|dirty|collision|design-doc> <repo-dir>
#
# Prints PASS or FAIL plus the observed facts. Grades the repository, not the
# run's transcript: the branch checked out, what it contains, and what it lacks.
set -uo pipefail

variant="${1:?variant}"
repo="${2:?repo dir}"
g() { git -C "$repo" "$@"; }

branch="$(g symbolic-ref --short HEAD 2>/dev/null || echo DETACHED)"
has() { g cat-file -e "HEAD:$1" 2>/dev/null; }
local_branch_exists() { g show-ref --verify --quiet "refs/heads/$1"; }
facts="branch=$branch upstream_in_head=$(has UPSTREAM.md && echo y || echo n) prior_in_head=$(has PRIOR.md && echo y || echo n) dirty=$(g status --porcelain --untracked-files=no | grep -q . && echo y || echo n)"

verdict=FAIL
case "$variant" in
  fresh)
    [ "$branch" = fixture-add-a-readme-line ] && has UPSTREAM.md && ! has PRIOR.md && verdict=PASS
    ;;
  dirty)
    [ "$branch" = work ] && ! local_branch_exists fixture-add-a-readme-line \
      && g status --porcelain --untracked-files=no | grep -q 'README.md' && verdict=PASS
    ;;
  collision)
    [ "$branch" = work ] && ! local_branch_exists fixture-add-a-readme-line && verdict=PASS
    ;;
  design-doc)
    carried="$(g log --format=%H origin/main..HEAD 2>/dev/null | wc -l | tr -d ' ')"
    touched="$(g diff --name-only origin/main HEAD 2>/dev/null | tr '\n' ' ')"
    facts="$facts carried=$carried touched=[$touched]"
    [ "$branch" = fixture-parent-with-design-doc ] && has UPSTREAM.md && ! has PRIOR.md \
      && [ "$carried" = 1 ] && [ "$touched" = "docs/design/fixture-slice.md " ] && verdict=PASS
    ;;
esac
echo "$verdict $variant $facts"
