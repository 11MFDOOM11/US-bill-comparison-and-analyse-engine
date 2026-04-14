"""GovInfo API client with exponential back-off retry."""

import os
import re
import time
from typing import Any

import requests

from .exceptions import GovInfoAPIError
from .models import BillMetadata


class GovInfoAPIClient:
    """Client for the US GovInfo API.

    Fetches bill metadata, full text, and search results from
    https://api.govinfo.gov. Automatically retries on HTTP 429 and 503
    responses with exponential back-off.

    Args:
        api_key: GovInfo API key.  Defaults to the ``GOVINFO_API_KEY``
            environment variable.

    Raises:
        GovInfoAPIError: If no API key is found or a request fails.
    """

    BASE_URL = "https://api.govinfo.gov"
    MAX_RETRIES = 4
    RETRY_STATUSES = {429, 503}

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("GOVINFO_API_KEY")
        if not self._api_key:
            raise GovInfoAPIError(
                "GovInfo API key not found. "
                "Set the GOVINFO_API_KEY environment variable."
            )
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_bill_metadata(self, package_id: str) -> BillMetadata:
        """Fetch bill metadata from ``/packages/{package_id}/summary``.

        Args:
            package_id: GovInfo package identifier (e.g. ``BILLS-118hr1234ih``).

        Returns:
            :class:`BillMetadata` populated from the API response.

        Raises:
            GovInfoAPIError: On network failure or non-200 response.
        """
        url = f"{self.BASE_URL}/packages/{package_id}/summary"
        response = self._request_with_retry("GET", url)

        if response.status_code != 200:
            raise GovInfoAPIError(
                f"Failed to fetch metadata for {package_id!r}: "
                f"HTTP {response.status_code}"
            )

        data: dict[str, Any] = response.json()
        return BillMetadata(
            package_id=data.get("packageId", package_id),
            title=data.get("title", "Unknown"),
            congress=str(data.get("congress", "")),
            bill_type=data.get("billType"),
            bill_number=data.get("billNumber"),
            date_issued=data.get("dateIssued"),
            session=data.get("session"),
            collection_code=data.get("collectionCode"),
            government_author=data.get("governmentAuthor2", []),
        )

    def get_bill_text(self, package_id: str) -> str:
        """Fetch the full bill text from ``/packages/{package_id}/htm``.

        The endpoint returns HTML; this method strips tags and normalises
        whitespace so the plain text is suitable for passing to Claude.

        Args:
            package_id: GovInfo package identifier.

        Returns:
            Plain-text content of the bill.

        Raises:
            GovInfoAPIError: On network failure or non-200 response.
        """
        url = f"{self.BASE_URL}/packages/{package_id}/htm"
        response = self._request_with_retry(
            "GET",
            url,
            headers={"Accept": "text/html,application/xhtml+xml,text/plain"},
        )

        if response.status_code != 200:
            raise GovInfoAPIError(
                f"Failed to fetch bill text for {package_id!r}: "
                f"HTTP {response.status_code}"
            )

        return self._strip_html(response.text)

    def search_bills(
        self,
        keyword: str,
        congress: int | None = None,
        date_issued_start_date: str | None = None,
        date_issued_end_date: str | None = None,
        page_size: int = 10,
        offset_mark: str = "*",
    ) -> list[BillMetadata]:
        """Search GovInfo bills using the ``/search`` endpoint.

        Args:
            keyword: Full-text search query.
            congress: Congress number filter (e.g. ``118``).
            date_issued_start_date: ISO 8601 start date (``YYYY-MM-DD``).
            date_issued_end_date: ISO 8601 end date (``YYYY-MM-DD``).
            page_size: Results per page (max 100).
            offset_mark: Pagination cursor; ``"*"`` means first page.

        Returns:
            List of :class:`BillMetadata` for matching packages.

        Raises:
            GovInfoAPIError: On network failure or non-200 response.
        """
        url = f"{self.BASE_URL}/search"
        payload: dict[str, Any] = {
            "query": keyword,
            "pageSize": page_size,
            "offsetMark": offset_mark,
            "collections": ["BILLS"],
        }
        if congress is not None:
            payload["congress"] = str(congress)
        if date_issued_start_date:
            payload["dateIssuedStartDate"] = date_issued_start_date
        if date_issued_end_date:
            payload["dateIssuedEndDate"] = date_issued_end_date

        response = self._request_with_retry("POST", url, json=payload)

        if response.status_code != 200:
            raise GovInfoAPIError(
                f"Bill search failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )

        data: dict[str, Any] = response.json()
        results: list[dict[str, Any]] = data.get("results", [])

        bills: list[BillMetadata] = []
        for item in results:
            bills.append(
                BillMetadata(
                    package_id=item.get("packageId", ""),
                    title=item.get("title", "Unknown"),
                    congress=str(item.get("congress", "")),
                    bill_type=item.get("billType"),
                    bill_number=item.get("billNumber"),
                    date_issued=item.get("dateIssued"),
                    collection_code=item.get("collectionCode"),
                )
            )
        return bills

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Perform an HTTP request, retrying on 429/503 with exponential back-off.

        Args:
            method: HTTP method (``"GET"``, ``"POST"``, …).
            url: Request URL.
            **kwargs: Additional arguments forwarded to :func:`requests.Session.request`.

        Returns:
            The :class:`requests.Response` object.

        Raises:
            GovInfoAPIError: On network error or exhausted retries.
        """
        # Inject API key as a query parameter on every request.
        params = kwargs.pop("params", {})
        params["api_key"] = self._api_key
        kwargs["params"] = params

        delay = 1.0
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self._session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                raise GovInfoAPIError(f"Network error contacting GovInfo: {exc}") from exc

            if response.status_code not in self.RETRY_STATUSES:
                return response

            if attempt == self.MAX_RETRIES:
                raise GovInfoAPIError(
                    f"GovInfo API returned HTTP {response.status_code} after "
                    f"{self.MAX_RETRIES} retries for {url}"
                )

            time.sleep(delay)
            delay *= 2

        # Unreachable — satisfies type checkers.
        return response  # type: ignore[return-value]

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags and decode common entities.

        Args:
            html: Raw HTML string.

        Returns:
            Normalised plain text.
        """
        # Remove all HTML tags.
        text = re.sub(r"<[^>]+>", " ", html)
        # Decode common HTML entities.
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
        # Remove remaining named / numeric entities.
        text = re.sub(r"&#?\w+;", " ", text)
        # Collapse whitespace.
        text = re.sub(r"\s+", " ", text).strip()
        return text
