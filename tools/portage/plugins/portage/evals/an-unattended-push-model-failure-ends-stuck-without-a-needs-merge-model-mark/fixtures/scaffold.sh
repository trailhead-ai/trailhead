#!/usr/bin/env bash
# The corpus's unattended PR-step scaffold, then a gate overlay: the slice branch's base carries an
# eval-gate.json mapping (push_model set) of a case to a prose file, the slice's local-only commit edits
# that prose, and ./portage runs the eval runner through a fake binary that fails the case.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
# shellcheck disable=SC1091
. "$here/params"
set +a
bash "$here/../../pr-step-scaffold.sh"
W="$PWD"
BRANCH=count-words
PLUGIN=ledgerkit SKILL=rounding CASE=ledger-roundtrip
P="tools/$PLUGIN/plugins/$PLUGIN"

export GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@e.x GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@e.x
export GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z"
cd "$W/repo"
signed() { # message parent-or-empty
  local tree parent_line=""
  tree=$(git write-tree)
  [ -n "$2" ] && parent_line="parent $2"$'\n'
  printf 'tree %s\n%sauthor T <t@e.x> 1767225600 +0000\ncommitter T <t@e.x> 1767225600 +0000\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n fakefakefake\n -----END PGP SIGNATURE-----\n\n%s\n' "$tree" "$parent_line" "$1" | git hash-object -t commit -w --stdin
}
old_one=$(git rev-parse HEAD~1)
old_two=$(git rev-parse HEAD)
old_base=$(git rev-parse HEAD~2)
gate_files() {
  mkdir -p "$P/evals/$CASE" "$P/skills/$SKILL"
  printf 'schema_version: "1.1"\nname: %s\nruns: 3\n' "$CASE" > "$P/evals/$CASE/case.yaml"
  cat > "$P/evals/eval-gate.json" <<JSON
{"schema_version":1,
 "runner":{"model":"claude-opus-5-5","push_model":"claude-sonnet-5-5","allow_tools":["Bash(./$PLUGIN *)"],"max_cost_usd":1},
 "cases":{"$CASE":{"measures":["skills/$SKILL/SKILL.md"]}}}
JSON
}
# Re-create base, first and second commits on a tree that holds the mapping and the prose file.
git checkout -q --detach "$old_base"
gate_files; printf 'Guidance for %s.\n' "$SKILL" > "$P/skills/$SKILL/SKILL.md"; git add -A
base=$(signed "chore: base" "")
git checkout -q --detach "$old_one"
gate_files; printf 'Guidance for %s.\n' "$SKILL" > "$P/skills/$SKILL/SKILL.md"; git add -A
one=$(signed "feat: count the words in a file" "$base")
git checkout -q --detach "$old_two"
gate_files; printf 'Guidance for %s, revised.\n' "$SKILL" > "$P/skills/$SKILL/SKILL.md"; git add -A
two=$(signed "feat: count the lines in a file" "$one")
git update-ref refs/heads/main "$base"
git update-ref "refs/heads/$BRANCH" "$two"
git checkout -q "$BRANCH"; git reset -q --hard "$two"
git push -q -f origin main
git push -q -f origin "$one:refs/heads/$BRANCH"
git fetch -q origin
git remote set-head origin main >/dev/null
cd "$W"

# The gate's runner is a fake binary that fails the case on every run.
sed -i '/^export PATH=/a export PORTAGE_EVAL_RUNNER="$W/fake-runner"' "$W/portage"
cat > "$W/fake-runner" <<'RUNNER'
#!/usr/bin/env bash
# A stand-in for the eval runner binary: the canary passes; every other case scores 0.5 on every run.
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
json=""; case_name=""
while [ $# -gt 0 ]; do
  case "$1" in --json) json="$2"; shift;; --case) case_name="$2"; shift;; esac
  shift
done
echo "runner case=$case_name" >> "$W/runner.log"
runs=3; score=0.5; ok=false
if [ "$case_name" = "sandbox-write" ]; then runs=1; score=1; ok=true; fi
out='{"schemaVersion":1,"partial":false,"cases":[{"name":"'"$case_name"'","arms":{"with":['
for i in $(seq "$runs"); do
  [ "$i" -gt 1 ] && out="$out,"
  out="$out"'{"score":'"$score"',"passed":'"$ok"',"error":null,"graders":[{"name":"outcome","passed":'"$ok"'}]}'
done
printf '%s]}}]}\n' "$out" > "$json"
RUNNER
chmod +x "$W/fake-runner"
