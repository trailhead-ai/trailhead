#!/usr/bin/env bash
# Scaffold for a monitor runner case: a scratch repo, a camp manifest and group TOML, a fresh
# 0700 outcome directory, and ./gh and ./portage stand-ins. Reads fixtures/params.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/params"
W="$PWD"

export GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@e.x GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@e.x
mkdir "$W/repo" "$W/outcome"
chmod 700 "$W/outcome"
git -C "$W/repo" -c init.defaultBranch=main init -q .
echo base > "$W/repo/notes.txt"
git -C "$W/repo" add -A
git -C "$W/repo" -c commit.gpgsign=false commit -q -m "chore: base"

cat > "$W/manifest.json" <<JSON
{"schema_version":1,"group":"g","slug":"s","branch":"topic/ledger-rounding","members":[{"name":"repo","repo_root":"$W/repo","worktree_path":"$W/repo"}]}
JSON

if [ "$AUTO_MERGE" = "true" ]; then
  printf '[release]\nauto_merge = true\n' > "$W/group.toml"
else
  printf '[release]\nreview_bot_login = ""\n' > "$W/group.toml"
fi

cat > "$W/gh" <<'GH'
#!/usr/bin/env bash
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "gh $*" >> "$W/gh.log"
echo "gh stand-in: unsupported $*" >&2
exit 1
GH

cat > "$W/portage" <<'PG'
#!/usr/bin/env bash
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "portage $*" >> "$W/portage.log"
case "${1:-}" in
  wait-for-actionable)
    printf '{"actionable":{"%s":{"action":"done","details":{}}},"waiting":{}}\n' "$W/repo:7"
    ;;
  merge)
    case " $* " in
      *" --operator-directed "*) ;;
      *) if ! grep -q 'auto_merge = true' "$W/group.toml"; then
           echo "portage merge: refusing to merge — [release].auto_merge is unset or false and the call lacks --operator-directed" >&2
           exit 2
         fi;;
    esac
    printf '{"merged":["%s:7"],"failed":{},"skipped":{}}\n' "$W/repo"
    ;;
  *)
    echo "portage stand-in: unsupported subcommand: ${1:-}" >&2
    exit 2
    ;;
esac
PG
chmod +x "$W/gh" "$W/portage"
