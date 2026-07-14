"""GUI settings dialog for gh-notify configuration."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gh_notify.config import Config, FilterSettings, NotificationSettings


class SettingsDialog(QDialog):
    """Settings dialog for configuring gh-notify."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("gh-notify Settings")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self) -> None:
        """Build the dialog UI."""
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._create_general_tab(), "General")
        tabs.addTab(self._create_notifications_tab(), "Notifications")
        tabs.addTab(self._create_filters_tab(), "Filters")
        layout.addWidget(tabs)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_general_tab(self) -> QWidget:
        """Create the General settings tab."""
        widget = QWidget()
        layout = QFormLayout(widget)

        self._poll_interval_spin = QSpinBox()
        self._poll_interval_spin.setMinimum(10)
        self._poll_interval_spin.setMaximum(600)
        self._poll_interval_spin.setSuffix(" seconds")
        layout.addRow("Poll interval:", self._poll_interval_spin)

        self._username_edit = QLineEdit()
        self._username_edit.setPlaceholderText("(auto-detected from gh CLI)")
        layout.addRow("GitHub username:", self._username_edit)

        info_label = QLabel("Leave username empty to auto-detect from gh CLI authentication.")
        info_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", info_label)

        return widget

    def _create_notifications_tab(self) -> QWidget:
        """Create the Notifications settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("Notify on:")
        group_layout = QVBoxLayout(group)

        self._review_check = QCheckBox("Review requested")
        self._mentions_check = QCheckBox("Mentions")
        self._comments_check = QCheckBox("PR comments")
        self._ci_check = QCheckBox("CI status changes")

        group_layout.addWidget(self._review_check)
        group_layout.addWidget(self._mentions_check)
        group_layout.addWidget(self._comments_check)
        group_layout.addWidget(self._ci_check)

        layout.addWidget(group)
        layout.addStretch()

        return widget

    def _create_filters_tab(self) -> QWidget:
        """Create the Filters settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Excluded repos
        repos_group = QGroupBox("Excluded Repositories (exact match, e.g. owner/repo)")
        repos_layout = QVBoxLayout(repos_group)
        self._repos_list = QListWidget()
        repos_layout.addWidget(self._repos_list)
        repos_layout.addLayout(self._create_list_controls(self._repos_list))
        layout.addWidget(repos_group)

        # Excluded title patterns
        titles_group = QGroupBox("Excluded Title Patterns (regex)")
        titles_layout = QVBoxLayout(titles_group)
        self._titles_list = QListWidget()
        titles_layout.addWidget(self._titles_list)
        titles_layout.addLayout(self._create_list_controls(self._titles_list))
        layout.addWidget(titles_group)

        # Excluded authors
        authors_group = QGroupBox("Excluded Authors (exact match)")
        authors_layout = QVBoxLayout(authors_group)
        self._authors_list = QListWidget()
        authors_layout.addWidget(self._authors_list)
        authors_layout.addLayout(self._create_list_controls(self._authors_list))
        layout.addWidget(authors_group)

        return widget

    def _create_list_controls(self, list_widget: QListWidget) -> QHBoxLayout:
        """Create Add/Remove buttons for a list widget."""
        layout = QHBoxLayout()

        line_edit = QLineEdit()
        line_edit.setPlaceholderText("Enter value...")
        layout.addWidget(line_edit)

        add_btn = QPushButton("Add")
        remove_btn = QPushButton("Remove")

        def add_item():
            text = line_edit.text().strip()
            if text:
                list_widget.addItem(text)
                line_edit.clear()

        def remove_item():
            current = list_widget.currentRow()
            if current >= 0:
                list_widget.takeItem(current)

        add_btn.clicked.connect(add_item)
        remove_btn.clicked.connect(remove_item)
        line_edit.returnPressed.connect(add_item)

        layout.addWidget(add_btn)
        layout.addWidget(remove_btn)

        return layout

    def _load_config(self) -> None:
        """Load current config values into the UI."""
        self._poll_interval_spin.setValue(self._config.poll_interval_seconds)
        self._username_edit.setText(self._config.username)

        self._review_check.setChecked(self._config.notifications.review_requested)
        self._mentions_check.setChecked(self._config.notifications.mentions)
        self._comments_check.setChecked(self._config.notifications.pr_comments)
        self._ci_check.setChecked(self._config.notifications.ci_status)

        for repo in self._config.filters.exclude_repos:
            self._repos_list.addItem(repo)
        for pattern in self._config.filters.exclude_title_patterns:
            self._titles_list.addItem(pattern)
        for author in self._config.filters.exclude_authors:
            self._authors_list.addItem(author)

    def get_config(self) -> Config:
        """Build a Config from the current UI state."""
        notifications = NotificationSettings(
            review_requested=self._review_check.isChecked(),
            mentions=self._mentions_check.isChecked(),
            pr_comments=self._comments_check.isChecked(),
            ci_status=self._ci_check.isChecked(),
        )

        filters = FilterSettings(
            exclude_repos=self._get_list_items(self._repos_list),
            exclude_title_patterns=self._get_list_items(self._titles_list),
            exclude_authors=self._get_list_items(self._authors_list),
        )

        return Config(
            poll_interval_seconds=self._poll_interval_spin.value(),
            username=self._username_edit.text().strip(),
            notifications=notifications,
            filters=filters,
        )

    def _get_list_items(self, list_widget: QListWidget) -> list[str]:
        """Extract all items from a QListWidget."""
        items = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item:
                items.append(item.text())
        return items
