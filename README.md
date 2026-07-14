# gh-notify

GitHub PR monitor for KDE Plasma with system tray and desktop notifications.

## Features

- System tray icon with context menu showing PRs needing attention
- Desktop notifications for new review requests, mentions, and PR activity
- Configurable filters to exclude repos, PR title patterns, or submitters
- GUI settings window for easy configuration
- Uses `gh` CLI authentication — no token management needed
- Efficient polling with GitHub API's `If-Modified-Since` / `X-Poll-Interval`

## Requirements

- Python 3.12+
- KDE Plasma desktop (or any desktop supporting StatusNotifierItem)
- `gh` CLI installed and authenticated (`gh auth login`)

## Installation

```bash
uv pip install -e .
```

## Usage

```bash
gh-notify
```

The app starts in the system tray. Right-click for the context menu, or left-click to see PRs needing your attention.

## Configuration

Configuration is stored in `~/.config/gh-notify/config.toml`. You can edit it directly or use the GUI settings window (right-click tray → Settings).

```toml
[general]
poll_interval_seconds = 60
username = ""  # auto-detected from gh CLI if empty

[notifications]
review_requested = true
mentions = true
pr_comments = true
ci_status = false

[filters]
# Exclude specific repos (exact match)
exclude_repos = []
# Exclude PRs with titles matching these patterns (regex)
exclude_title_patterns = []
# Exclude PRs from these users (exact match)
exclude_authors = []
```

## Autostart

Copy the desktop file to autostart:

```bash
cp gh-notify.desktop ~/.config/autostart/
```

## License

MIT
