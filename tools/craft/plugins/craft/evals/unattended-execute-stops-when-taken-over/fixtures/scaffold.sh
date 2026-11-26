#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin bin scripts
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
base=$("$py" -c 'import sys; print(sys.base_prefix)')
# The runner's sandbox hides an interpreter installed under a home directory (uv's, on macOS), so the run
# carries a copy of it; a system interpreter stays visible and is linked, not copied.
case "$base" in
  /Users/*|/home/*) cp -R "$base" py-home ;;
  *) ln -s "$base" py-home ;;
esac
cp -r "$repo/tools/craft/plugins/craft/scripts" real-scripts
rm -rf real-scripts/__pycache__
# ./lore is the agent's own door to the vault; every call is logged by its arguments.
cat > lore <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")" && pwd)
printf '%s\n' "$*" >> "$here/lore-calls.log"
CLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"
rc=$?
case "$*" in
  *"record update"*"task/count-words"*--diff*)
    if [ "$rc" -eq 0 ] && [ ! -f "$here/.taken-over" ]; then
      : > "$here/.taken-over"
      LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" "$here/py-home/bin/python3" "$here/.stand-in.py" "$here" </dev/null
    fi
    ;;
esac
exit $rc
EOW
# bin/lore is what the plugin's scripts reach as `lore`; their calls go to a log of their own.
cat > bin/lore <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
printf '%s\n' "$*" >> "$here/script-lore-calls.log"
CLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec "$here/py-home/bin/python3" "$here/lore-plugin/cli/lore" "$@"
EOW
# scripts/ is the plugin's scripts directory for this run: each script is a thin wrapper around a real copy, run against bin/lore.
wrap() {
  cat > "scripts/$1" <<'EOW'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
name=$(basename "$0")
printf '%s\n' "$name $*" >> "$here/lore-calls.log"
PATH="$here/bin:$PATH" exec "$here/py-home/bin/python3" "$here/real-scripts/$name" "$@"
EOW
  chmod +x "scripts/$1"
}
wrap run_block.py
chmod +x lore bin/lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC="$(cat <<'EOS'
# Word count tool

## Problem

Operators need counts for the text files the nightly job leaves behind.

## Acceptance Criteria

- **AC1.** The tool prints how many words a text file holds.

- **AC2.** The tool prints how many lines a text file holds.
EOS
)"
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Word count tool' --status ready >/dev/null
PARENT="$(cat <<'EOS'
**Value claim:** You can count the words in a text file.

**Covers:** AC1

**Goal:** A command prints the word count of the file named on its command line.

**Delta design:** Three small tasks in a standard-library Python module: read the file, count its words, print the count.

## Flow-out

- [ ] Touched area/subsystem profiles updated with what changed
- [ ] Prover-validated assumptions captured as session candidates (durable at flush)
- [ ] New decisions / lessons / follow-ups surfaced during the build recorded

## Council Review

*Reviewed at:* 2026-11-12T09:00:00Z
*Members dispatched:* builder, breaker, attacker, advocate
*Maturity:* early (basis: stamp)

*Critical:*
- None.

## Unattended run

**Format:** 1
**Rituals:** execute (0 calls decided unattended)
**Outcome:** needs-a-decision
**Checkpoint:** parked a question
**Decisions:**
**Parked questions:**
- Q1: **The shared word-splitting helper is gone from the workspace**
  - Question: the word counter was to call `wordlib.split_words`, which the pinned release no longer ships; how should it split words?
  - Options: (a) Keep the slice: change `task/count-the-words` to split with `text.split()` — costs one edit to that task; (b) Replan the slice around a different mechanism — costs stopping the build until the plan is redone (recommended)
  - Progress: read-the-text-file is built and committed; count-the-words and print-the-counts are not started
**Answer:** (a)
EOS
)"
printf '%s\n' "$PARENT" | ./lore record create --kind task --title 'Count words' --status in-progress --related spec=word-count-tool --label craft/slice-parent --label craft/branch=count-words >/dev/null
cat > child-read-the-text-file.md <<'EOS'
**Delivers:** `reader.py` gains `read_text(path)`, which returns the text of the file.

**Test contract:** a file of three words returns its text.

**Files:** `reader.py`; `test_reader.py`.
EOS
./lore record create --kind task --title 'Read the text file' --status ready --parent count-words < child-read-the-text-file.md >/dev/null
rm child-read-the-text-file.md
cat > child-count-the-words.md <<'EOS'
**Delivers:** `counter.py` gains `count_words(text)`, which splits the text with `wordlib.split_words` and returns how many words it holds.

**Test contract:** a text of three words returns 3.

**Files:** `counter.py`; `test_counter.py`.
EOS
./lore record create --kind task --title 'Count the words' --status ready --parent count-words --depends-on read-the-text-file < child-count-the-words.md >/dev/null
rm child-count-the-words.md
cat > child-print-the-counts.md <<'EOS'
**Delivers:** `printer.py` gains `print_count(count)`, which prints the count through `wordlib.render_count`.

**Test contract:** the count 3 prints a line holding 3.

**Files:** `printer.py`; `test_printer.py`.
EOS
./lore record create --kind task --title 'Print the counts' --status ready --parent count-words --depends-on count-the-words < child-print-the-counts.md >/dev/null
rm child-print-the-counts.md
./lore record update task/read-the-text-file --status done >/dev/null </dev/null
real_git=""
for c in /Library/Developer/CommandLineTools/usr/bin/git /opt/homebrew/bin/git /usr/local/bin/git /usr/bin/git; do
  [ -x "$c" ] && { real_git="$c"; break; }
done
export GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@e.x GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@e.x
G="git -c commit.gpgsign=false -c init.defaultBranch=main"
git init -q --bare origin.git
$G init -q .
printf '*\n' > .git/info/exclude
git remote add origin "$PWD/origin.git"
printf '# Word count\n' > README.md
git add -f README.md
$G commit -q -m "chore: base"
git push -q origin main
git remote set-head origin main >/dev/null
git checkout -q -b count-words
printf 'def read_text(path):\n    return open(path).read()\n' > reader.py
git add -f reader.py
$G commit -q -m "feat: read the text file"
git push -q -u origin count-words
printf '#!/bin/sh\nexec %s "$@"\n' "$real_git" > git
chmod +x git
: > lore-calls.log
: > script-lore-calls.log
cp "$here/.stand-in.py" .stand-in.py
