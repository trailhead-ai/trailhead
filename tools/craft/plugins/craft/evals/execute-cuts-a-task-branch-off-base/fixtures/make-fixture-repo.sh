#!/usr/bin/env bash
# Materialise a vanilla-path fixture repo for the task-branch-cut eval case.
#
#   ./make-fixture-repo.sh <fresh|dirty|collision|design-doc> <dest-dir>
#
# Every variant produces a local "origin" at "<dest>-origin" and a working repo
# at "<dest>" checked out on branch "work". "work" carries one commit from an
# earlier, unrelated task (PRIOR.md) that is not on origin/main, and origin/main
# advances one commit (UPSTREAM.md) after the clone that the working repo has
# NOT fetched. A branch cut from the latest base therefore contains UPSTREAM.md
# and not PRIOR.md; a run that builds on "work" has the opposite shape.
# Refuses to run when either path already exists.
set -euo pipefail

variant="${1:?variant required: fresh | dirty | collision | design-doc}"
dest="${2:?destination directory required}"
origin="${dest}-origin"

case "$variant" in
  fresh|dirty|collision|design-doc) ;;
  *) echo "unknown variant: $variant" >&2; exit 2 ;;
esac

if [ -e "$dest" ] || [ -e "$origin" ] || [ -e "${dest}-signer" ]; then
  echo "refusing to overwrite existing path: $dest, $origin or ${dest}-signer" >&2
  exit 2
fi
mkdir -p "$origin"

# A stand-in signer, so a `-S` commit succeeds without a real key: it prints a
# placeholder signature and the status line git reads to confirm signing.
signer="${dest}-signer"
cat > "$signer" <<'SIGNER'
#!/bin/sh
cat > /dev/null
echo "[GNUPG:] SIG_CREATED D 1 8 00 0 FIXTURE" >&2
printf -- '-----BEGIN PGP SIGNATURE-----\nfixture\n-----END PGP SIGNATURE-----\n'
SIGNER
chmod +x "$signer"

gitid() {
  git config user.email test@test.com
  git config user.name Test
  git config commit.gpgsign false
  git config gpg.program "$signer"
}

(
  cd "$origin"
  git init -q .
  gitid
  git checkout -q -b main
  echo "hello" > README.md
  git add -A
  git commit -q -m "chore: initial commit"
)

git clone -q "$origin" "$dest"
(
  cd "$dest"
  gitid
  git checkout -q -b work origin/main
  echo "earlier task" > PRIOR.md
  git add PRIOR.md
  git commit -q -m "feat: earlier unrelated task"
)

(
  cd "$origin"
  echo "upstream" > UPSTREAM.md
  git add UPSTREAM.md
  git commit -q -m "chore: upstream change after the clone"
)

case "$variant" in
  fresh)
    ;;
  dirty)
    # A tracked file carries an uncommitted edit.
    (cd "$dest" && echo "uncommitted edit" >> README.md)
    ;;
  collision)
    # The branch name the standalone task derives already exists on origin.
    (
      cd "$origin"
      git branch fixture-add-a-readme-line main
    )
    ;;
  design-doc)
    # Plan's design-doc commit sits on "work" on top of the earlier task,
    # committed with --only so it touches that one path.
    (
      cd "$dest"
      mkdir -p docs/design
      echo "# Fixture slice design doc" > docs/design/fixture-slice.md
      git add docs/design/fixture-slice.md
      git commit -q --only -m "docs(fixture): design doc for fixture-parent-with-design-doc" -- docs/design/fixture-slice.md
    )
    ;;
esac

echo "$variant fixture ready at $dest" >&2
