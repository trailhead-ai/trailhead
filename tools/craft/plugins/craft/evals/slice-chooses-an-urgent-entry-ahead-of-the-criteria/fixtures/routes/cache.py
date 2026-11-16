CACHE = {}


def cached_route(key, compute):
    if key not in CACHE:
        CACHE[key] = compute()
    return CACHE[key]
