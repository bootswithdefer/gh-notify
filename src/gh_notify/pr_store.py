"""PR state tracker that detects additions, removals, and updates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal

from gh_notify.models import PullRequest


class PrCategory(Enum):
    """Which category a PR belongs to in the UI."""

    REVIEW_REQUESTED = "review_requested"
    AUTHORED = "authored"


@dataclass
class PrChange:
    """Describes a change to a tracked PR."""

    pr: PullRequest
    category: PrCategory


class PrStore(QObject):
    """Tracks PR state and emits granular change signals.

    Instead of replacing the entire list on each poll, this compares the new
    set against the previous one and emits signals for individual additions,
    removals, and updates. The UI can then update only the affected rows.
    """

    # Emitted for each new PR that appeared
    pr_added = pyqtSignal(object, object)  # (PullRequest, PrCategory)
    # Emitted for each PR that disappeared
    pr_removed = pyqtSignal(object, object)  # (PullRequest, PrCategory)
    # Emitted for each PR whose data changed (e.g., title, updated_at)
    pr_updated = pyqtSignal(object, object)  # (PullRequest, PrCategory)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Keyed by (repo_full_name, number) -> PullRequest
        self._review_prs: dict[tuple[str, int], PullRequest] = {}
        self._authored_prs: dict[tuple[str, int], PullRequest] = {}

    @property
    def review_prs(self) -> list[PullRequest]:
        """Current review-requested PRs."""
        return list(self._review_prs.values())

    @property
    def authored_prs(self) -> list[PullRequest]:
        """Current authored PRs."""
        return list(self._authored_prs.values())

    @property
    def all_prs(self) -> list[tuple[PullRequest, PrCategory]]:
        """All tracked PRs with their category."""
        result: list[tuple[PullRequest, PrCategory]] = []
        for pr in self._review_prs.values():
            result.append((pr, PrCategory.REVIEW_REQUESTED))
        for pr in self._authored_prs.values():
            result.append((pr, PrCategory.AUTHORED))
        return result

    def update_review_prs(self, new_prs: list[PullRequest]) -> None:
        """Update the review-requested PR set, emitting granular change signals."""
        self._update_category(self._review_prs, new_prs, PrCategory.REVIEW_REQUESTED)

    def update_authored_prs(self, new_prs: list[PullRequest]) -> None:
        """Update the authored PR set, emitting granular change signals."""
        self._update_category(self._authored_prs, new_prs, PrCategory.AUTHORED)

    def _update_category(
        self,
        current: dict[tuple[str, int], PullRequest],
        new_prs: list[PullRequest],
        category: PrCategory,
    ) -> None:
        """Diff the current set against the new list and emit change signals."""
        new_map: dict[tuple[str, int], PullRequest] = {}
        for pr in new_prs:
            new_map[_pr_key(pr)] = pr

        old_keys = set(current.keys())
        new_keys = set(new_map.keys())

        # Removals
        for key in old_keys - new_keys:
            pr = current.pop(key)
            self.pr_removed.emit(pr, category)

        # Additions
        for key in new_keys - old_keys:
            pr = new_map[key]
            current[key] = pr
            self.pr_added.emit(pr, category)

        # Updates (same PR, but data changed)
        for key in old_keys & new_keys:
            old_pr = current[key]
            new_pr = new_map[key]
            if _pr_changed(old_pr, new_pr):
                current[key] = new_pr
                self.pr_updated.emit(new_pr, category)


def _pr_key(pr: PullRequest) -> tuple[str, int]:
    """Unique key for a PR."""
    return (pr.repo_full_name, pr.number)


def _pr_changed(old: PullRequest, new: PullRequest) -> bool:
    """Check if any visible PR field changed."""
    return old.title != new.title or old.updated_at != new.updated_at or old.draft != new.draft
