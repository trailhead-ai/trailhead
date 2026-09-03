"""Toy router. `methods=["POST"]` marks a write endpoint."""

ROUTES = []


def route(path, methods):
    def decorator(fn):
        ROUTES.append((path, tuple(methods), fn))
        return fn

    return decorator
