"""Configuration loading and saving for gh-notify."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

CONFIG_DIR = Path.home() / ".config" / "gh-notify"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class NotificationSettings:
    """Which notification types to show."""

    review_requested: bool = True
    mentions: bool = True
    pr_comments: bool = True
    ci_status: bool = False


@dataclass
class FilterSettings:
    """Filters to exclude certain PRs from notifications."""

    exclude_repos: list[str] = field(default_factory=list)
    exclude_title_patterns: list[str] = field(default_factory=list)
    exclude_authors: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Application configuration."""

    poll_interval_seconds: int = 60
    username: str = ""
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    filters: FilterSettings = field(default_factory=FilterSettings)

    @classmethod
    def load(cls) -> Self:
        """Load config from TOML file, creating defaults if missing."""
        if not CONFIG_FILE.exists():
            config = cls()
            config.save()
            return config

        with CONFIG_FILE.open("rb") as f:
            data = tomllib.load(f)

        general = data.get("general", {})
        notif_data = data.get("notifications", {})
        filter_data = data.get("filters", {})

        notifications = NotificationSettings(
            review_requested=notif_data.get("review_requested", True),
            mentions=notif_data.get("mentions", True),
            pr_comments=notif_data.get("pr_comments", True),
            ci_status=notif_data.get("ci_status", False),
        )

        filters = FilterSettings(
            exclude_repos=filter_data.get("exclude_repos", []),
            exclude_title_patterns=filter_data.get("exclude_title_patterns", []),
            exclude_authors=filter_data.get("exclude_authors", []),
        )

        return cls(
            poll_interval_seconds=general.get("poll_interval_seconds", 60),
            username=general.get("username", ""),
            notifications=notifications,
            filters=filters,
        )

    def save(self) -> None:
        """Save config to TOML file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        lines = [
            "[general]",
            f"poll_interval_seconds = {self.poll_interval_seconds}",
            f'username = "{self.username}"',
            "",
            "[notifications]",
            f"review_requested = {_bool_str(self.notifications.review_requested)}",
            f"mentions = {_bool_str(self.notifications.mentions)}",
            f"pr_comments = {_bool_str(self.notifications.pr_comments)}",
            f"ci_status = {_bool_str(self.notifications.ci_status)}",
            "",
            "[filters]",
            f"exclude_repos = {_list_str(self.filters.exclude_repos)}",
            f"exclude_title_patterns = {_list_str(self.filters.exclude_title_patterns)}",
            f"exclude_authors = {_list_str(self.filters.exclude_authors)}",
            "",
        ]

        CONFIG_FILE.write_text("\n".join(lines))


def _bool_str(value: bool) -> str:
    """Convert bool to TOML-compatible string."""
    return "true" if value else "false"


def _list_str(items: list[str]) -> str:
    """Convert list to TOML-compatible array string."""
    if not items:
        return "[]"
    escaped = [f'"{item}"' for item in items]
    return f"[{', '.join(escaped)}]"
