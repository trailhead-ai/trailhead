#!/usr/bin/env bash
# Materialise a vanilla-path fixture repo for the workspace-preflight eval case.
#
#   ./make-fixture-repo.sh <behind|upstream-gone|current|fetch-failed> <dest-dir>
#
# Every variant produces a local `origin` clone source at "<dest>-origin" and
# a working repo at "<dest>" whose "origin" remote points at it (except
# fetch-failed, which repoints the remote after cloning). Prints nothing on
# success other than a final status line; the working repo is left checked
# out on branch "work".
set -euo pipefail

variant="${1:?variant required: behind | upstream-gone | current | fetch-failed}"
dest="${2:?destination directory required}"
origin="${dest}-origin"

case "$variant" in
  behind|upstream-gone|current|fetch-failed) ;;
  *) echo "unknown variant: $variant" >&2; exit 2 ;;
esac

rm -rf "$dest" "$origin"
mkdir -p "$origin"

# --- the "origin" repo: a bare-equivalent local remote --------------------
(
  cd "$origin"
  git init -q .
  git config user.email test@test.com
  git config user.name Test
  git config commit.gpgsign false
  git checkout -q -b main
  echo "hello" > README.md
  git add -A
  git commit -q -m "chore: initial commit"
)

# --- clone into the working repo -------------------------------------------
git clone -q "$origin" "$dest"
(
  cd "$dest"
  git config user.email test@test.com
  git config user.name Test
  git config commit.gpgsign false
  git checkout -q -b work origin/main
)

case "$variant" in
  behind)
    # origin/main advances 3 commits after the clone; "work" stays put. The
    # commits touch a file the fixture's task record never cites (README.md
    # is left untouched upstream), so this variant isolates branch staleness
    # from the unrelated citation-resolution gate.
    for i in 1 2 3; do
      (
        cd "$origin"
        echo "change $i" >> UNRELATED.md
        git add -A
        git commit -q -m "chore: upstream change $i"
      )
    done
    (cd "$dest" && git fetch -q origin)
    behind_count="$(cd "$dest" && git rev-list --count HEAD..origin/main)"
    echo "behind fixture ready: behind=$behind_count" >&2
    ;;
  upstream-gone)
    # branch.work.merge points at a ref that was never pushed and does not
    # exist on origin, so the upstream track is reported "gone".
    (
      cd "$dest"
      git config branch.work.remote origin
      git config branch.work.merge refs/heads/deleted-branch
    )
    echo "upstream-gone fixture ready" >&2
    ;;
  current)
    # "work" is exactly at origin/main; nothing to do beyond the clone.
    echo "current fixture ready" >&2
    ;;
  fetch-failed)
    # origin/main is already cached locally from the clone (git clone always
    # writes refs/remotes/origin/main), then the remote URL is repointed at a
    # path that does not exist, so a later `git fetch origin` fails while the
    # cached ref is still readable.
    (
      cd "$dest"
      git remote set-url origin "${dest}-origin-does-not-exist"
    )
    echo "fetch-failed fixture ready" >&2
    ;;
esac
