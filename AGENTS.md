# AGENTS.md

## Project Overview

gh-notify is a KDE Plasma desktop application that monitors GitHub pull requests and sends desktop notifications. It runs as a system tray app with a main window listing PRs.

## Tech Stack

- **Language:** Python 3.12+
- **GUI:** PyQt6 (system tray via QSystemTrayIcon/SNI, main window, settings dialog)
- **HTTP:** httpx (synchronous client, used from worker thread)
- **Notifications:** desktop-notifier (org.freedesktop.Notifications D-Bus)
- **Auth:** `gh` CLI (`gh auth token` subprocess call)
- **API:** GitHub GraphQL (PR data with review/checks status) + REST (notifications endpoint)
- **Config:** TOML (~/.config/gh-notify/config.toml), stdlib tomllib for reading
- **Build:** hatchling, managed with uv
- **Linting:** ruff (line-length=160)

## Architecture

```
__main__.py          Entry point, signal handling
app.py               QApplication, tray icon, animation, orchestration
main_window.py       QMainWindow with tabbed QTreeWidget PR list
settings_dialog.py   QDialog with tabs for general/notifications/filters
poller.py            QThread worker + QTimer polling orchestrator
pr_store.py          PR state tracker, emits granular change signals
github_client.py     GitHub API client (GraphQL + REST), retry, rate limit
notifier.py          desktop-notifier wrapper on a dedicated asyncio thread
config.py            TOML config dataclasses, load/save
models.py            PullRequest, NotificationEvent, enums
icons/               SVG icons (normal, attention, 4 poll animation frames)
```

## Threading Model

- **Main thread:** Qt event loop, UI rendering, signal/slot handling
- **Poller worker thread:** All HTTP calls and data parsing (QThread + QObject.moveToThread)
- **Notifier thread:** Dedicated asyncio event loop for async desktop-notifier sends

No HTTP calls or subprocess calls happen on the main thread. The poller emits results via Qt signals (queued cross-thread connections) which the main thread processes instantly.

## Key Design Decisions

- **Incremental updates:** Each GraphQL page emits results immediately via page signals. The PrStore adds/updates rows as pages arrive. Removals only happen when the full fetch completes.
- **No full list rebuilds:** PrStore diffs new data against previous state and emits pr_added/pr_removed/pr_updated for individual items.
- **GraphQL fallback:** If `statusCheckRollup` causes resource limit errors, falls back to a lightweight query without checks.
- **Rate limit awareness:** Tracks X-RateLimit headers, preemptively stops when remaining ≤ 5, shows quota in status bar.
- **Retry logic:** 3 retries with exponential backoff for 500/502/503/504, network errors, and transient GraphQL errors.
- **Single instance:** QLocalServer lock prevents multiple copies.

## Development

```bash
# Install in dev mode
uv pip install -e .

# Run
uv run gh-notify

# Lint
uv run --with ruff ruff check src/
uv run --with ruff ruff format --line-length 160 src/
```

## Conventions

- Never run `python` directly — use `uv run`
- Ruff for all linting/formatting (line-length 160)
- PyQt6 signals/slots for all cross-thread communication
- No blocking calls on the main thread
- Frozen dataclasses for models (PullRequest, NotificationEvent)
- Config changes go through the settings dialog → Config.save() → Poller.update_config()
- Context menu capped at 5 items per section with overflow submenu
- Status bar shows live progress during polling, idle summary otherwise

## Files Not to Modify Without Understanding

- `poller.py` — Threading is delicate. The `_do_poll` signal triggers `poll()` on the worker thread. Don't call worker methods directly from main thread.
- `github_client.py` — Retry logic, rate limit tracking, and GraphQL fallback interact. Changes to `_request()` affect all API calls.
- `pr_store.py` — Incremental vs full update semantics matter. Page updates only add/update; full updates also remove.
