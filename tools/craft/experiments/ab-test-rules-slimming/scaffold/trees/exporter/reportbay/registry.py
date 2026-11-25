EXPORTERS = {}


def register(name):
    def wrap(fn):
        EXPORTERS[name] = fn
        return fn
    return wrap


def export(rows, fmt):
    try:
        exporter = EXPORTERS[fmt]
    except KeyError:
        raise ValueError(f"unknown format: {fmt}") from None
    return exporter(rows)
