"""Main application window with a GitHub-style PR list view."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QMainWindow,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gh_notify.models import ChecksStatus, PullRequest, ReviewStatus
from gh_notify.pr_store import PrCategory, PrStore

# Sort role for custom sort data (timestamps as ints, enums as ints)
SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class MainWindow(QMainWindow):
    """Main window displaying PR lists in a GitHub-style layout."""

    def __init__(self, store: PrStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("gh-notify — Pull Requests")
        self.setMinimumSize(1000, 600)

        self._setup_ui()
        self._connect_signals()
        self._populate_initial()

        # Refresh relative timestamps every 60s
        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._refresh_timestamps)
        self._time_timer.start(60_000)

    def _setup_ui(self) -> None:
        """Build the main window UI."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tab widget for review vs authored
        self._tabs = QTabWidget()
        self._review_tree = self._create_pr_tree()
        self._authored_tree = self._create_pr_tree()

        self._tabs.addTab(self._review_tree, "Awaiting Your Review")
        self._tabs.addTab(self._authored_tree, "Your Pull Requests")

        layout.addWidget(self._tabs)

        # Status bar
        self._status_label = QLabel("Loading...")
        self.statusBar().addWidget(self._status_label)

    def _create_pr_tree(self) -> QTreeWidget:
        """Create a QTreeWidget configured for PR display."""
        tree = QTreeWidget()
        tree.setHeaderLabels(["", "Title", "Repository", "Author", "Review", "Checks", "Updated"])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setSortingEnabled(True)

        # Column widths
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # Status icon
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Title
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)  # Repo
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)  # Author
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Review
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)  # Checks
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)  # Updated
        tree.setColumnWidth(0, 30)
        tree.setColumnWidth(2, 180)
        tree.setColumnWidth(3, 120)
        tree.setColumnWidth(4, 100)
        tree.setColumnWidth(5, 80)
        tree.setColumnWidth(6, 100)

        # Default sort by updated descending (most recent first)
        tree.sortByColumn(6, Qt.SortOrder.DescendingOrder)

        # Double-click opens in browser
        tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Style
        tree.setFrameShape(QFrame.Shape.NoFrame)
        tree.setIndentation(0)

        return tree

    def _connect_signals(self) -> None:
        """Connect store signals to UI update methods."""
        self._store.pr_added.connect(self._on_pr_added)
        self._store.pr_removed.connect(self._on_pr_removed)
        self._store.pr_updated.connect(self._on_pr_updated)

    def _populate_initial(self) -> None:
        """Populate trees with current store data."""
        for pr in self._store.review_prs:
            self._add_pr_item(self._review_tree, pr)
        for pr in self._store.authored_prs:
            self._add_pr_item(self._authored_tree, pr)
        self._update_tab_labels()
        self._update_status()

    def _on_pr_added(self, pr: PullRequest, category: PrCategory) -> None:
        """Handle a new PR being added."""
        tree = self._tree_for_category(category)
        self._add_pr_item(tree, pr)
        self._update_tab_labels()
        self._update_status()

    def _on_pr_removed(self, pr: PullRequest, category: PrCategory) -> None:
        """Handle a PR being removed."""
        tree = self._tree_for_category(category)
        self._remove_pr_item(tree, pr)
        self._update_tab_labels()
        self._update_status()

    def _on_pr_updated(self, pr: PullRequest, category: PrCategory) -> None:
        """Handle a PR being updated."""
        tree = self._tree_for_category(category)
        item = self._find_item(tree, pr)
        if item:
            self._update_item_data(item, pr)

    def _tree_for_category(self, category: PrCategory) -> QTreeWidget:
        """Get the tree widget for a given category."""
        if category == PrCategory.REVIEW_REQUESTED:
            return self._review_tree
        return self._authored_tree

    def _add_pr_item(self, tree: QTreeWidget, pr: PullRequest) -> None:
        """Add a PR as a row in the tree widget."""
        item = _SortableTreeWidgetItem()
        self._update_item_data(item, pr)
        tree.addTopLevelItem(item)

    def _update_item_data(self, item: QTreeWidgetItem, pr: PullRequest) -> None:
        """Set/update all columns on a tree item from PR data."""
        # Column 0: status icon (draft vs open)
        if pr.draft:
            item.setText(0, "◌")
            item.setToolTip(0, "Draft")
            item.setForeground(0, QColor("#8b949e"))
            item.setData(0, SORT_ROLE, 1)
        else:
            item.setText(0, "●")
            item.setToolTip(0, "Open")
            item.setForeground(0, QColor("#3fb950"))
            item.setData(0, SORT_ROLE, 0)

        # Column 1: title with PR number
        item.setText(1, f"#{pr.number} {pr.title}")
        title_font = item.font(1)
        title_font.setWeight(QFont.Weight.Medium)
        item.setFont(1, title_font)

        # Column 2: repository
        item.setText(2, pr.repo_full_name)
        item.setForeground(2, QColor("#8b949e"))

        # Column 3: author
        item.setText(3, pr.author or "—")
        item.setForeground(3, QColor("#8b949e"))

        # Column 4: review status
        review_text, review_color, review_sort = _review_display(pr.review_status)
        item.setText(4, review_text)
        item.setForeground(4, QColor(review_color))
        item.setToolTip(4, pr.review_status.value.replace("_", " ").title())
        item.setData(4, SORT_ROLE, review_sort)

        # Column 5: checks status
        checks_text, checks_color, checks_sort = _checks_display(pr.checks_status)
        item.setText(5, checks_text)
        item.setForeground(5, QColor(checks_color))
        item.setToolTip(5, pr.checks_status.value.replace("_", " ").title())
        item.setData(5, SORT_ROLE, checks_sort)

        # Column 6: relative time (with raw timestamp for sorting)
        item.setText(6, _relative_time(pr.updated_at))
        item.setForeground(6, QColor("#8b949e"))
        item.setData(6, SORT_ROLE, int(pr.updated_at.timestamp()))

        # Store PR data for later retrieval
        item.setData(0, Qt.ItemDataRole.UserRole, pr)

    def _remove_pr_item(self, tree: QTreeWidget, pr: PullRequest) -> None:
        """Remove a PR row from the tree widget."""
        item = self._find_item(tree, pr)
        if item:
            index = tree.indexOfTopLevelItem(item)
            if index >= 0:
                tree.takeTopLevelItem(index)

    def _find_item(self, tree: QTreeWidget, pr: PullRequest) -> QTreeWidgetItem | None:
        """Find the tree item for a given PR."""
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item is None:
                continue
            stored_pr = item.data(0, Qt.ItemDataRole.UserRole)
            if stored_pr and stored_pr.repo_full_name == pr.repo_full_name and stored_pr.number == pr.number:
                return item
        return None

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Open the PR in the browser on double-click."""
        pr = item.data(0, Qt.ItemDataRole.UserRole)
        if pr and pr.html_url:
            subprocess.Popen(["xdg-open", pr.html_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603

    def _update_tab_labels(self) -> None:
        """Update tab labels with current counts."""
        review_count = self._review_tree.topLevelItemCount()
        authored_count = self._authored_tree.topLevelItemCount()
        self._tabs.setTabText(0, f"Awaiting Your Review ({review_count})")
        self._tabs.setTabText(1, f"Your Pull Requests ({authored_count})")

    def _update_status(self) -> None:
        """Update the status bar text."""
        review_count = self._review_tree.topLevelItemCount()
        authored_count = self._authored_tree.topLevelItemCount()
        self._status_label.setText(f"{review_count} review requests · {authored_count} authored PRs")

    def _refresh_timestamps(self) -> None:
        """Refresh all relative timestamp displays."""
        for tree in (self._review_tree, self._authored_tree):
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                if item is None:
                    continue
                pr = item.data(0, Qt.ItemDataRole.UserRole)
                if pr:
                    item.setText(6, _relative_time(pr.updated_at))

    def closeEvent(self, event) -> None:  # noqa: N802
        """Hide window instead of closing (tray app behavior)."""
        event.ignore()
        self.hide()


class _SortableTreeWidgetItem(QTreeWidgetItem):
    """TreeWidgetItem that sorts using SORT_ROLE data when available."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        my_data = self.data(column, SORT_ROLE)
        other_data = other.data(column, SORT_ROLE)
        if my_data is not None and other_data is not None:
            return my_data < other_data
        return super().__lt__(other)


