"""Desktop notification handling using desktop-notifier library."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from desktop_notifier import DesktopNotifier, Urgency

if TYPE_CHECKING:
    from gh_notify.models import NotificationEvent

logger = logging.getLogger(__name__)

ICON_PATH = Path(__file__).parent / "icons" / "gh-notify.svg"


class Notifier:
    """Sends desktop notifications for GitHub PR events."""

    def __init__(self) -> None:
        self._notifier = DesktopNotifier(
            app_name="gh-notify",
            app_icon=self._get_icon_uri(),
        )

    def _get_icon_uri(self) -> str:
        """Get the file URI for the app icon."""
        if ICON_PATH.exists():
            return f"file://{ICON_PATH}"
        return ""

    async def send(self, event: NotificationEvent) -> None:
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

    def send_sync(self, event: NotificationEvent) -> None:
        """Send a notification synchronously (uses asyncio internally)."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.send(event))
            else:
                loop.run_until_complete(self.send(event))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.send(event))


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
