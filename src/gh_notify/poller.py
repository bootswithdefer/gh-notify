"""Polling logic for GitHub notifications and PR monitoring."""

from __future__ import annotations

import logging
import re

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from gh_notify.config import Config
from gh_notify.github_client import GitHubClient, GitHubClientError
from gh_notify.models import NotificationEvent, NotificationType, PullRequest

logger = logging.getLogger(__name__)


class _PollWorker(QObject):
    """Worker that performs HTTP polling and parsing in a background thread.

    All network I/O and data parsing happens here — nothing blocks the main thread.
    """

    # Fully processed results emitted to main thread
    notifications_ready = pyqtSignal(list)  # list[NotificationEvent]
    review_prs_ready = pyqtSignal(list)  # list[PullRequest]
    authored_prs_ready = pyqtSignal(list)  # list[PullRequest]
    poll_interval_changed = pyqtSignal(int)  # new interval in ms
    error_occurred = pyqtSignal(str)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._client: GitHubClient | None = None

    def _get_client(self) -> GitHubClient:
        """Lazy-init the client on the worker thread."""
        if self._client is None:
            self._client = GitHubClient()
        return self._client

    @pyqtSlot()
    def poll(self) -> None:
        """Perform a single polling cycle. Runs in the worker thread."""
        try:
            client = self._get_client()
            username = self._config.username or client.username

            # Fetch and parse notifications into events
            raw_notifications = client.fetch_notifications()
            events: list[NotificationEvent] = []
            for raw in raw_notifications:
                event = client.parse_notification_to_event(raw)
                if event is not None:
                    events.append(event)
            self.notifications_ready.emit(events)

            # Fetch review-requested PRs
            prs = client.fetch_review_requested_prs(username)
            prs = [pr for pr in prs if not self._is_pr_filtered(pr)]
            self.review_prs_ready.emit(prs)

            # Fetch authored PRs
            authored = client.fetch_authored_prs(username)
            authored = [pr for pr in authored if not self._is_pr_filtered(pr)]
            self.authored_prs_ready.emit(authored)

            # Report server-recommended poll interval
            server_interval_ms = client.poll_interval * 1000
            config_interval_ms = self._config.poll_interval_seconds * 1000
            effective_ms = max(server_interval_ms, config_interval_ms)
            self.poll_interval_changed.emit(effective_ms)

        except GitHubClientError as e:
            logger.exception("Polling error")
            self.error_occurred.emit(str(e))

    def update_config(self, config: Config) -> None:
        """Update configuration."""
        self._config = config

    @pyqtSlot()
    def cleanup(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def _is_pr_filtered(self, pr: PullRequest) -> bool:
        """Check if a PR should be filtered out based on config filters."""
        filters = self._config.filters

        if pr.repo_full_name in filters.exclude_repos:
            return True

        if pr.author and pr.author in filters.exclude_authors:
            return True

        for pattern in filters.exclude_title_patterns:
            try:
                if re.search(pattern, pr.title, re.IGNORECASE):
                    return True
            except re.error:
                logger.warning("Invalid regex pattern in filters: %s", pattern)

        return False


class Poller(QObject):
    """Polls GitHub for notifications and PR updates using a background thread.

    The UI remains fully responsive — all HTTP calls and parsing happen off
    the main thread. PR data is cached and the menu is built from the cache
    instantly on right-click.
    """

    # Signal to trigger poll on the worker thread
    _do_poll = pyqtSignal()

    # Emitted when new notification events arrive (after deduplication + filtering)
    new_events = pyqtSignal(list)  # list[NotificationEvent]
    # Emitted when review-requested PRs are updated
    review_prs_updated = pyqtSignal(list)  # list[PullRequest]
    # Emitted when authored PRs are updated
    authored_prs_updated = pyqtSignal(list)  # list[PullRequest]
    # Emitted on error
    error_occurred = pyqtSignal(str)

    def __init__(self, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._seen_ids: set[str] = set()
        self._initial_poll = True
        self._review_prs: list[PullRequest] = []
        self._authored_prs: list[PullRequest] = []

        # Background thread setup
        self._thread = QThread()
        self._worker = _PollWorker(config)
        self._worker.moveToThread(self._thread)

        # Connect the trigger signal to worker.poll (queued cross-thread connection)
        self._do_poll.connect(self._worker.poll)

        # Connect worker result signals to main-thread handlers
        self._worker.notifications_ready.connect(self._on_notifications_ready)
        self._worker.review_prs_ready.connect(self._on_review_prs_ready)
        self._worker.authored_prs_ready.connect(self._on_authored_prs_ready)
        self._worker.poll_interval_changed.connect(self._on_poll_interval_changed)
        self._worker.error_occurred.connect(self.error_occurred)

        # Timer fires on main thread, emits signal to trigger poll on worker thread
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._trigger_poll)

    @property
    def review_prs(self) -> list[PullRequest]:
        """Current list of PRs requesting review (cached)."""
        return self._review_prs

    @property
    def authored_prs(self) -> list[PullRequest]:
        """Current list of authored PRs (cached)."""
        return self._authored_prs

    def start(self) -> None:
        """Start the worker thread and begin polling."""
        self._thread.start()
        interval_ms = self._config.poll_interval_seconds * 1000
        self._timer.start(interval_ms)
        # Immediate first poll (non-blocking — just emits signal to worker thread)
        self._trigger_poll()

    def stop(self) -> None:
        """Stop polling and clean up the worker thread."""
        self._timer.stop()
        self._worker.cleanup()
        self._thread.quit()
        self._thread.wait(2000)
        if self._thread.isRunning():
            self._thread.terminate()

    def update_config(self, config: Config) -> None:
        """Update configuration and restart timer with new interval."""
        self._config = config
        self._worker.update_config(config)
        if self._timer.isActive():
            self._timer.setInterval(self._config.poll_interval_seconds * 1000)

    def _trigger_poll(self) -> None:
        """Emit signal to trigger poll on the worker thread (non-blocking)."""
        self._do_poll.emit()

    def _on_notifications_ready(self, events: list[NotificationEvent]) -> None:
        """Filter and deduplicate events on the main thread (no I/O, instant)."""
        new_events: list[NotificationEvent] = []
        for event in events:
            if event.id in self._seen_ids:
                continue
            if not self._should_notify(event):
                continue
            if self._is_filtered(event):
                continue
            self._seen_ids.add(event.id)
            new_events.append(event)

        # Suppress notifications on the first poll to avoid spamming on startup
        if self._initial_poll:
            self._initial_poll = False
            return

        if new_events:
            self.new_events.emit(new_events)

    def _on_review_prs_ready(self, prs: list[PullRequest]) -> None:
        """Cache and emit review PRs."""
        self._review_prs = prs
        self.review_prs_updated.emit(prs)

    def _on_authored_prs_ready(self, prs: list[PullRequest]) -> None:
        """Cache and emit authored PRs."""
        self._authored_prs = prs
        self.authored_prs_updated.emit(prs)

    def _on_poll_interval_changed(self, interval_ms: int) -> None:
        """Adjust timer if server recommends a different interval."""
        if self._timer.interval() != interval_ms:
            self._timer.setInterval(interval_ms)

    def _should_notify(self, event: NotificationEvent) -> bool:
        """Check if this event type should generate a notification based on config."""
        match event.notification_type:
            case NotificationType.REVIEW_REQUESTED:
                return self._config.notifications.review_requested
            case NotificationType.MENTION:
                return self._config.notifications.mentions
            case NotificationType.COMMENT:
                return self._config.notifications.pr_comments
            case NotificationType.CI_STATUS:
                return self._config.notifications.ci_status

    def _is_filtered(self, event: NotificationEvent) -> bool:
        """Check if this event should be filtered out."""
        return self._is_pr_filtered(event.pr)

    def _is_pr_filtered(self, pr: PullRequest) -> bool:
        """Check if a PR should be filtered out based on config filters."""
        filters = self._config.filters

        if pr.repo_full_name in filters.exclude_repos:
            return True

        if pr.author and pr.author in filters.exclude_authors:
            return True

        for pattern in filters.exclude_title_patterns:
            try:
                if re.search(pattern, pr.title, re.IGNORECASE):
                    return True
            except re.error:
                logger.warning("Invalid regex pattern in filters: %s", pattern)

        return False
