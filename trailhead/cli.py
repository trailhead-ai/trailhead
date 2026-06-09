"""trailhead management CLI.

Subcommands (install/doctor/config) are Step 5 / WS-12.
This stub handles --version and --help only.
"""

import argparse

from trailhead import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="trailhead",
        description="trailhead — manage and compose lore, forge, and camp plugins.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"trailhead {__version__}",
    )
    parser.parse_args()
