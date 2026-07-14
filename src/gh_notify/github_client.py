"""GitHub API client using httpx and gh CLI authentication."""

from __future__ import annotations

import contextlib
import logging
import subprocess
from datetime import UTC, datetime
from typing import Any

import httpx

from gh_notify.models import ChecksStatus, NotificationEvent, NotificationType, PullRequest, ReviewStatus

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubClientError(Exception):
    """Raised when GitHub API requests fail."""


class RateLimitError(GitHubClientError):
    """Raised when GitHub API rate limit is exceeded."""

    def __init__(self, reset_at: int, remaining: int = 0) -> None:
        self.reset_at = reset_at
        self.remaining = remaining
        super().__init__(f"Rate limited. Resets at {reset_at}")


class GitHubClient:
    """GitHub API client using gh CLI token with rate limit tracking and retry."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._client: httpx.Client | None = None
        self._last_modified: str | None = None
        self._poll_interval: int = 60
        self._username: str | None = None
        # Rate limit state
        self._rate_remaining: int | None = None
        self._rate_limit: int | None = None
        self._rate_reset: int | None = None  # Unix timestamp

    @property
    def poll_interval(self) -> int:
        """Server-recommended poll interval in seconds."""
        return self._poll_interval

    @property
    def username(self) -> str:
        """Authenticated GitHub username."""
        if self._username is None:
            self._username = self._fetch_username()
        return self._username

    @property
    def rate_remaining(self) -> int | None:
        """Remaining API calls before rate limit."""
        return self._rate_remaining

    @property
    def rate_limit(self) -> int | None:
        """Total rate limit quota."""
        return self._rate_limit

    @property
    def rate_reset(self) -> int | None:
        """Unix timestamp when rate limit resets."""
        return self._rate_reset

    def _get_token(self) -> str:
        """Get auth token from gh CLI."""
        if self._token is None:
            try:
                result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                self._token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                msg = "Failed to get GitHub token from gh CLI. Ensure gh is installed and authenticated."
                raise GitHubClientError(msg) from e
        return self._token

    def _get_client(self) -> httpx.Client:
        """Get or create the httpx client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=GITHUB_API_BASE,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._get_token()}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
            )
        return self._client

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make an authenticated request to the GitHub API.

        Handles:
        - Rate limit tracking (X-RateLimit-* headers)
        - Rate limit enforcement (raises RateLimitError before calling if exhausted)
        - Retry with backoff on transient errors (500, 502, 503, 504, network errors)
        - Secondary rate limit detection (403 with Retry-After or abuse message)
        - Token refresh on 401
        """
        import time

        # Check if we're rate limited before making the call
        if self._rate_remaining is not None and self._rate_remaining <= 5:
            now = int(time.time())
            if self._rate_reset and now < self._rate_reset:
                raise RateLimitError(self._rate_reset, self._rate_remaining)

        client = self._get_client()
        max_retries = 3
        retryable_statuses = {500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = client.request(method, path, **kwargs)
                self._update_rate_limit(response)

                # Primary rate limit (403 with remaining=0)
                if response.status_code == 403 and self._rate_remaining is not None and self._rate_remaining == 0:
                    raise RateLimitError(self._rate_reset or 0, 0)

                # Secondary rate limit (403 with Retry-After or abuse detection)
                if response.status_code == 403:
                    body = response.text.lower()
                    if "retry-after" in response.headers:
                        retry_after = int(response.headers.get("Retry-After", "60"))
                        reset_at = int(time.time()) + retry_after
                        raise RateLimitError(reset_at, 0)
                    if "abuse" in body or "rate limit" in body or "secondary" in body:
                        # Secondary rate limit without Retry-After — back off 60s
                        raise RateLimitError(int(time.time()) + 60, 0)

                # Retry on transient server errors
                if response.status_code in retryable_statuses:
                    if attempt < max_retries:
                        sleep_time = 2**attempt  # 1s, 2s, 4s
                        logger.warning(
                            "GitHub API %d on %s %s, retrying in %ds (attempt %d/%d)", response.status_code, method, path, sleep_time, attempt + 1, max_retries
                        )
                        time.sleep(sleep_time)
                        continue
                    # Exhausted retries
                    raise GitHubClientError(f"GitHub API error: {response.status_code} after {max_retries + 1} attempts on {method} {path}")

                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401 and attempt == 0:
                    # Token might be stale, clear it and retry once
                    self._token = None
                    self._client = None
                    client = self._get_client()
                    continue
                raise GitHubClientError(f"GitHub API error: {e.response.status_code} on {method} {path}") from e
            except httpx.RequestError as e:
                last_error = e
                if attempt < max_retries:
                    sleep_time = 2**attempt
                    logger.warning(
                        "GitHub API network error on %s %s: %s, retrying in %ds (attempt %d/%d)", method, path, e, sleep_time, attempt + 1, max_retries
                    )
                    time.sleep(sleep_time)
                    continue
                raise GitHubClientError(f"GitHub API unreachable after {max_retries + 1} attempts: {e}") from e

        msg = f"GitHub API request failed after {max_retries + 1} attempts: {last_error}"
        raise GitHubClientError(msg)

    def _update_rate_limit(self, response: httpx.Response) -> None:
        """Update rate limit state from response headers."""
        if "x-ratelimit-remaining" in response.headers:
            with contextlib.suppress(ValueError):
                self._rate_remaining = int(response.headers["x-ratelimit-remaining"])
        if "x-ratelimit-limit" in response.headers:
            with contextlib.suppress(ValueError):
                self._rate_limit = int(response.headers["x-ratelimit-limit"])
        if "x-ratelimit-reset" in response.headers:
            with contextlib.suppress(ValueError):
                self._rate_reset = int(response.headers["x-ratelimit-reset"])

    def _fetch_username(self) -> str:
        """Fetch the authenticated user's username."""
        response = self._request("GET", "/user")
        return response.json()["login"]

    def fetch_notifications(self) -> list[dict[str, Any]]:
        """Fetch recent notification threads, respecting If-Modified-Since.

        Returns empty list if nothing changed (304).
        Fetches up to 2 pages (100 notifications) — older ones are not actionable
        for desktop notifications and would waste API quota.
        """
        headers: dict[str, str] = {}
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified

        client = self._get_client()
        all_notifications: list[dict[str, Any]] = []
        page = 1
        per_page = 50
        max_pages = 2  # Cap at 100 notifications — beyond this is historical noise

        while page <= max_pages:
            try:
                response = client.get("/notifications", headers=headers, params={"all": "false", "per_page": per_page, "page": page})
            except httpx.RequestError as e:
                raise GitHubClientError(f"GitHub API request failed: {e}") from e

            # Update poll interval from server recommendation (from first response)
            if page == 1 and "X-Poll-Interval" in response.headers:
                with contextlib.suppress(ValueError):
                    self._poll_interval = int(response.headers["X-Poll-Interval"])

            if response.status_code == 304:
                return []

            response.raise_for_status()

            if page == 1 and "Last-Modified" in response.headers:
                self._last_modified = response.headers["Last-Modified"]

            items = response.json()
            if not items:
                break

            all_notifications.extend(items)

            # Stop if we got fewer items than requested (last page)
            if len(items) < per_page:
                break
            page += 1

        return all_notifications

    def fetch_review_requested_prs(self, username: str) -> list[PullRequest]:
        """Fetch open PRs where review is pending from the user.

        Uses GraphQL to get review decision and check status in one query.
        """
        query = f"is:pr is:open review-requested:{username} -reviewed-by:{username}"
        return self._graphql_search_prs(query)

    def fetch_authored_prs(self, username: str) -> list[PullRequest]:
        """Fetch all open PRs authored by the user with review/check status."""
        query = f"is:pr is:open author:{username}"
        return self._graphql_search_prs(query)

    def _graphql_search_prs(self, search_query: str) -> list[PullRequest]:
        """Fetch PRs via GraphQL search, including reviewDecision and statusCheckRollup.

        Uses a page size of 25 to stay within GitHub's resource budget.
        Falls back to a lightweight query (no checks) if resource limits are hit.
        """
        try:
            return self._graphql_search_prs_full(search_query)
        except GitHubClientError as e:
            if "resource limit" in str(e).lower():
                logger.warning("GraphQL resource limit hit, falling back to lightweight query")
                return self._graphql_search_prs_lightweight(search_query)
            raise

    def _graphql_search_prs_full(self, search_query: str) -> list[PullRequest]:
        """Full GraphQL search with review decision and checks status."""
        results: list[PullRequest] = []
        cursor: str | None = None

        while True:
            after_clause = f', after: "{cursor}"' if cursor else ""
            gql = f"""
            {{
              search(query: "{search_query}", type: ISSUE, first: 25{after_clause}) {{
                pageInfo {{
                  hasNextPage
                  endCursor
                }}
                nodes {{
                  ... on PullRequest {{
                    number
                    title
                    url
                    isDraft
                    updatedAt
                    author {{
                      login
                    }}
                    repository {{
                      nameWithOwner
                    }}
                    reviewDecision
                    commits(last: 1) {{
                      nodes {{
                        commit {{
                          statusCheckRollup {{
                            state
                          }}
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
            """

            response = self._request("POST", "/graphql", json={"query": gql})
            data = response.json()

            if "errors" in data:
                error_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                raise GitHubClientError(f"GraphQL error: {error_msg}")

            search_data = data.get("data", {}).get("search", {})
            nodes = search_data.get("nodes", [])

            for node in nodes:
                if not node:
                    continue
                results.append(self._parse_graphql_pr(node))

            page_info = search_data.get("pageInfo", {})
            if not page_info.get("hasNextPage", False):
                break
            cursor = page_info.get("endCursor")

        return results

    def _graphql_search_prs_lightweight(self, search_query: str) -> list[PullRequest]:
        """Lightweight GraphQL search without checks (for when resource limits are hit)."""
        results: list[PullRequest] = []
        cursor: str | None = None

        while True:
            after_clause = f', after: "{cursor}"' if cursor else ""
            gql = f"""
            {{
              search(query: "{search_query}", type: ISSUE, first: 50{after_clause}) {{
                pageInfo {{
                  hasNextPage
                  endCursor
                }}
                nodes {{
                  ... on PullRequest {{
                    number
                    title
                    url
                    isDraft
                    updatedAt
                    author {{
                      login
                    }}
                    repository {{
                      nameWithOwner
                    }}
                    reviewDecision
                  }}
                }}
              }}
            }}
            """

            response = self._request("POST", "/graphql", json={"query": gql})
            data = response.json()

            if "errors" in data:
                error_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                raise GitHubClientError(f"GraphQL error: {error_msg}")

            search_data = data.get("data", {}).get("search", {})
            nodes = search_data.get("nodes", [])

            for node in nodes:
                if not node:
                    continue
                results.append(self._parse_graphql_pr_lightweight(node))

            page_info = search_data.get("pageInfo", {})
            if not page_info.get("hasNextPage", False):
                break
            cursor = page_info.get("endCursor")

        return results

    def _parse_graphql_pr_lightweight(self, node: dict[str, Any]) -> PullRequest:
        """Parse a GraphQL PR node without checks status."""
        review_decision = node.get("reviewDecision") or ""
        review_status = _map_review_decision(review_decision)
        repo = node.get("repository", {}).get("nameWithOwner", "")
        number = node.get("number", 0)

        return PullRequest(
            number=number,
            title=node.get("title", ""),
            repo_full_name=repo,
            author=node.get("author", {}).get("login", "") if node.get("author") else "",
            url=node.get("url", ""),
            html_url=node.get("url", ""),
            updated_at=_parse_datetime(node.get("updatedAt", "")),
            draft=node.get("isDraft", False),
            review_status=review_status,
            checks_status=ChecksStatus.NONE,
        )

    def _parse_graphql_pr(self, node: dict[str, Any]) -> PullRequest:
        """Parse a GraphQL PR node into a PullRequest."""
        # Map reviewDecision
        review_decision = node.get("reviewDecision") or ""
        review_status = _map_review_decision(review_decision)

        # Map statusCheckRollup
        commits = node.get("commits", {}).get("nodes", [])
        checks_status = ChecksStatus.NONE
        if commits:
            rollup = commits[0].get("commit", {}).get("statusCheckRollup")
            if rollup:
                checks_status = _map_checks_state(rollup.get("state", ""))

        repo = node.get("repository", {}).get("nameWithOwner", "")
        number = node.get("number", 0)

        return PullRequest(
            number=number,
            title=node.get("title", ""),
            repo_full_name=repo,
            author=node.get("author", {}).get("login", "") if node.get("author") else "",
            url=node.get("url", ""),
            html_url=node.get("url", ""),  # GraphQL url field is the HTML URL
            updated_at=_parse_datetime(node.get("updatedAt", "")),
            draft=node.get("isDraft", False),
            review_status=review_status,
            checks_status=checks_status,
        )

    def _search_all_prs(self, query: str) -> list[PullRequest]:
        """Fetch all pages of search results for a PR query (REST fallback)."""
        results: list[PullRequest] = []
        page = 1
        per_page = 100  # GitHub search API max

        while True:
            response = self._request("GET", "/search/issues", params={"q": query, "per_page": per_page, "page": page})
            data = response.json()
            items = data.get("items", [])
            results.extend(self._parse_search_result(item) for item in items)

            # Stop if we got fewer items than requested (last page) or hit 1000 result cap
            if len(items) < per_page or len(results) >= data.get("total_count", 0):
                break
            page += 1

        return results

    def _parse_search_result(self, item: dict[str, Any]) -> PullRequest:
        """Parse a search result item into a PullRequest."""
        # Extract repo full name from repository_url
        repo_url = item.get("repository_url", "")
        repo_full_name = "/".join(repo_url.rstrip("/").split("/")[-2:]) if repo_url else ""

        return PullRequest(
            number=item["number"],
            title=item["title"],
            repo_full_name=repo_full_name,
            author=item.get("user", {}).get("login", ""),
            url=item.get("url", ""),
            html_url=item.get("html_url", ""),
            updated_at=_parse_datetime(item.get("updated_at", "")),
            draft=item.get("draft", False),
        )

    def parse_notification_to_event(self, notification: dict[str, Any]) -> NotificationEvent | None:
        """Convert a raw notification dict to a NotificationEvent.

        Returns None if the notification is not PR-related.
        """
        subject = notification.get("subject", {})
        subject_type = subject.get("type", "")

        if subject_type != "PullRequest":
            return None

        reason = notification.get("reason", "")
        notification_type = _map_reason_to_type(reason)
        if notification_type is None:
            return None

        # Build a minimal PR from notification data
        repo = notification.get("repository", {})
        repo_full_name = repo.get("full_name", "")
        subject_url = subject.get("url", "")

        # Extract PR number from URL (e.g., .../pulls/123)
        pr_number = 0
        if subject_url:
            parts = subject_url.rstrip("/").split("/")
            with contextlib.suppress(ValueError, IndexError):
                pr_number = int(parts[-1])

        html_url = f"https://github.com/{repo_full_name}/pull/{pr_number}" if repo_full_name and pr_number else ""

        pr = PullRequest(
            number=pr_number,
            title=subject.get("title", ""),
            repo_full_name=repo_full_name,
            author="",  # Not available in notification payload
            url=subject_url,
            html_url=html_url,
            updated_at=_parse_datetime(notification.get("updated_at", "")),
        )

        return NotificationEvent(
            id=notification.get("id", ""),
            notification_type=notification_type,
            title=subject.get("title", ""),
            body=f"{notification_type.value} in {pr.display_name}",
            pr=pr,
            timestamp=_parse_datetime(notification.get("updated_at", "")),
        )

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def _map_reason_to_type(reason: str) -> NotificationType | None:
    """Map GitHub notification reason to our notification type."""
    match reason:
        case "review_requested":
            return NotificationType.REVIEW_REQUESTED
        case "mention":
            return NotificationType.MENTION
        case "comment" | "subscribed":
            return NotificationType.COMMENT
        case "ci_activity":
            return NotificationType.CI_STATUS
        case _:
            return None


def _parse_datetime(dt_str: str) -> datetime:
    """Parse ISO 8601 datetime string from GitHub API."""
    if not dt_str:
        return datetime.now(tz=UTC)
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=UTC)


def _map_review_decision(decision: str) -> ReviewStatus:
    """Map GraphQL reviewDecision to ReviewStatus enum."""
    match decision:
        case "APPROVED":
            return ReviewStatus.APPROVED
        case "CHANGES_REQUESTED":
            return ReviewStatus.CHANGES_REQUESTED
        case "REVIEW_REQUIRED":
            return ReviewStatus.REVIEW_REQUIRED
        case "DISMISSED":
            return ReviewStatus.DISMISSED
        case _:
            return ReviewStatus.PENDING


def _map_checks_state(state: str) -> ChecksStatus:
    """Map GraphQL StatusCheckRollup state to ChecksStatus enum."""
    match state:
        case "SUCCESS":
            return ChecksStatus.PASSING
        case "FAILURE" | "ERROR":
            return ChecksStatus.FAILING
        case "PENDING" | "EXPECTED":
            return ChecksStatus.PENDING
        case _:
            return ChecksStatus.NONE
