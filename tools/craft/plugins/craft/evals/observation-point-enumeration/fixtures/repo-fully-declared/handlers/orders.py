from router import route
from schema import validate
from store import persist


@route("/orders", methods=["POST"])
def create_order(payload):
    validate(payload, "order")
    return persist("order", payload)
