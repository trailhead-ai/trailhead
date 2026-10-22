#!/usr/bin/env bash
# Scaffold for a portage runner case: a scratch repo with a bare origin, a local-only commit
# on a feature branch, ./gh and ./portage stand-ins, and a fake eval-runner binary.
# Reads fixtures/params (KEY=value lines). The repo root is seven directory levels above
# this file's directory (fixtures -> <case> -> evals -> plugins/portage -> plugins ->
# tools/portage -> tools -> repo).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../../../../../.." && pwd)"
plugin_root="$(cd "$here/../../.." && pwd -P)"
# shellcheck disable=SC1091
. "$here/params"
W="$PWD"

export GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@e.x GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@e.x
export GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z"
G="git -c commit.gpgsign=false -c init.defaultBranch=main"

git init -q --bare "$W/origin.git"
mkdir "$W/repo" "$W/tmp" "$W/claude-dir"
cd "$W/repo"
$G init -q .
git remote add origin "$W/origin.git"

# Every commit carries a gpgsig header: presence is all a push preflight checks.
signed() { # message parent-or-empty
  local tree parent_line=""
  tree=$(git write-tree)
  [ -n "$2" ] && parent_line="parent $2"$'\n'
  printf 'tree %s\n%sauthor T <t@e.x> 1767225600 +0000\ncommitter T <t@e.x> 1767225600 +0000\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n fakefakefake\n -----END PGP SIGNATURE-----\n\n%s\n' "$tree" "$parent_line" "$1" | git hash-object -t commit -w --stdin
}

P="tools/$PLUGIN/plugins/$PLUGIN"
mkdir -p "$P/evals/$CASE" "$P/skills/$SKILL"
echo base > notes.txt
printf 'Guidance for %s.\n' "$SKILL" > "$P/skills/$SKILL/SKILL.md"
printf 'schema_version: "1.1"\nname: %s\nruns: 3\n' "$CASE" > "$P/evals/$CASE/case.yaml"
cat > "$P/evals/eval-gate.json" <<JSON
{"schema_version":1,
 "runner":{"model":"claude-opus-5-5","allow_tools":["Bash(./$PLUGIN *)"],"max_cost_usd":1},
 "cases":{"$CASE":{"measures":["skills/$SKILL/SKILL.md"]}}}
JSON
git add -A
base=$(signed "chore: base" ""); git update-ref refs/heads/main "$base"; git symbolic-ref HEAD refs/heads/main
git push -q origin main
git remote set-head origin main >/dev/null
git checkout -q -b "$BRANCH"
echo one > one.txt; git add one.txt
one=$(signed "feat: first change" "$base"); git update-ref HEAD "$one"; git reset -q --hard "$one"
git push -q origin "$BRANCH"
printf 'Guidance for %s, revised.\n' "$SKILL" > "$P/skills/$SKILL/SKILL.md"; git add -A
two=$(signed "feat: revise the guidance" "$one"); git update-ref HEAD "$two"; git reset -q --hard "$two"
cd "$W"

mkdir -p "$W/trailhead-src"; cp -r "$repo_root/trailhead" "$W/trailhead-src/trailhead"
cat > "$W/manifest.json" <<JSON
{"schema_version":1,"group":"g","slug":"s","branch":"$BRANCH","members":[{"name":"repo","repo_root":"$W/repo","worktree_path":"$W/repo"}]}
JSON
echo "$MODE" > "$W/.gate-mode"

cat > "$W/gh" <<'GH'
#!/usr/bin/env bash
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "gh $*" >> "$W/gh.log"
branch=$(cat "$W/.branch")
case "$1 $2" in
  "pr view") printf '{"number":7,"url":"https://example.test/o/r/pull/7","state":"OPEN","headRefName":"%s","body":"Original body"}\n' "$branch";;
  "pr list") printf '[{"number":7,"url":"https://example.test/o/r/pull/7","headRefName":"%s"}]\n' "$branch";;
  "pr edit")
    while [ $# -gt 0 ]; do case "$1" in
      --body-file) [ -r "$2" ] || { echo "gh: no such file $2" >&2; exit 1; }; cat "$2" > "$W/pr-body.md"; shift;;
      --body) printf '%s' "$2" > "$W/pr-body.md"; shift;;
    esac; shift; done;;
  *) echo "gh stand-in: unsupported $*" >&2; exit 1;;
esac
GH
echo "$BRANCH" > "$W/.branch"
printf "Original body\n" > "$W/pr-description.md"

cat > "$W/portage" <<PG
#!/usr/bin/env bash
W="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
echo "portage \$*" >> "\$W/portage.log"
export TRAILHEAD_ROOT="\$W/trailhead-src" TRAILHEAD_CLAUDE_DIR="\$W/claude-dir" TMPDIR="\$W/tmp"
export PORTAGE_EVAL_RUNNER="\$W/fake-runner"
if [ "\${1:-}" = "eval-gate" ]; then
  exec python3 "$plugin_root/cli/portage" "\$@"
fi
# The gate is a feature of the prose under test, not something the stand-in advertises: every
# other invocation (help, usage errors, ordinary commands) has the gate's name scrubbed.
set -o pipefail
python3 "$plugin_root/cli/portage" "\$@" 2>&1 | sed -e "s/, 'eval-gate'//g" -e 's/,eval-gate//g' -e '/eval-gate/d'
exit "\${PIPESTATUS[0]}"
PG

cat > "$W/fake-runner" <<'RUNNER'
#!/usr/bin/env bash
# A stand-in for the eval runner binary: the canary passes; the case follows .gate-mode.
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
json=""; case_name=""
while [ $# -gt 0 ]; do
  case "$1" in --json) json="$2"; shift;; --case) case_name="$2"; shift;; esac
  shift
done
echo "runner case=$case_name" >> "$W/runner.log"
runs=3; score=0.5; ok=false
if [ "$case_name" = "sandbox-write" ]; then runs=1; score=1; ok=true
elif [ "$(cat "$W/.gate-mode")" != "fail" ]; then exit 1
fi
out='{"schemaVersion":1,"partial":false,"cases":[{"name":"'"$case_name"'","arms":{"with":['
for i in $(seq "$runs"); do
  [ "$i" -gt 1 ] && out="$out,"
  out="$out"'{"score":'"$score"',"passed":'"$ok"',"error":null,"graders":[{"name":"outcome","passed":'"$ok"'}]}'
done
printf '%s]}}]}\n' "$out" > "$json"
RUNNER
chmod +x "$W/gh" "$W/portage" "$W/fake-runner"
