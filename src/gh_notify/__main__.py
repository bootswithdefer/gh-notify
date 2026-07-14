"""Entry point for gh-notify."""

from __future__ import annotations

import logging
import os
import signal
import sys


def main() -> None:
    """Run the gh-notify application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Suppress non-critical XDG portal registration warning when the .desktop
    # file isn't installed system-wide. The app functions normally without it.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.services.warning=false")

    # Allow Ctrl+C to work with Qt's event loop
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    from gh_notify.app import GhNotifyApp

    app = GhNotifyApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
