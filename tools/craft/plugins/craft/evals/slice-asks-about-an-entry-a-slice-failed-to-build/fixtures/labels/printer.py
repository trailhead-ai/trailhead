def read_labels(path):
    handle = open(path)
    return [line.strip() for line in handle if line.strip()]


def print_labels(path):
    for label in read_labels(path):
        print(label)
