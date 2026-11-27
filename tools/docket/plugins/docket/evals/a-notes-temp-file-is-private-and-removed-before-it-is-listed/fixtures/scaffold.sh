#!/usr/bin/env bash
# runs the shared docket scaffold, then wraps ./lore and ./docket so the run leaves a trace of the note's temp file
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$here/../../docket-scaffold.sh"
py="$(head -1 docket | sed 's/^#!//')"
mv lore lore.real
mv docket docket.real
: > tempfile-trace.log
cat > trace.py <<'PY'
import os, stat, sys
here = os.path.dirname(os.path.abspath(__file__))
trace = os.path.join(here, "tempfile-trace.log")
if sys.argv[1] == "lore":
    st = os.fstat(0)
    if stat.S_ISREG(st.st_mode):
        line = "created mode=%o ino=%d\n" % (stat.S_IMODE(st.st_mode), st.st_ino)
    else:
        line = "created not-a-regular-file ino=0\n"
    open(trace, "a").write(line)
else:
    tmp = os.environ.get("TMPDIR") or "/tmp"
    present = {e.inode() for e in os.scandir(tmp)}
    for line in open(trace).read().splitlines():
        if line.startswith("created ") and " ino=" in line:
            ino = int(line.rsplit("ino=", 1)[1])
            open(trace, "a").write("add-time ino=%d %s\n" % (ino, "present" if ino in present else "gone"))
PY
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nif [ "$1 $2" = "record create" ]; then %s "$here/trace.py" lore; fi\nexec "$here/lore.real" "$@"\n' "$py" > lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nif [ "$1" = add ]; then %s "$here/trace.py" docket; fi\nexec "$here/docket.real" "$@"\n' "$py" > docket
chmod +x lore docket
