"""Client for the X API v2 recent-search endpoint.

Searches the most recent 7 days of public posts for references to a
specific US congressional bill, using app-only Bearer Token authentication.
No user OAuth flow is required — this client is strictly read-only.

Environment variables
---------------------
X_BEARER_TOKEN : required
    App-only Bearer Token from developer.twitter.com.
X_MAX_RESULTS : optional
    Posts per search page, 10–100. Defaults to 100.
X_MAX_PAGES : optional
    Pagination depth cap per call (cost control). Defaults to 5.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from .exceptions import XAPIError
from .models import XPost
from .utils import PackageIDParser


class XClient:
    """Client for the X API v2 recent search endpoint.

    Uses app-only Bearer Token authentication. Suitable for reading public
    posts only — no user context or write operations.

    Args:
        bearer_token: X API v2 Bearer Token. Defaults to ``X_BEARER_TOKEN``
            environment variable.
        max_results: Posts per page, 10–100. Defaults to ``X_MAX_RESULTS``
            env var or 100.
        max_pages: Maximum pagination depth per search call. Defaults to
            ``X_MAX_PAGES`` env var or 5. Acts as a cost-control guard.

    Raises:
        XAPIError: If the Bearer Token is missing.
    """

    BASE_URL = "https://api.twitter.com/2"

    _TWEET_FIELDS = (
        "id,text,created_at,author_id,public_metrics,"
        "entities,context_annotations,lang"
    )
    _EXPANSIONS = "author_id"
    _USER_FIELDS = (
        "id,name,username,verified,public_metrics,description"
    )

    def __init__(
        self,
        bearer_token: str | None = None,
        max_results: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        token = bearer_token or os.environ.get("X_BEARER_TOKEN", "")
        if not token:
            raise XAPIError(
                "X Bearer Token is required. Set the X_BEARER_TOKEN "
                "environment variable or pass bearer_token explicitly."
            )

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

        try:
            self._max_results = int(
                max_results
                if max_results is not None
                else os.environ.get("X_MAX_RESULTS", 100)
            )
        except (TypeError, ValueError) as exc:
            raise XAPIError(
                f"X_MAX_RESULTS must be an integer between 10 and 100: {exc}"
            ) from exc

        if not 10 <= self._max_results <= 100:
            raise XAPIError(
                f"max_results must be between 10 and 100, got {self._max_results}."
            )

        try:
            self._max_pages = int(
                max_pages
                if max_pages is not None
                else os.environ.get("X_MAX_PAGES", 5)
            )
        except (TypeError, ValueError) as exc:
            raise XAPIError(
                f"X_MAX_PAGES must be a positive integer: {exc}"
            ) from exc

        if self._max_pages < 1:
            raise XAPIError(
                f"max_pages must be at least 1, got {self._max_pages}."
            )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def search_bill_posts(
        self,
        package_id: str,
        bill_short_title: str | None = None,
        additional_operators: str = "-is:retweet lang:en",
        since_id: str | None = None,
    ) -> list[XPost]:
        """Search recent X posts referencing a bill.

        Constructs a query from bill number variants derived from
        *package_id*, optionally supplemented with *bill_short_title*.
        Paginates up to :attr:`_max_pages` deep and returns a deduplicated
        list of :class:`XPost` objects sorted newest-first.

        Args:
            package_id: GovInfo bill ID (e.g. ``"BILLS-119hr1ih"``).
            bill_short_title: Optional human-readable bill name to include
                in the query (e.g. ``"Big Beautiful Bill"``).
            additional_operators: X query operators appended to every
                query. Defaults to ``"-is:retweet lang:en"``.
            since_id: If provided, only fetch posts newer than this post
                ID. Useful for incremental fetches without re-consuming
                quota.

        Returns:
            List of :class:`XPost` objects, sorted newest-first.

        Raises:
            XAPIError: On authentication failure, rate-limit exhaustion,
                or any non-200 response from the X API.
        """
        query = self._build_query(package_id, bill_short_title, additional_operators)

        all_posts: list[XPost] = []
        seen_ids: set[str] = set()
        next_token: str | None = None
        pages_fetched = 0

        while pages_fetched < self._max_pages:
            params: dict[str, Any] = {
                "query": query,
                "max_results": self._max_results,
                "tweet.fields": self._TWEET_FIELDS,
                "expansions": self._EXPANSIONS,
                "user.fields": self._USER_FIELDS,
            }
            if next_token:
                params["next_token"] = next_token
            if since_id:
                params["since_id"] = since_id

            page_posts, next_token = self._fetch_page(
                query, self._max_results, next_token, since_id=since_id
            )
            pages_fetched += 1

            for post in page_posts:
                if post.post_id not in seen_ids:
                    seen_ids.add(post.post_id)
                    all_posts.append(post)

            if next_token is None:
                break

        # Newest-first (X returns newest first within a page but we preserve order)
        return all_posts

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_query(
        self,
        package_id: str,
        bill_short_title: str | None,
        operators: str,
    ) -> str:


        variants = PackageIDParser.to_x_query_variants(package_id)
        terms = [f'"{v}"' for v in variants]
        if bill_short_title and bill_short_title.strip():
            terms.append(f'"{bill_short_title.strip()}"')

        inner = " OR ".join(terms)
        query = f"({inner}) {operators}".strip()
        return query

    def _fetch_page(
        self,
        query: str,
        max_results: int,
        next_token: str | None = None,
        since_id: str | None = None,
    ) -> tuple[list[XPost], str | None]:
        """Fetch a single page of search results.

        Args:
            query: X query string.
            max_results: Posts per page (10–100).
            next_token: Pagination cursor from a previous response.
            since_id: Lower-bound post ID for incremental fetches.

        Returns:
            Two-tuple ``(posts, next_token)`` where *next_token* is
            ``None`` if there are no further pages.

        Raises:
            XAPIError: On any non-200 HTTP response or missing data.
        """
        url = f"{self.BASE_URL}/tweets/search/recent"
        params: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "tweet.fields": self._TWEET_FIELDS,
            "expansions": self._EXPANSIONS,
            "user.fields": self._USER_FIELDS,
        }
        if next_token:
            params["next_token"] = next_token
        if since_id:
            params["since_id"] = since_id

        try:
            response = self._session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            raise XAPIError(
                f"Network error contacting X API: {exc}"
            ) from exc

        if response.status_code == 401:
            raise XAPIError(
                "X API authentication failed — check your Bearer Token."
            )
        if response.status_code == 429:
            raise XAPIError(
                "X API rate limit exceeded. Wait before retrying or reduce "
                "max_results / max_pages to control quota usage."
            )
        if not response.ok:
            raise XAPIError(
                f"X API returned HTTP {response.status_code}: {response.text}"
            )

        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise XAPIError(
                f"X API returned non-JSON response: {exc}"
            ) from exc

        if "errors" in body and "data" not in body:
            raise XAPIError(f"X API error response: {body['errors']}")

        data: list[dict[str, Any]] = body.get("data", [])
        meta: dict[str, Any] = body.get("meta", {})

        # Build a username-lookup map from the includes block
        users_by_id: dict[str, dict[str, Any]] = {}
        for user in body.get("includes", {}).get("users", []):
            users_by_id[user["id"]] = user

        posts = [self._parse_post(raw, users_by_id) for raw in data]
        returned_next_token: str | None = meta.get("next_token")
        return posts, returned_next_token

    def _parse_post(
        self,
        raw: dict[str, Any],
        users_by_id: dict[str, dict[str, Any]],
    ) -> XPost:
        """Map a raw API response dict to an :class:`XPost`.

        Args:
            raw: Single tweet object from the API ``data`` array.
            users_by_id: Mapping of user ID → user object from
                ``includes.users``.

        Returns:
            Populated :class:`XPost`.

        Raises:
            XAPIError: If mandatory fields are absent from *raw*.
        """
        try:
            post_id: str = raw["id"]
            text: str = raw["text"]
            author_id: str = raw["author_id"]
        except KeyError as exc:
            raise XAPIError(
                f"X API post missing required field {exc}: {raw}"
            ) from exc

        user = users_by_id.get(author_id, {})
        author_username: str = user.get("username", "")
        author_name: str = user.get("name", "")
        author_verified: bool = user.get("verified", False)

        metrics: dict[str, int] = raw.get("public_metrics", {})

        return XPost(
            post_id=post_id,
            text=text,
            author_id=author_id,
            author_username=author_username,
            author_name=author_name,
            author_verified=author_verified,
            created_at=raw.get("created_at", ""),
            like_count=metrics.get("like_count", 0),
            retweet_count=metrics.get("retweet_count", 0),
            reply_count=metrics.get("reply_count", 0),
            quote_count=metrics.get("quote_count", 0),
            url=(
                f"https://x.com/{author_username}/status/{post_id}"
                if author_username
                else f"https://x.com/i/web/status/{post_id}"
            ),
            lang=raw.get("lang", ""),
            context_annotations=raw.get("context_annotations", []),
        )
