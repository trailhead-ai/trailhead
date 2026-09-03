from router import route
from schema import validate
from store import persist


@route("/exports", methods=["POST"])
def create_export(payload):
    validate(payload, "export")
    return persist("export", payload)