def _review_display(status: ReviewStatus) -> tuple[str, str, int]:
    """Get display text, color, and sort value for review status."""
    match status:
        case ReviewStatus.APPROVED:
            return ("✓ Approved", "#3fb950", 0)
        case ReviewStatus.CHANGES_REQUESTED:
            return ("✗ Changes", "#f85149", 3)
        case ReviewStatus.REVIEW_REQUIRED:
            return ("⊘ Required", "#d29922", 2)
        case ReviewStatus.DISMISSED:
            return ("— Dismissed", "#8b949e", 1)
        case ReviewStatus.PENDING:
            return ("◌ Pending", "#8b949e", 1)


def _checks_display(status: ChecksStatus) -> tuple[str, str, int]:
    """Get display text, color, and sort value for checks status."""
    match status:
        case ChecksStatus.PASSING:
            return ("✓ Pass", "#3fb950", 0)
        case ChecksStatus.FAILING:
            return ("✗ Fail", "#f85149", 2)
        case ChecksStatus.PENDING:
            return ("◌ Running", "#d29922", 1)
        case ChecksStatus.NONE:
            return ("—", "#8b949e", 3)


def _relative_time(dt: datetime) -> str:
    """Format a datetime as a relative time string."""
    now = datetime.now(tz=UTC)
    delta = now - dt

    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    days = seconds // 86400
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        months = days // 30
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"
