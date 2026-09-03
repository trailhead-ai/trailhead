from router import route
from schema import validate
from store import persist


@route("/invoices", methods=["POST"])
def create_invoice(payload):
    validate(payload, "invoice")
    return persist("invoice", payload)
