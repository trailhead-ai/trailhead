import sys

from summary.totals import invoice_total


def main(argv):
    path = argv[1]
    print(f"total: {invoice_total(path)}")


if __name__ == "__main__":
    main(sys.argv)
