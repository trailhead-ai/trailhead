import sys

from tally.counter import count_words


def main(argv):
    path = argv[1]
    print(f"words: {count_words(path)}")


if __name__ == "__main__":
    main(sys.argv)
