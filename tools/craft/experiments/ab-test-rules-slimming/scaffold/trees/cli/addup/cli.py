import argparse
import sys


def main(argv=None, out=None):
    parser = argparse.ArgumentParser(prog="addup", description="Add up numbers.")
    parser.add_argument("numbers", nargs="+", type=float)
    args = parser.parse_args(argv)
    out = out or sys.stdout
    print(f"{sum(args.numbers):g}", file=out)
    return 0
