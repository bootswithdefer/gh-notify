"""Data models for gh-notify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class NotificationType(Enum):
    """Types of notification events."""

    REVIEW_REQUESTED = "review_requested"
    MENTION = "mention"
    COMMENT = "comment"
    CI_STATUS = "ci_status"


class ReviewStatus(Enum):
    """Review decision status for a PR."""

    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REVIEW_REQUIRED = "review_required"
    DISMISSED = "dismissed"


class ChecksStatus(Enum):
    """Combined CI/checks status for a PR."""

    PENDING = "pending"
    PASSING = "passing"
    FAILING = "failing"
    NONE = "none"


@dataclass(frozen=True)
class PullRequest:
    """Represents a GitHub pull request."""

    number: int
    title: str
    repo_full_name: str
    author: str
    url: str
    html_url: str
    updated_at: datetime
    draft: bool = False
    review_status: ReviewStatus = ReviewStatus.PENDING
    checks_status: ChecksStatus = ChecksStatus.NONE

    @property
    def display_name(self) -> str:
        """Short display name for menus."""
        return f"{self.repo_full_name}#{self.number}"


@dataclass(frozen=True)
class NotificationEvent:
    """A notification event to display to the user."""

    id: str
    notification_type: NotificationType
    title: str
    body: str
    pr: PullRequest
    timestamp: datetime

    @property
    def summary(self) -> str:
        """One-line summary for the notification."""
        match self.notification_type:
            case NotificationType.REVIEW_REQUESTED:
                return f"Review requested: {self.pr.display_name}"
            case NotificationType.MENTION:
                return f"Mentioned in {self.pr.display_name}"
            case NotificationType.COMMENT:
                return f"Comment on {self.pr.display_name}"
            case NotificationType.CI_STATUS:
                return f"CI update: {self.pr.display_name}"
