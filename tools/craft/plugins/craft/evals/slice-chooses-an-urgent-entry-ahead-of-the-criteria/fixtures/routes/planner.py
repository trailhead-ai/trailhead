def plan(path):
    with open(path) as handle:
        stops = [line.strip() for line in handle if line.strip()]
    return " -> ".join(stops)
