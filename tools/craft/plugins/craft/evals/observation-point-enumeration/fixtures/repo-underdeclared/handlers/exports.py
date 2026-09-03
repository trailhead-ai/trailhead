from router import route
from store import persist


@route("/exports", methods=["POST"])
def create_export(payload):
    return persist("export", payload)
