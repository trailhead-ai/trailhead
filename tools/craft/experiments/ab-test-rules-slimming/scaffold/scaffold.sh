#!/usr/bin/env bash
# Writes one fixture package into the current directory, and a ./run-tests wrapper for it.
# usage: scaffold.sh TREE   (exporter | retry | cli | parser)
#
# ./run-tests takes no arguments, runs the fixture's tests, and appends one line per run to
# run-tests.log (created by its first run): its exit code, and whether the implementation file
# still hashes to what the scaffold seeded.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tree="${1:?usage: scaffold.sh TREE}"
[ -d "$here/trees/$tree" ] || { echo "no fixture tree: $tree" >&2; exit 2; }
case "$tree" in
  exporter) impl=reportbay/exporters.py ;;
  retry)    impl=courier/retry.py ;;
  cli)      impl=addup/cli.py ;;
  parser)   impl=kvparse/parser.py ;;
  *)        echo "no fixture tree: $tree" >&2; exit 2 ;;
esac
cp -R "$here/trees/$tree/." .

py=$(command -v python3)
# The operator's real home comes from the account database, never from $HOME: a run gives the scaffold a temporary HOME.
probe=$("$py" -c 'import os, pwd, sys; print(sys.base_prefix); print(pwd.getpwuid(os.getuid()).pw_dir)') || probe=""
base=$(printf '%s\n' "$probe" | sed -n 1p)
real_home=$(printf '%s\n' "$probe" | sed -n 2p)
home_real=""
[ -n "$real_home" ] && home_real=$(cd -P "$real_home" 2>/dev/null && pwd) || home_real=""
[ -n "$home_real" ] || { echo "scaffold.sh: refusing to continue: cannot resolve the operator's real home directory" >&2; exit 3; }
base_real=$(cd -P "$base" 2>/dev/null && pwd) || { echo "scaffold.sh: cannot resolve python's prefix: $base" >&2; exit 3; }
# A prefix that is the operator's home directory, or holds it, would put the whole home in the run directory.
case "$home_real/" in
  "${base_real%/}/"*) echo "scaffold.sh: refusing to expose the home directory: python's prefix is the home directory or one of its ancestors" >&2; exit 3 ;;
esac
# The runner's sandbox hides an interpreter installed under a home directory, so the run carries a copy of the
# interpreter alone: bin/python3*, lib/libpython*, and the standard library under lib/python3*/ without its
# site-packages or dist-packages. A prefix such as ~/.local also holds personal scripts in bin/, other tools' data
# in lib/ and the pip --user set, none of which belongs in a run. A system interpreter stays visible and is
# linked, not copied.
case "$base_real/" in
  /Users/*|/home/*|"$home_real"/*)
    mkdir py-home
    "$py" - "$base" py-home <<'COPY'
import fnmatch, shutil, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
skip = shutil.ignore_patterns("site-packages", "dist-packages", "__pycache__")
for sub, pattern in (("bin", "python3*"), ("lib", "libpython*"), ("lib", "python3*")):
    if not (src / sub).is_dir():
        continue
    for entry in sorted((src / sub).iterdir()):
        if not fnmatch.fnmatch(entry.name, pattern):
            continue
        target = dst / sub / entry.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True, ignore=skip)
        else:
            shutil.copy2(entry, target, follow_symlinks=False)
COPY
    ;;
  *) ln -s "$base" py-home ;;
esac
seed=$(py-home/bin/python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$impl")

cat > run-tests <<EOW
#!/bin/sh
here=\$(cd "\$(dirname "\$0")" && pwd)
cd "\$here"
py="\$here/py-home/bin/python3"
export PYTHONDONTWRITEBYTECODE=1
"\$py" -m unittest discover -s tests -t . 2>&1
code=\$?
now=\$("\$py" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$impl" 2>/dev/null)
if [ "\$now" = "$seed" ]; then state=untouched; else state=changed; fi
printf 'exit=%s impl=%s\n' "\$code" "\$state" >> run-tests.log
exit \$code
EOW
chmod +x run-tests
