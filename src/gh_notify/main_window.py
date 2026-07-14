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

from gh_notify.models import PullRequest
from gh_notify.pr_store import PrCategory, PrStore


class MainWindow(QMainWindow):
    """Main window displaying PR lists in a GitHub-style layout."""

    def __init__(self, store: PrStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("gh-notify — Pull Requests")
        self.setMinimumSize(900, 600)

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
        tree.setHeaderLabels(["", "Title", "Repository", "Author", "Updated"])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Column widths
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # Status icon
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Title
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)  # Repo
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)  # Author
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Updated
        tree.setColumnWidth(0, 30)
        tree.setColumnWidth(2, 200)
        tree.setColumnWidth(3, 130)
        tree.setColumnWidth(4, 120)

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
        item = QTreeWidgetItem()
        self._update_item_data(item, pr)
        tree.addTopLevelItem(item)

    def _update_item_data(self, item: QTreeWidgetItem, pr: PullRequest) -> None:
        """Set/update all columns on a tree item from PR data."""
        # Column 0: status icon (draft vs open)
        if pr.draft:
            item.setText(0, "◌")
            item.setToolTip(0, "Draft")
            item.setForeground(0, QColor("#8b949e"))
        else:
            item.setText(0, "●")
            item.setToolTip(0, "Open")
            item.setForeground(0, QColor("#3fb950"))

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

        # Column 4: relative time
        item.setText(4, _relative_time(pr.updated_at))
        item.setForeground(4, QColor("#8b949e"))

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
                    item.setText(4, _relative_time(pr.updated_at))

    def closeEvent(self, event) -> None:  # noqa: N802
        """Hide window instead of closing (tray app behavior)."""
        event.ignore()
        self.hide()


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
