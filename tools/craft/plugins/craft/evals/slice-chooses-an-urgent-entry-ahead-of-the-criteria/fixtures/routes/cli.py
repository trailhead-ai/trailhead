import sys

from routes.planner import plan


def main(argv):
    print(plan(argv[1]))


if __name__ == "__main__":
    main(sys.argv)
