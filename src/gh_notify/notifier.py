"""Desktop notification handling using desktop-notifier library."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from desktop_notifier import DesktopNotifier, Icon, Urgency

if TYPE_CHECKING:
    from gh_notify.models import NotificationEvent

logger = logging.getLogger(__name__)

ICON_PATH = Path(__file__).parent / "icons" / "gh-notify.svg"


class Notifier:
    """Sends desktop notifications for GitHub PR events.

    Notifications are sent asynchronously on a dedicated background thread
    so they never block the Qt event loop.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._notifier = DesktopNotifier(
            app_name="gh-notify",
            app_icon=self._get_icon(),
        )

    def _run_loop(self) -> None:
        """Run the asyncio event loop in a background thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _get_icon(self) -> Icon | None:
        """Get the Icon object for the app icon."""
        if ICON_PATH.exists():
            return Icon(path=ICON_PATH)
        return None

    def send_async(self, event: NotificationEvent) -> None:
        """Schedule a notification to be sent (non-blocking, fire-and-forget)."""
        asyncio.run_coroutine_threadsafe(self._send(event), self._loop)

    async def _send(self, event: NotificationEvent) -> None:
        """Send a desktop notification for an event."""
        try:
            await self._notifier.send(
                title=event.summary,
                message=event.title,
                urgency=Urgency.Normal,
                on_clicked=lambda: _open_url(event.pr.html_url),
            )
        except Exception:
            logger.exception("Failed to send notification")

    def shutdown(self) -> None:
        """Stop the background event loop."""
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


def _open_url(url: str) -> None:
    """Open a URL in the default browser."""
    if not url:
        return
    try:
        if sys.platform == "linux":
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
        else:
            import webbrowser

            webbrowser.open(url)
    except Exception:
        logger.exception("Failed to open URL: %s", url)
