from tally.counter import count_words


def report_line(path):
    return f"{path}: {count_words(path)} words"
