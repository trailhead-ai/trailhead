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
base=$("$py" -c 'import sys; print(sys.base_prefix)')
# The runner's sandbox hides an interpreter installed under a home directory, so the run carries a copy of it;
# a system interpreter stays visible and is linked, not copied.
case "$base" in
  /Users/*|/home/*) cp -R "$base" py-home ;;
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
