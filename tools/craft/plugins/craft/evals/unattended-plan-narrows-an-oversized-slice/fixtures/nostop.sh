#!/usr/bin/env bash
# The `nostop` variant of scaffold.sh: run from the scaffold's directory after the lore CLI is installed.
set -euo pipefail

cat > CLAUDE.md <<'EOF'
# CLAUDE.md

## Project Maturity

early

## Layout

`shop/` is a small command-line tool in standard-library Python. `shop/cli.py` is the command line,
`shop/orders.py` reads the orders, `shop/reports/` holds one module per legacy report, `shop/tests/`
holds one test file per module and `data/` holds the JSON file each one reads.

Each dataset (the orders and each of the fourteen legacy reports) has its own fields, its own row
shape and its own date and money formats, and each module opens its own JSON file. Anything that
handles a dataset (its table, its copy into a database, its reader) is written in that dataset's own
module, with its own test file; there is no generic table mapper and no shared loader.

The operator refreshes the JSON files by hand and runs the commands against whatever `data/` holds, so
a command must never read some of its figures from one source and some from another.

## Review

A pull request here is read by one reviewer in one sitting. Keep it to at most 10 changed files and
300 changed lines; a larger one is sent back to be split.
EOF
cat > CONTRIBUTING.md <<'EOF'
# Contributing

- One module per concern, one test file per module (`shop/tests/test_<module>.py`).
- Pull requests change at most 10 files and 300 lines in total, tests included. A pull request over
  either limit is sent back to be split before it is read.
EOF

mkdir -p shop/reports shop/tests data
: > shop/__init__.py
: > shop/tests/__init__.py
cat > shop/orders.py <<'EOF'
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def load_orders():
    return json.loads((DATA / "orders.json").read_text(encoding="utf-8"))
