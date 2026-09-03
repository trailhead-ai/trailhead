from router import route
from store import persist


@route("/refunds", methods=["POST"])
def create_refund(payload):
    return persist("refund", payload)
