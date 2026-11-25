def parse_pairs(text):
    """Turn 'a=1; b=2' into {'a': '1', 'b': '2'}."""
    result = {}
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        key, value = part.split("=")[:2]
        result[key.strip()] = value.strip()
    return result
