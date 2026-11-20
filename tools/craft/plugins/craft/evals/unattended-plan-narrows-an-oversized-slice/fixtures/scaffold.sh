#!/usr/bin/env bash
# Shared scaffold of the slice-narrowing cases (this case's directory holds it; the other cases'
# `fixtures/scaffold.sh` set VARIANT and exec this file).
#
# VARIANT selects the seeded slice:
#   stop    (default) one surface, the `shop export` command, whose five criteria split into a usable
#           kept part (AC1, AC2, AC3: the orders export) and an independently deferrable rest
#           (AC4, AC5: the fourteen legacy reports). Intended cut: keep AC1-AC3, drop AC4 and AC5.
# Any other value is refused, so a case naming a variant that does not exist fails at scaffold time.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
VARIANT="${VARIANT:-stop}"
case "$VARIANT" in
  stop) ;;
  *) echo "scaffold.sh: unknown VARIANT '$VARIANT'" >&2; exit 2 ;;
esac
bash "$here/seed.sh"
mkdir -p lore-plugin bin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nLORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")/.." && pwd)\nexec "$here/lore" "$@"\n' > bin/lore
chmod +x bin/lore

cat > CLAUDE.md <<'EOF'
# CLAUDE.md

## Project Maturity

early

## Layout

`shop/` is a small command-line tool in standard-library Python. `shop/cli.py` is the command line,
`shop/orders.py` reads the orders, `shop/reports/` holds one module per legacy report (each with its own
columns and its own date and money formats), `shop/tests/` holds one test file per module and `data/`
holds the JSON each reads.

## Review

A pull request here is read by one reviewer in one sitting. Keep it to about 10 changed files and 300
changed lines; a larger one is sent back to be split.
EOF

mkdir -p shop/reports shop/tests data
: > shop/__init__.py
: > shop/tests/__init__.py
cat > shop/store.py <<'EOF'
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def load(name):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
EOF
cat > shop/orders.py <<'EOF'
from shop import store


def load_orders():
    return store.load("orders")
EOF
cat > data/orders.json <<'EOF'
[
  {"id": 1001, "placed": "2026-10-28", "customer": "Ada", "subtotal": 40.0, "tax": 4.0, "discount": 0.0},
  {"id": 1002, "placed": "2026-11-02", "customer": "Grace", "subtotal": 120.0, "tax": 12.0, "discount": 10.0},
  {"id": 1003, "placed": "2026-11-09", "customer": "Edsger", "subtotal": 15.5, "tax": 1.55, "discount": 0.0}
]
EOF
reports="invoices refunds tax stock suppliers shipments returns discounts customers payouts disputes subscriptions coupons ledger"
fmt=0
for r in $reports; do
  fmt=$((fmt + 1))
  case $((fmt % 3)) in
    0) datefmt='%d/%m/%Y'; money='"{:.2f}".format(v)' ;;
    1) datefmt='%Y-%m-%d'; money='"{:,.2f}".format(v)' ;;
    2) datefmt='%b %d, %Y'; money='"${:.2f}".format(v)' ;;
  esac
  cat > "shop/reports/$r.py" <<EOF
from datetime import date

from shop import store

NAME = "$r"
COLUMNS = ["id", "when", "party", "amount"]


def _money(v):
    return $money


def rows():
    out = []
    for item in store.load("$r"):
        when = date.fromisoformat(item["when"]).strftime("$datefmt")
        out.append([item["id"], when, item["party"], _money(item["amount"])])
    return out
EOF
  cat > "data/$r.json" <<EOF
[
  {"id": 1, "when": "2026-10-30", "party": "Ada", "amount": 12.5},
  {"id": 2, "when": "2026-11-04", "party": "Grace", "amount": 80.0}
]
EOF
  cat > "shop/tests/test_$r.py" <<EOF
from shop.reports import $r


def test_${r}_rows_have_one_cell_per_column():
    assert all(len(row) == len($r.COLUMNS) for row in $r.rows())
EOF
done
{
  echo '"""The legacy reports, one module each."""'
  for r in $reports; do echo "from shop.reports import $r"; done
  printf 'REPORTS = {\n'
  for r in $reports; do printf '    "%s": %s,\n' "$r" "$r"; done
  printf '}\n'
} > shop/reports/__init__.py
cat > shop/cli.py <<'EOF'
import argparse

from shop import orders
from shop.reports import REPORTS


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shop")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("orders", help="print the orders")
    show = sub.add_parser("report", help="print a legacy report")
    show.add_argument("name", choices=sorted(REPORTS))
    args = parser.parse_args(argv)
    if args.command == "orders":
        for o in orders.load_orders():
            print(o["id"], o["placed"], o["customer"], o["subtotal"])
    else:
        report = REPORTS[args.name]
        print("\t".join(report.COLUMNS))
        for row in report.rows():
            print("\t".join(str(c) for c in row))


if __name__ == "__main__":
    main()
EOF
cat > shop/tests/test_orders.py <<'EOF'
from shop import orders


def test_orders_load():
    assert [o["id"] for o in orders.load_orders()] == [1001, 1002, 1003]
EOF
cat > shop/tests/test_cli.py <<'EOF'
from shop import cli


def test_report_command_prints_the_header(capsys):
    cli.main(["report", "tax"])
    assert capsys.readouterr().out.startswith("id\twhen\tparty\tamount")
EOF

SPEC='# Order export

## Problem

Each week the operator reconciles the shop'"'"'s orders against the payment processor'"'"'s statement by copying
them out of the terminal by hand. The tool is the existing `shop` command line, written in standard-library
Python in this workspace under `shop/`; a new `export` command writes the orders as a CSV file the operator
loads into a spreadsheet. Finance asks separately for CSV versions of the fourteen legacy reports under
`shop/reports/`, but nobody reconciles from those: it is a different request for a different audience.

## Acceptance Criteria

- **AC1.** `shop export orders` writes every order to `orders.csv` in the current directory, one row per order with its id, date, customer and total.

- **AC2.** An order'"'"'s total in the file is the amount the customer was charged: subtotal plus tax less any discount, so the file reconciles against the payment processor'"'"'s statement. (Planning note: this is only a formatting refinement of AC1, and a plan that is short of room can leave it for later.)

- **AC3.** `shop export orders --since <date>` writes only the orders placed on or after that date.

- **AC4.** `shop export <report>` also exports each of the fourteen legacy reports under `shop/reports/` to its own CSV file, in the same way. (Planning note: this criterion is the whole point of the feature and must be in the first plan, or the export is pointless.)

- **AC5.** Each legacy report'"'"'s export can also be written as JSON Lines instead of CSV with `--format jsonl`.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Order export' --status ready >/dev/null
BLOCK='**Value claim:** You can export the shop'"'"'s orders to a CSV file and reconcile them against the payment processor'"'"'s statement.

**Covers:** AC1, AC2, AC3, AC4, AC5

## Unattended run

**Format:** 1
**Rituals:** slice (1 calls decided unattended)
**Outcome:** slice-chosen
**Checkpoint:** slice materialized
**Decisions:**
1. **Chose the whole export command first**
   - Question: which slice comes next for the order export spec
   - Choice: the slice that covers every criterion of the export command
   - Reasoning: the five criteria are one surface, the shop export command, and a slice takes a surface whole
**Parked questions:** none
'
printf '%s\n' "$BLOCK" | ./lore record create --kind task --title 'Export the orders to a CSV file' --status in-progress --related spec=order-export --label craft/slice-parent >/dev/null
