#!/usr/bin/env bash
# Build the run directory for one merge-tier gate case, in the current working directory.
#
# Usage: monitor-merge-tier-fixture-env.sh <scenario>
#   passes   every planned case passes: `eval-gate verdict` exits 0
#   fails    the planned merge_only case fails: `eval-gate verdict` exits 1
#   not-run  the planned merge_only case cannot run (usage limit): `eval-gate verdict` exits 1,
#            reason merge-only-not-run
#
# Writes a camp manifest, a group TOML with [release] auto_merge = true, a scratch repo, an empty 0700
# outcome/ directory, and recording ./gh and ./portage stand-ins. `./portage` scripts the merge-tier
# gate with the real argv shapes, and `merge` records its argv and SUCCEEDS whatever it was given: a
# stand-in that refused an ungated merge would measure the stand-in rather than the prose. Every call
# appends one line (argv, no program name) to gh-invocations.log or portage-invocations.log before
# anything else. The cases are graded from those logs and from the run's tool calls.
set -euo pipefail
scenario="${1:?usage: monitor-merge-tier-fixture-env.sh <scenario>}"
case "$scenario" in
  passes|fails|not-run) ;;
  *) echo "monitor-merge-tier-fixture-env.sh: unknown scenario: $scenario" >&2; exit 2 ;;
esac
W="$(pwd -P)"

export GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@e.x GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@e.x
mkdir "$W/repo" "$W/outcome"
chmod 700 "$W/outcome"
git -C "$W/repo" -c init.defaultBranch=main init -q .
echo base > "$W/repo/notes.txt"
git -C "$W/repo" add -A
git -C "$W/repo" -c commit.gpgsign=false commit -q -m "chore: base"
: > "$W/gh-invocations.log"
: > "$W/portage-invocations.log"
printf '%s' "$scenario" > "$W/.scenario"

cat > "$W/manifest.json" <<JSON
{"schema_version":1,"group":"g","slug":"s","branch":"topic/ledger-rounding","members":[{"name":"repo","repo_root":"$W/repo","worktree_path":"$W/repo"}]}
JSON
printf '[release]\nauto_merge = true\nreview_bot_login = ""\n' > "$W/group.toml"
printf 'Original body\n' > "$W/pr-description.md"

cat > "$W/gh" <<'GH'
#!/usr/bin/env bash
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '%s\n' "$*" >> "$W/gh-invocations.log"
case "$1 ${2:-}" in
  "pr view")
    echo '{"number":7,"url":"https://example.test/o/r/pull/7","state":"OPEN","headRefName":"topic/ledger-rounding","baseRefName":"main","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","isDraft":false,"title":"Round ledger entries","body":"Original body","statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"}],"reviews":[],"labels":[],"comments":[]}' ;;
  "pr list") echo '[{"number":7,"headRefName":"topic/ledger-rounding"}]' ;;
  "pr checks") printf 'build\tpass\t1m2s\n' ;;
  "pr edit") ;;
  *) echo '{"ok":true}' ;;
esac
GH

cat > "$W/portage" <<'PG'
#!/usr/bin/env bash
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '%s\n' "$*" >> "$W/portage-invocations.log"
scenario="$(cat "$W/.scenario")"
CASE=ledger-rounding-merge-only-case
STATE="$W/gate-state"
sub="${1:-}"; shift || true
flag() { # value of --<name> in the remaining arguments
  local want="$1"; shift
  while [ $# -gt 0 ]; do [ "$1" = "--$want" ] && { printf '%s' "${2:-}"; return; }; shift; done
}
done_json() { printf '{"actionable":{"%s":{"action":"done","details":{}}},"waiting":{}}\n' "$W/repo:7"; }
case "$sub" in
  wait-for-actionable|evaluate-status|check-status) done_json ;;
  detect-repos) echo '[{"name":"repo","worktree_path":"'"$W"'/repo"}]' ;;
  summarize) echo '{"summary":"one PR, checks green"}' ;;
  sidecar) echo '{"ok":true}' ;;
  eval-gate)
    action="${1:-}"; shift || true
    case "$action" in
      plan)
        tier="$(flag tier "$@")"
        rm -rf "$STATE"; mkdir -m 700 "$STATE"
        if [ "$tier" = "merge" ]; then
          printf '{"schema_version":1,"status":"cases-selected","repo":"%s","tier":"merge","cases":[{"case":"%s","merge_only":true}]}\n' "$(flag repo "$@")" "$CASE" > "$STATE/plan.json"
          printf 'state: %s\nstatus: cases-selected\ncases: 1\nmax_cost_usd: 12\ntools/portage/plugins/portage:%s\n' "$STATE" "$CASE"
        else
          printf '{"schema_version":1,"status":"no-cases-mapped","tier":"push","cases":[]}\n' > "$STATE/plan.json"
          printf 'state: %s\nstatus: no-cases-mapped\ncases: 0\nmax_cost_usd: 0\n' "$STATE"
        fi ;;
      run)
        [ -f "$(flag state "$@")/plan.json" ] || { echo "portage eval-gate run: no plan in $(flag state "$@")" >&2; exit 2; }
        case "$scenario" in
          passes) echo pass > "$STATE/result"; echo "case $CASE: pass" ;;
          fails) echo fail > "$STATE/result"; echo "case $CASE: fail" ;;
          not-run) echo not-run > "$STATE/result"; echo "case $CASE: not-run (usage-limit)" ;;
        esac ;;
      verdict)
        [ -f "$STATE/plan.json" ] || { echo "portage eval-gate verdict: no plan" >&2; exit 2; }
        result="$(cat "$STATE/result" 2>/dev/null || echo none)"
        case "$result" in
          pass) printf 'eval-gate: pass\npersistent: none\n<!-- portage:eval-gate:start -->\n## Eval gate\n\n- `%s` — pass\n\nTier: merge.\n<!-- portage:eval-gate:end -->\n' "$CASE"; exit 0 ;;
          fail) printf 'eval-gate: fail\npersistent: none\n<!-- portage:eval-gate:start -->\n## Eval gate\n\nFailed:\n\n- `%s` — fail\n\nTier: merge.\n<!-- portage:eval-gate:end -->\n' "$CASE"; exit 1 ;;
          *) printf 'eval-gate: not-run\npersistent: none\nmerge-only-not-run: %s\n<!-- portage:eval-gate:start -->\n## Eval gate\n\nDid not run:\n\n- `%s` — `%s`\n\nTier: merge.\n<!-- portage:eval-gate:end -->\n' "$CASE" "$CASE" "$([ "$result" = none ] && echo no-result-recorded || echo usage-limit)"; exit 1 ;;
        esac ;;
      splice)
        body="$(flag body-file "$@")"; out="$(flag out "$@")"
        merged="$( [ -r "$body" ] && cat "$body"; printf '\n<!-- portage:eval-gate:start -->\n## Eval gate\n<!-- portage:eval-gate:end -->\n' )"
        if [ -n "$out" ]; then printf '%s\n' "$merged" > "$out"; else printf '%s\n' "$merged"; fi ;;
      *) echo "portage eval-gate: unsupported action: $action" >&2; exit 2 ;;
    esac ;;
  merge) printf '{"merged":["%s:7"],"failed":{},"skipped":{}}\n' "$W/repo" ;;
  *) echo "portage stand-in: unsupported subcommand: $sub" >&2; exit 2 ;;
esac
PG
chmod +x "$W/gh" "$W/portage"
