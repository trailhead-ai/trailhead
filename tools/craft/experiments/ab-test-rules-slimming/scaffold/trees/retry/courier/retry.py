RETRY_LIMIT = 5


def deliver(send, message, limit=None):
    """Call send(message), retrying on ConnectionError, up to `limit` attempts in all."""
    limit = RETRY_LIMIT if limit is None else limit
    last = None
    for _ in range(limit):
        try:
            return send(message)
        except ConnectionError as exc:
            last = exc
    raise last
