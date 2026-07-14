"""Entry point for gh-notify."""

from __future__ import annotations

import logging
import sys


def main() -> None:
    """Run the gh-notify application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from gh_notify.app import GhNotifyApp

    app = GhNotifyApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
