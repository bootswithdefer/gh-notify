"""Polling logic for GitHub notifications and PR monitoring."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from gh_notify.config import Config
from gh_notify.github_client import GitHubClient, GitHubClientError
from gh_notify.models import NotificationEvent, NotificationType, PullRequest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class Poller(QObject):
    """Polls GitHub for notifications and PR updates using QTimer."""

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
        self._client = GitHubClient()
        self._seen_ids: set[str] = set()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._review_prs: list[PullRequest] = []
        self._authored_prs: list[PullRequest] = []

    @property
    def review_prs(self) -> list[PullRequest]:
        """Current list of PRs requesting review."""
        return self._review_prs

    @property
    def authored_prs(self) -> list[PullRequest]:
        """Current list of authored PRs."""
        return self._authored_prs

    def start(self) -> None:
        """Start polling."""
        interval_ms = self._config.poll_interval_seconds * 1000
        self._timer.start(interval_ms)
        # Do an immediate poll
        self._poll()

    def stop(self) -> None:
        """Stop polling."""
        self._timer.stop()
        self._client.close()

    def update_config(self, config: Config) -> None:
        """Update configuration and restart timer with new interval."""
        self._config = config
        if self._timer.isActive():
            self._timer.setInterval(self._config.poll_interval_seconds * 1000)

    def _get_username(self) -> str:
        """Get the username to use for queries."""
        if self._config.username:
            return self._config.username
        return self._client.username

    def _poll(self) -> None:
        """Perform a polling cycle."""
        try:
            username = self._get_username()
            self._poll_notifications()
            self._poll_review_prs(username)
            self._poll_authored_prs(username)

            # Adjust timer to server-recommended interval if longer
            server_interval = self._client.poll_interval * 1000
            current_interval = self._config.poll_interval_seconds * 1000
            effective_interval = max(server_interval, current_interval)
            if self._timer.interval() != effective_interval:
                self._timer.setInterval(effective_interval)

        except GitHubClientError as e:
            logger.exception("Polling error")
            self.error_occurred.emit(str(e))

    def _poll_notifications(self) -> None:
        """Poll the notifications endpoint for new events."""
        raw_notifications = self._client.fetch_notifications()

        new_events: list[NotificationEvent] = []
        for raw in raw_notifications:
            event = self._client.parse_notification_to_event(raw)
            if event is None:
                continue
            if event.id in self._seen_ids:
                continue
            if not self._should_notify(event):
                continue
            if self._is_filtered(event):
                continue
            self._seen_ids.add(event.id)
            new_events.append(event)

        if new_events:
            self.new_events.emit(new_events)

    def _poll_review_prs(self, username: str) -> None:
        """Poll for PRs requesting review."""
        prs = self._client.fetch_review_requested_prs(username)
        prs = [pr for pr in prs if not self._is_pr_filtered(pr)]
        self._review_prs = prs
        self.review_prs_updated.emit(prs)

    def _poll_authored_prs(self, username: str) -> None:
        """Poll for authored PRs."""
        prs = self._client.fetch_authored_prs(username)
        prs = [pr for pr in prs if not self._is_pr_filtered(pr)]
        self._authored_prs = prs
        self.authored_prs_updated.emit(prs)

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

        # Check repo exclusion
        if pr.repo_full_name in filters.exclude_repos:
            return True

        # Check author exclusion
        if pr.author and pr.author in filters.exclude_authors:
            return True

        # Check title pattern exclusion
        for pattern in filters.exclude_title_patterns:
            try:
                if re.search(pattern, pr.title, re.IGNORECASE):
                    return True
            except re.error:
                logger.warning("Invalid regex pattern in filters: %s", pattern)

        return False
