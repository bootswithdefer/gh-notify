"""Main application with system tray icon and context menu."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QSize, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from gh_notify.config import Config
from gh_notify.main_window import MainWindow
from gh_notify.models import NotificationEvent, PullRequest
from gh_notify.notifier import Notifier
from gh_notify.poller import Poller
from gh_notify.pr_store import PrStore

logger = logging.getLogger(__name__)

ICON_PATH = Path(__file__).parent / "icons" / "gh-notify.svg"
ICON_ATTENTION_PATH = Path(__file__).parent / "icons" / "gh-notify-attention.svg"
ICON_POLL_PATHS = [Path(__file__).parent / "icons" / f"gh-notify-poll-{i}.svg" for i in range(4)]

SINGLE_INSTANCE_KEY = "gh-notify-single-instance"


class GhNotifyApp:
    """Main application class managing tray icon, poller, and notifications."""

    def __init__(self) -> None:
        self._app = QApplication(sys.argv)
        self._app.setApplicationName("gh-notify")
        self._app.setApplicationDisplayName("GitHub PR Monitor")
        self._app.setOrganizationName("gh-notify")
        self._app.setDesktopFileName("gh-notify")
        self._app.setQuitOnLastWindowClosed(False)

        # Single instance enforcement
        if not self._acquire_lock():
            logger.error("gh-notify is already running")
            sys.exit(0)

        # Set app-wide window icon
        app_icon = self._load_icon(ICON_PATH)
        self._app.setWindowIcon(app_icon)

        self._config = Config.load()
        self._notifier = Notifier()
        self._poller = Poller(self._config)
        self._store = PrStore()

        # Main window (created once, shown/hidden on demand)
        self._main_window = MainWindow(self._store)

        self._review_prs: list[PullRequest] = []
        self._authored_prs: list[PullRequest] = []

        # Polling animation state
        self._poll_icons: list[QIcon] = []
        self._poll_frame = 0
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._animate_poll_icon)

        self._setup_tray()
        self._connect_signals()

        # Cleanup on quit
        self._app.aboutToQuit.connect(self._cleanup)

    def _acquire_lock(self) -> bool:
        """Try to acquire a single-instance lock via QLocalServer."""
        # Try connecting to an existing instance
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if socket.waitForConnected(500):
            # Another instance is running
            socket.close()
            return False
        socket.close()

        # Create the server lock
        self._lock_server = QLocalServer()
        # Remove stale socket file if previous crash left one
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        return self._lock_server.listen(SINGLE_INSTANCE_KEY)

    def _setup_tray(self) -> None:
        """Set up the system tray icon and context menu."""
        self._tray = QSystemTrayIcon(self._app)

        # Load icons
        self._icon_normal = self._load_icon(ICON_PATH)
        self._icon_attention = self._load_icon(ICON_ATTENTION_PATH)
        self._poll_icons = [self._load_icon(p) for p in ICON_POLL_PATHS]
        self._tray.setIcon(self._icon_normal)
        self._tray.setToolTip("gh-notify — GitHub PR Monitor")

        # Context menu
        self._menu = QMenu()
        self._open_action = self._menu.addAction("Open gh-notify")
        self._open_action.triggered.connect(self._show_main_window)

        self._menu.addSeparator()

        self._review_section_action = self._menu.addAction("— PRs Awaiting Review —")
        self._review_section_action.setEnabled(False)
        self._review_separator = self._menu.addSeparator()

        self._authored_section_action = self._menu.addAction("— Your Open PRs —")
        self._authored_section_action.setEnabled(False)
        self._authored_separator = self._menu.addSeparator()

        self._settings_action = self._menu.addAction("Settings...")
        self._settings_action.triggered.connect(self._open_settings)

        self._menu.addSeparator()
        self._quit_action = self._menu.addAction("Quit")
        self._quit_action.triggered.connect(self._quit)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _load_icon(self, path: Path) -> QIcon:
        """Load an icon from file path, falling back to a theme icon."""
        if path.exists():
            return QIcon(str(path))
        # Fallback: try theme icon
        icon = QIcon.fromTheme("github")
        if icon.isNull():
            # Generate a simple colored pixmap as last resort
            pixmap = QPixmap(QSize(64, 64))
            pixmap.fill()
            return QIcon(pixmap)
        return icon

    def _connect_signals(self) -> None:
        """Connect poller signals to handlers."""
        self._poller.new_events.connect(self._on_new_events)
        self._poller.review_prs_updated.connect(self._on_review_prs_updated)
        self._poller.authored_prs_updated.connect(self._on_authored_prs_updated)
        self._poller.error_occurred.connect(self._on_error)
        self._poller.polling_started.connect(self._on_polling_started)
        self._poller.polling_finished.connect(self._on_polling_finished)
        self._poller.progress.connect(self._on_progress)

    def _on_new_events(self, events: list[NotificationEvent]) -> None:
        """Handle new notification events."""
        for event in events:
            self._notifier.send_async(event)

        # Show attention icon when there are review requests
        if any(e.notification_type.value == "review_requested" for e in events):
            self._tray.setIcon(self._icon_attention)

    def _on_review_prs_updated(self, prs: list[PullRequest]) -> None:
        """Update the review PRs via the store (triggers incremental UI updates)."""
        self._review_prs = prs
        self._store.update_review_prs(prs)
        self._rebuild_menu()

        # Update icon based on whether there are review requests
        if prs:
            self._tray.setIcon(self._icon_attention)
            self._tray.setToolTip(f"gh-notify — {len(prs)} PR(s) awaiting review")
        else:
            self._tray.setIcon(self._icon_normal)
            self._tray.setToolTip("gh-notify — GitHub PR Monitor")

    def _on_authored_prs_updated(self, prs: list[PullRequest]) -> None:
        """Update the authored PRs via the store (triggers incremental UI updates)."""
        self._authored_prs = prs
        self._store.update_authored_prs(prs)
        self._rebuild_menu()

    def _on_error(self, message: str) -> None:
        """Handle polling errors."""
        logger.error("Polling error: %s", message)
        self._tray.setToolTip(f"gh-notify — Error: {message[:50]}")

    def _rebuild_menu(self) -> None:
        """Rebuild the context menu with current PR lists."""
        self._menu.clear()

        # Open window action
        open_action = self._menu.addAction("Open gh-notify")
        open_action.triggered.connect(self._show_main_window)
        self._menu.addSeparator()

        max_visible = 5

        # Review section
        review_header = self._menu.addAction(f"— PRs Awaiting Review ({len(self._review_prs)}) —")
        review_header.setEnabled(False)

        if self._review_prs:
            self._add_pr_items(self._menu, self._review_prs, max_visible)
        else:
            none_action = self._menu.addAction("  (none)")
            none_action.setEnabled(False)

        self._menu.addSeparator()

        # Authored section
        authored_header = self._menu.addAction(f"— Your Open PRs ({len(self._authored_prs)}) —")
        authored_header.setEnabled(False)

        if self._authored_prs:
            self._add_pr_items(self._menu, self._authored_prs, max_visible)
        else:
            none_action = self._menu.addAction("  (none)")
            none_action.setEnabled(False)

        self._menu.addSeparator()

        # Settings and Quit
        settings_action = self._menu.addAction("Settings...")
        settings_action.triggered.connect(self._open_settings)

        self._menu.addSeparator()

        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self._quit)

    def _add_pr_items(self, menu: QMenu, prs: list[PullRequest], max_visible: int) -> None:
        """Add PR items to a menu, using a submenu for overflow."""
        visible = prs[:max_visible]
        overflow = prs[max_visible:]

        for pr in visible:
            action = menu.addAction(f"  {pr.display_name}: {_truncate(pr.title, 50)}")
            action.triggered.connect(_make_open_handler(pr.html_url))

        if overflow:
            more_menu = menu.addMenu(f"  … {len(overflow)} more")
            for pr in overflow:
                action = more_menu.addAction(f"{pr.display_name}: {_truncate(pr.title, 50)}")
                action.triggered.connect(_make_open_handler(pr.html_url))

    def _on_polling_started(self) -> None:
        """Start the icon animation when polling begins."""
        self._poll_frame = 0
        self._poll_timer.start()
        self._main_window.set_status("Updating…")

    def _on_polling_finished(self) -> None:
        """Stop the icon animation when polling completes."""
        self._poll_timer.stop()
        # Restore the appropriate static icon
        if self._review_prs:
            self._tray.setIcon(self._icon_attention)
        else:
            self._tray.setIcon(self._icon_normal)
        self._main_window.update_idle_status()

    def _on_progress(self, message: str) -> None:
        """Forward progress messages to the main window status bar."""
        self._main_window.set_status(message)

    def _animate_poll_icon(self) -> None:
        """Advance to the next animation frame."""
        if self._poll_icons:
            self._tray.setIcon(self._poll_icons[self._poll_frame % len(self._poll_icons)])
            self._poll_frame += 1

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Left click — show/raise main window
            self._show_main_window()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            # Right click — context menu (handled automatically by setContextMenu)
            pass

    def _show_main_window(self) -> None:
        """Show and raise the main window."""
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _open_settings(self) -> None:
        """Open the settings dialog."""
        from gh_notify.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._config, self._main_window)
        if dialog.exec():
            self._config = dialog.get_config()
            self._config.save()
            self._poller.update_config(self._config)

    def _quit(self) -> None:
        """Quit the application."""
        self._app.quit()

    def _cleanup(self) -> None:
        """Clean up resources on application exit."""
        self._poll_timer.stop()
        self._poller.stop()
        self._notifier.shutdown()
        self._tray.hide()
        if hasattr(self, "_lock_server"):
            self._lock_server.close()

    def run(self) -> int:
        """Start the application."""
        self._poller.start()
        return self._app.exec()


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _make_open_handler(url: str):
    """Create a click handler that opens a URL."""

    def handler():
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603

    return handler