EOF
cat > data/orders.json <<'EOF'
[
  {"id": 1001, "placed": "2026-10-28", "customer": "Ada", "subtotal": 40.0, "tax": 4.0, "discount": 0.0},
  {"id": 1002, "placed": "2026-11-02", "customer": "Grace", "subtotal": 120.0, "tax": 12.0, "discount": 10.0},
  {"id": 1003, "placed": "2026-11-09", "customer": "Edsger", "subtotal": 15.5, "tax": 1.55, "discount": 0.0}
]
EOF
reports="invoices:build_invoice_lines:number,issued,billed_to,gross
refunds:refund_rows:ref,refunded_on,customer,returned
tax:tax_by_party:code,period,authority,levy
stock:stock_levels:sku,counted,bin,units
suppliers:supplier_table:vendor,since,contact,spend
shipments:shipment_legs:waybill,left,carrier,freight
returns:returned_items:rma,received,reason,credit
discounts:applied_discounts:promo,used,channel,saved
customers:customer_summary:acct,joined,name,lifetime
payouts:payout_batches:batch,paid,bank,net
disputes:open_disputes:case,raised,against,held
subscriptions:subscription_ledger:plan,renewed,holder,fee
coupons:coupon_redemptions:code,redeemed,source,value
ledger:ledger_entries:entry,posted,account,balance"
names=""
n=0
while IFS=: read -r r fn keys; do
  n=$((n + 1))
  names="$names $r"
  IFS=, read -r k1 k2 k3 k4 <<<"$keys"
  case $((n % 3)) in
    0) datefmt='%d/%m/%Y'; money='"{:.2f}".format(v)' ;;
    1) datefmt='%Y-%m-%d'; money='"{:,.2f}".format(v)' ;;
    2) datefmt='%b %d, %Y'; money='"${:.2f}".format(v)' ;;
  esac
  case $((n % 3)) in
    0) body="    out = []
    for item in _read():
        out.append({\"$k1\": item[\"$k1\"], \"$k2\": _when(item[\"$k2\"]), \"$k3\": item[\"$k3\"], \"$k4\": _money(item[\"$k4\"])})
    return out"
       show="    for row in $fn():
        print(row[\"$k1\"], row[\"$k2\"], row[\"$k3\"], row[\"$k4\"])" ;;
    1) body="    for item in _read():
        yield (item[\"$k1\"], item[\"$k3\"], _money(item[\"$k4\"]), _when(item[\"$k2\"]))"
       show="    print(\"$k1\", \"$k3\", \"$k4\", \"$k2\")
    for row in $fn():
        print(*row)" ;;
    2) body="    grouped = {}
    for item in _read():
        grouped.setdefault(item[\"$k3\"], []).append((item[\"$k1\"], _when(item[\"$k2\"]), item[\"$k4\"]))
    return {who: {\"entries\": rows, \"total\": _money(sum(r[2] for r in rows))} for who, rows in grouped.items()}"
       show="    for who, group in $fn().items():
        print(who, group[\"total\"], len(group[\"entries\"]))" ;;
  esac
  cat > "shop/reports/$r.py" <<EOF
import json
from datetime import date
from pathlib import Path

NAME = "$r"
SOURCE = Path(__file__).resolve().parent.parent.parent / "data" / "$r.json"


def _read():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _money(v):
    return $money


def _when(text):
    return date.fromisoformat(text).strftime("$datefmt")


def $fn():
$body


def print_$r():
$show
EOF
  cat > "data/$r.json" <<EOF
[
  {"$k1": 1, "$k2": "2026-10-30", "$k3": "Ada", "$k4": 12.5},
  {"$k1": 2, "$k2": "2026-11-04", "$k3": "Grace", "$k4": 80.0}
]
EOF
  cat > "shop/tests/test_$r.py" <<EOF
from shop.reports import $r


def test_${r}_prints(capsys):
    $r.print_$r()
    assert capsys.readouterr().out
EOF
done <<<"$reports"
{
  echo '"""The legacy reports, one module each."""'
  for r in $names; do echo "from shop.reports import $r"; done
  printf 'REPORTS = {\n'
  for r in $names; do printf '    "%s": %s.print_%s,\n' "$r" "$r" "$r"; done
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
        REPORTS[args.name]()


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


def test_report_command_prints_the_report(capsys):
    cli.main(["report", "tax"])
    assert capsys.readouterr().out
EOF

SPEC='# Shop database

## Problem

The shop tool keeps each dataset in its own JSON file under `data/`: the orders and the fourteen legacy
reports under `shop/reports/`. The operator wants one SQLite database, `data/shop.db`, to be the only
place the commands read from. The tool is the existing `shop` command line, written in standard-library
Python in this workspace under `shop/`. The operator refreshes the JSON files by hand and runs
`shop migrate` again after each refresh.

## Acceptance Criteria

- **AC1.** `shop migrate` copies the orders and the data of each of the fourteen legacy reports from the JSON files under `data/` into `data/shop.db`, one table per file with the same fields.

- **AC2.** Every `shop` command (`orders` and each of the fourteen reports) reads its data from `data/shop.db` and never from the JSON files; where `data/shop.db` does not exist the command exits with a message telling the operator to run `shop migrate`.

- **AC3.** Every command prints exactly what it printed when it read the JSON files, for the same data.

- **AC4.** Running `shop migrate` again, after the JSON files are refreshed, leaves the database holding each record once and the same as a first run on those files would, so the commands print what they would after a first run.
'
printf '%s\n' "$SPEC" | ./lore record create --kind spec --title 'Shop database' --status ready >/dev/null
BLOCK='**Value claim:** You can move the shop'"'"'s data into one database and every command keeps printing what it printed.

**Covers:** AC1, AC2, AC3, AC4

## Unattended run

**Format:** 1
**Rituals:** slice (1 calls decided unattended)
**Outcome:** slice-chosen
**Checkpoint:** slice materialized
**Decisions:**
1. **Chose the whole migration first**
   - Question: which slice comes next for the shop database spec
   - Choice: the slice that covers every criterion of the migration
   - Reasoning: the four criteria are one surface, the shop commands'"'"' data source, and a slice takes a surface whole
**Parked questions:** none
'
printf '%s\n' "$BLOCK" | ./lore record create --kind task --title 'Move the shop data into one database' --status in-progress --related spec=shop-database --label craft/slice-parent >/dev/null
