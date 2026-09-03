from router import route
from store import ROWS


@route("/reports/daily", methods=["GET"])
def daily_report():
    return [row for row in ROWS if row[0] == "order"]


@route("/reports/accounts", methods=["GET"])
def account_report():
    return sorted({row[1].get("account_id") for row in ROWS if "account_id" in row[1]})
