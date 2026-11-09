def read_text(path):
    handle = open(path)
    return handle.read()


def count_words(path):
    return len(read_text(path).split())
