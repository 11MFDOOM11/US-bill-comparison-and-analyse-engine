"""ProPublica Congress API client for fetching politician statements."""

import os
import re
import time
from typing import Any

import requests

from .exceptions import ProPublicaAPIError
from .models import PoliticianStatement


class ProPublicaClient:
    """Client for the ProPublica Congress API.

    Fetches congressional member statements that are used as source material
    for the comparison engine.  ProPublica provides statement metadata and a
    URL to each statement; full text is fetched separately from the source URL.

    Args:
        api_key: ProPublica API key.  Defaults to the ``PROPUBLICA_API_KEY``
            environment variable.

    Raises:
        ProPublicaAPIError: If no API key is found or a request fails.

    Note:
        Authentication uses the ``X-API-Key`` request header — not a query
        parameter.  Rate limit is 5,000 requests per day.
    """

    BASE_URL = "https://api.propublica.org/congress/v1"
    MAX_RETRIES = 4
    RETRY_STATUSES = {429, 503}

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("PROPUBLICA_API_KEY")
        if not self._api_key:
            raise ProPublicaAPIError(
                "ProPublica API key not found. "
                "Set the PROPUBLICA_API_KEY environment variable. "
                "Request access at https://www.propublica.org/datastore/api"
            )
        self._session = requests.Session()
        self._session.headers.update({
            "X-API-Key": self._api_key,
            "Accept": "application/json",
        })
        # Cache full-text fetches to stay within the daily rate limit.
        self._text_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def search_statements(
        self,
        query: str,
        offset: int = 0,
    ) -> list[PoliticianStatement]:
        """Search member statements by keyword.

        Calls ``GET /statements/search.json?query={query}``.  Returns
        statement metadata including a URL from which full text can be
        fetched separately via :meth:`fetch_full_text`.

        Args:
            query: Keyword or phrase, e.g. a bill name, number, or topic.
            offset: Pagination offset (multiples of 20).

        Returns:
            List of :class:`PoliticianStatement` with metadata populated;
            ``full_text`` is empty until :meth:`fetch_full_text` is called.

        Raises:
            ProPublicaAPIError: If the API call fails.
        """
        url = f"{self.BASE_URL}/statements/search.json"
        params: dict[str, Any] = {"query": query}
        if offset:
            params["offset"] = offset

        response = self._request_with_retry("GET", url, params=params)
        if response.status_code != 200:
            raise ProPublicaAPIError(
                f"Statement search failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )

        data: dict[str, Any] = response.json()
        results: list[dict[str, Any]] = (
            data.get("results", [{}])[0].get("statements", [])
            if data.get("results")
            else []
        )
        return [self._parse_statement(r) for r in results]

    def get_statements_for_bill(
        self,
        congress: str,
        bill_slug: str,
    ) -> list[PoliticianStatement]:
        """Fetch statements specifically tagged to a bill.

        Calls ``GET /{congress}/bills/{bill_slug}/statements.json``.

        Args:
            congress: Congress number, e.g. ``"118"``.
            bill_slug: ProPublica bill slug in the format ``hr1234-118``.

        Returns:
            List of :class:`PoliticianStatement` with metadata populated.

        Raises:
            ProPublicaAPIError: If the API call fails.
        """
        url = f"{self.BASE_URL}/{congress}/bills/{bill_slug}/statements.json"
        response = self._request_with_retry("GET", url)

        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise ProPublicaAPIError(
                f"Failed to fetch statements for bill {bill_slug!r}: "
                f"HTTP {response.status_code}"
            )

        data: dict[str, Any] = response.json()
        results: list[dict[str, Any]] = (
            data.get("results", [{}])[0].get("statements", [])
            if data.get("results")
            else []
        )
        return [self._parse_statement(r) for r in results]

    def get_member_statements(
        self,
        bioguide_id: str,
        congress: str,
    ) -> list[PoliticianStatement]:
        """Fetch all statements by a specific member in a congress.

        Calls ``GET /members/{bioguide_id}/statements/{congress}.json``.

        Args:
            bioguide_id: The member's Bioguide / ProPublica ID.
            congress: Congress number, e.g. ``"118"``.

        Returns:
            List of :class:`PoliticianStatement` with metadata populated.

        Raises:
            ProPublicaAPIError: If the API call fails.
        """
        url = f"{self.BASE_URL}/members/{bioguide_id}/statements/{congress}.json"
        response = self._request_with_retry("GET", url)

        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise ProPublicaAPIError(
                f"Failed to fetch statements for member {bioguide_id!r}: "
                f"HTTP {response.status_code}"
            )

        data: dict[str, Any] = response.json()
        results: list[dict[str, Any]] = data.get("results", [])
        return [self._parse_statement(r) for r in results]

    def fetch_full_text(self, statement: PoliticianStatement) -> str:
        """Fetch and return the full text of a statement from its source URL.

        Responses are cached in memory to avoid redundant fetches against the
        daily rate limit.  If the fetch fails for any reason the method
        returns an empty string rather than raising, so the comparison engine
        can skip the statement gracefully.

        Args:
            statement: A :class:`PoliticianStatement` whose ``url`` field
                points to the member's official website.

        Returns:
            Plain text of the statement, or ``""`` on failure.
        """
        if not statement.url:
            return ""

        if statement.url in self._text_cache:
            return self._text_cache[statement.url]

        try:
            response = self._session.get(
                statement.url,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; BillAnalyzer/1.0; "
                        "+https://github.com/anthropics)"
                    )
                },
            )
            if response.status_code != 200:
                self._text_cache[statement.url] = ""
                return ""
            text = self._strip_html(response.text)
        except requests.RequestException:
            self._text_cache[statement.url] = ""
            return ""

        self._text_cache[statement.url] = text
        return text

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_statement(data: dict[str, Any]) -> PoliticianStatement:
        """Convert a ProPublica API result dict to a :class:`PoliticianStatement`.

        Args:
            data: A single statement object from the ProPublica API response.

        Returns:
            Populated :class:`PoliticianStatement` (``full_text`` is empty).
        """
        subjects_raw = data.get("subjects", [])
        if isinstance(subjects_raw, str):
            subjects = [s.strip() for s in subjects_raw.split(",") if s.strip()]
        else:
            subjects = list(subjects_raw)

        return PoliticianStatement(
            member_id=data.get("member_id", data.get("bioguide_id", "")),
            member_name=data.get("name", data.get("member_name", "")),
            party=data.get("party", ""),
            state=data.get("state", ""),
            chamber=data.get("chamber", ""),
            date=data.get("date", ""),
            title=data.get("title", ""),
            url=data.get("url", ""),
            subjects=subjects,
            full_text="",
        )

    def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Perform an HTTP request, retrying on 429/503 with exponential back-off.

        Args:
            method: HTTP method (``"GET"``, …).
            url: Request URL.
            **kwargs: Additional arguments forwarded to
                :func:`requests.Session.request`.

        Returns:
            The :class:`requests.Response` object.

        Raises:
            ProPublicaAPIError: On network error or exhausted retries.
        """
        delay = 1.0
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self._session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                raise ProPublicaAPIError(
                    f"Network error contacting ProPublica: {exc}"
                ) from exc

            if response.status_code not in self.RETRY_STATUSES:
                return response

            if attempt == self.MAX_RETRIES:
                raise ProPublicaAPIError(
                    f"ProPublica API returned HTTP {response.status_code} "
                    f"after {self.MAX_RETRIES} retries for {url}"
                )

            time.sleep(delay)
            delay *= 2

        return response  # type: ignore[return-value]

    @staticmethod
    def _strip_html(html: str) -> str:
        """Strip HTML tags and decode common entities from fetched article text.

        Args:
            html: Raw HTML string.

        Returns:
            Normalised plain text.
        """
        text = re.sub(r"<[^>]+>", " ", html)
        entities = {
            "&nbsp;": " ",
            "&amp;": "&",
            "&lt;": "<",
            "&gt;": ">",
            "&quot;": '"',
            "&apos;": "'",
        }
        for entity, char in entities.items():
            text = text.replace(entity, char)
        text = re.sub(r"&#?\w+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
