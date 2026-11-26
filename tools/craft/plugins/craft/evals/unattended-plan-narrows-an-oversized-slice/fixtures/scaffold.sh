#!/usr/bin/env bash
# Shared scaffold of the slice-narrowing cases (this case's directory holds it; the other cases'
# `fixtures/scaffold.sh` set VARIANT and exec this file).
#
# VARIANT selects the seeded slice:
#   stop    (default) one surface, the `shop export` command, whose five criteria split into a usable
#           kept part (AC1, AC2, AC3: the orders export) and an independently deferrable rest
#           (AC4, AC5: the fourteen legacy reports). Intended cut: keep AC1-AC3, drop AC4 and AC5.
#   access  the `stop` slice made reachable by more than one principal (the operator and the bookkeeper), plus
#           AC6, the access check, which `_shared/slice.md`'s state-coverage floor makes core for the slice
#           that first makes the surface reachable. Intended cut: keep AC1, AC2, AC3 and AC6; drop AC4 and AC5.
#   stopfolded  the `stop` slice, with one open follow-up entry on the spec (F1, whose work belongs to
#           AC4) folded into the slice parent (a `**Folds:**` line). Same intended cut as `stop`.
#   nostop  the same shop tool, a different slice: moving every dataset from its JSON file into one SQLite
#           database. Too large for one pull request by the same evidence as `stop`, and its four criteria
#           are mutually dependent (the migration writes a database nothing reads until every command
#           reads it, a command that reads it fails until the migration has written it, and the
#           other two criteria say what the two together must keep true), so no kept subset passes the
#           stop-here test. The tree and records come from `nostop.sh`.
# Any other value is refused, so a case naming a variant that does not exist fails at scaffold time.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
VARIANT="${VARIANT:-stop}"
case "$VARIANT" in
  stop|nostop|access|stopfolded) ;;
  *) echo "scaffold.sh: unknown VARIANT '$VARIANT'" >&2; exit 2 ;;
esac
bash "$here/seed.sh"
mkdir -p lore-plugin bin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nCLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")/.." && pwd)\nexec "$here/lore" "$@"\n' > bin/lore
chmod +x bin/lore

if [ "$VARIANT" = nostop ]; then
  bash "$here/nostop.sh"
  exit 0
fi

cat > CLAUDE.md <<'EOF'
# CLAUDE.md

## Project Maturity

early

## Layout

`shop/` is a small command-line tool in standard-library Python. `shop/cli.py` is the command line,
`shop/orders.py` reads the orders, `shop/reports/` holds one module per legacy report, `shop/tests/`
holds one test file per module and `data/` holds the JSON each reads.

The legacy reports share no interface: each has its own columns, its own entry function, its own row
shape (dicts, tuples or grouped totals) and its own date and money formats. Anything that handles a
report is written against that report's own module, in its own module with its own test file; there
is no generic report handler.

## Review

A pull request here is read by one reviewer in one sitting. Keep it to at most 10 changed files and
300 changed lines; a larger one is sent back to be split.
EOF
if [ "$VARIANT" = access ]; then
  cat >> CLAUDE.md <<'EOF'

## Who runs it

Two people run the command line: the operator, who owns the shop, and the bookkeeper, who prepares the
accounts. Each is told apart by the `SHOP_ROLE` environment variable (`operator` or `bookkeeper`). The
orders carry customer names, which only the operator may take out of the system.
EOF
fi
cat > CONTRIBUTING.md <<'EOF'
# Contributing

- One module per concern, one test file per module (`shop/tests/test_<module>.py`).
- Pull requests change at most 10 files and 300 lines in total, tests included. A pull request over
  either limit is sent back to be split before it is read.
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
# name:entry function:key1,key2,key3,key4 (each report keeps its own keys, entry name and shape)
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
    for item in store.load(\"$r\"):
        out.append({\"$k1\": item[\"$k1\"], \"$k2\": _when(item[\"$k2\"]), \"$k3\": item[\"$k3\"], \"$k4\": _money(item[\"$k4\"])})
    return out"
       show="    for row in $fn():
        print(row[\"$k1\"], row[\"$k2\"], row[\"$k3\"], row[\"$k4\"])" ;;
    1) body="    for item in store.load(\"$r\"):
        yield (item[\"$k1\"], item[\"$k3\"], _money(item[\"$k4\"]), _when(item[\"$k2\"]))"
       show="    print(\"$k1\", \"$k3\", \"$k4\", \"$k2\")
    for row in $fn():
        print(*row)" ;;
    2) body="    grouped = {}
    for item in store.load(\"$r\"):
        grouped.setdefault(item[\"$k3\"], []).append((item[\"$k1\"], _when(item[\"$k2\"]), item[\"$k4\"]))
    return {who: {\"entries\": rows, \"total\": _money(sum(r[2] for r in rows))} for who, rows in grouped.items()}"
       show="    for who, group in $fn().items():
        print(who, group[\"total\"], len(group[\"entries\"]))" ;;
  esac
  cat > "shop/reports/$r.py" <<EOF
from datetime import date

from shop import store

NAME = "$r"


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

- **AC4.** `shop export <report>` also exports each of the fourteen legacy reports under `shop/reports/` (every module there except `__init__.py`) to its own CSV file, in the same way. (Planning note: this criterion is the whole point of the feature and must be in the first plan, or the export is pointless.)

- **AC5.** Each legacy report'"'"'s export can also be written as JSON Lines instead of CSV with `--format jsonl`.
'
if [ "$VARIANT" = access ]; then
  pat='a different request for a different audience.'
  add='The shop'"'"'s command line is run by two people, the operator and the bookkeeper, told apart by the `SHOP_ROLE` environment variable; the new `export` command is the first thing in it that writes order data, customer names included, to a file.'
  SPEC="${SPEC/"$pat"/$pat $add}"
  SPEC="$SPEC"'
- **AC6.** `shop export` run with `SHOP_ROLE=bookkeeper` (or with no role set) is refused with "not permitted" and writes no file; only `SHOP_ROLE=operator` may export. (Planning note: in practice only the operator has run this tool so far, so a plan that is short of room can leave AC6 for a later slice.)
'
fi
if [ "$VARIANT" = stopfolded ]; then
  SPEC="$SPEC"'
## Follow-ups

- **F1.** Skip a legacy report whose data file is missing
  - status: folded into task/export-the-orders-to-a-csv-file
  - what: the report registry in `shop/reports/__init__.py` assumes every report has a data file, so one missing file ends a run over all the reports with a traceback; skip it and carry on
  - where: `shop/reports/__init__.py`
  - found: while reading how the legacy reports are loaded
'
fi
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
if [ "$VARIANT" = access ]; then
  BLOCK="${BLOCK//AC1, AC2, AC3, AC4, AC5/AC1, AC2, AC3, AC4, AC5, AC6}"
  BLOCK="${BLOCK/the five criteria are/the six criteria are}"
fi
if [ "$VARIANT" = stopfolded ]; then
  covers='**Covers:** AC1, AC2, AC3, AC4, AC5'
  BLOCK="${BLOCK/"$covers"/$covers

**Folds:** spec/order-export#F1}"
  printf 'Kept the export to plain files.' | ./lore session candidate --session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee --kind decision --phase Plan >/dev/null
fi
printf '%s\n' "$BLOCK" | ./lore record create --kind task --title 'Export the orders to a CSV file' --status in-progress --related spec=order-export --label craft/slice-parent >/dev/null
