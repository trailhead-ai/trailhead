def read_amounts(path):
    with open(path) as handle:
        return [float(line) for line in handle if line.strip()]


def invoice_total(path):
    return sum(read_amounts(path))
