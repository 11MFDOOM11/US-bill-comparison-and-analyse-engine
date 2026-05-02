"""Congress.gov API v3 client for CRS summaries, bill metadata, and member lookups."""

import os
import re
import time
from typing import Any

import requests

from .exceptions import CongressGovAPIError
from .models import CRSSummary
from .utils import PackageIDParser


class CongressGovClient:
    """Client for the Congress.gov REST API v3.

    Fetches CRS-authored bill summaries, legislative action timelines, and
    member metadata. All bill type parameters are normalised to lower-case
    before being sent — the API rejects upper-case values.

    Rate limit: 5,000 requests per hour (shared with CongressionalRecordClient
    when both use the same key).

    Args:
        api_key: Congress.gov API key. Defaults to the
            ``CONGRESS_GOV_API_KEY`` environment variable.

    Raises:
        CongressGovAPIError: If no API key is found or a request fails.
    """

    BASE_URL = "https://api.congress.gov/v3"
    MAX_RETRIES = 4
    RETRY_STATUSES = {429, 503}

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("CONGRESS_GOV_API_KEY")
        if not self._api_key:
            raise CongressGovAPIError(
                "Congress.gov API key not found. "
                "Set the CONGRESS_GOV_API_KEY environment variable."
            )
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_crs_summary(
        self,
        congress: str,
        bill_type: str,
        bill_number: str,
    ) -> CRSSummary:
        """Fetch the most recent CRS summary for a bill.

        The summaries list may contain multiple entries (one per version of
        the bill). This method always returns the entry with the highest
        ``updateDate`` — i.e. the most recent authoritative description.

        Args:
            congress: Congress number (e.g. ``"118"``).
            bill_type: Bill type, lower-case (e.g. ``"hr"``, ``"s"``).
            bill_number: Bill number (e.g. ``"1234"``).

        Returns:
            :class:`CRSSummary` with HTML stripped from the summary text.

        Raises:
            CongressGovAPIError: If the API call fails or no summaries exist.
        """
        bill_type = bill_type.lower()
        url = (
            f"{self.BASE_URL}/bill/{congress}/{bill_type}/{bill_number}/summaries"
        )
        response = self._request_with_retry("GET", url)

        if response.status_code != 200:
            raise CongressGovAPIError(
                f"Failed to fetch CRS summary for "
                f"{congress}/{bill_type}/{bill_number}: "
                f"HTTP {response.status_code}"
            )

        data: dict[str, Any] = response.json()
        summaries: list[dict[str, Any]] = data.get("summaries", [])

        if not summaries:
            raise CongressGovAPIError(
                f"No CRS summaries found for "
                f"{congress}/{bill_type}/{bill_number}"
            )

        most_recent = max(summaries, key=lambda s: s.get("updateDate", ""))

        return CRSSummary(
            congress=congress,
            bill_type=bill_type,
            bill_number=bill_number,
            action_date=most_recent.get("actionDate", ""),
            action_description=most_recent.get("actionDesc", ""),
            text=self._strip_html(most_recent.get("text", "")),
            update_date=most_recent.get("updateDate", ""),
            version_code=most_recent.get("versionCode", ""),
        )

    def get_crs_summary_by_package_id(self, package_id: str) -> CRSSummary:
        """Convenience wrapper — parses a GovInfo ID then calls get_crs_summary.

        Args:
            package_id: GovInfo package identifier (e.g. ``BILLS-118hr1234ih``).

        Returns:
            :class:`CRSSummary` for the identified bill.

        Raises:
            CongressGovAPIError: If the API call fails or no summaries exist.
            ValueError: If *package_id* cannot be parsed.
        """
        congress, bill_type, bill_number = PackageIDParser.to_congress_gov_params(
            package_id
        )
        return self.get_crs_summary(congress, bill_type, bill_number)

    def get_bill_action_dates(
        self,
        congress: str,
        bill_type: str,
        bill_number: str,
    ) -> list[str]:
        """Return a sorted, deduplicated list of ISO 8601 dates from the bill's action timeline.

        Used by :class:`CongressionalRecordClient` to scope Congressional Record
        queries to dates on which the bill was debated or acted upon.

        Args:
            congress: Congress number.
            bill_type: Bill type, lower-case.
            bill_number: Bill number.

        Returns:
            Sorted list of ISO 8601 date strings (``YYYY-MM-DD``).

        Raises:
            CongressGovAPIError: If the API call fails.
        """
        bill_type = bill_type.lower()
        url = (
            f"{self.BASE_URL}/bill/{congress}/{bill_type}/{bill_number}/actions"
        )
        response = self._request_with_retry("GET", url)

        if response.status_code != 200:
            raise CongressGovAPIError(
                f"Failed to fetch actions for "
                f"{congress}/{bill_type}/{bill_number}: "
                f"HTTP {response.status_code}"
            )

        data: dict[str, Any] = response.json()
        actions: list[dict[str, Any]] = data.get("actions", [])

        dates: set[str] = set()
        for action in actions:
            date = action.get("actionDate", "")
            if date:
                dates.add(date)

        return sorted(dates)

    def get_member_by_name(
        self,
        name: str,
        chamber: str | None = None,
    ) -> dict[str, str]:
        """Look up a member by surname fragment and return their key metadata.

        Performs a case-insensitive substring match on the member list. When
        a *chamber* is supplied the match is additionally constrained to
        members who served in that chamber.

        Args:
            name: Surname (or partial name) to search for.
            chamber: Optional ``"House"`` or ``"Senate"`` filter.

        Returns:
            Dict with ``bioguide_id``, ``party`` (single letter), and
            ``state`` (two-letter abbreviation). Returns empty strings on
            no match.
        """
        if not name or name == "Unknown":
            return {"bioguide_id": "", "party": "", "state": ""}

        url = f"{self.BASE_URL}/member"
        response = self._request_with_retry("GET", url)

        if response.status_code != 200:
            return {"bioguide_id": "", "party": "", "state": ""}

        data: dict[str, Any] = response.json()
        members: list[dict[str, Any]] = data.get("members", [])
        name_upper = name.upper()

        for member in members:
            member_name: str = member.get("name", "")
            if name_upper not in member_name.upper():
                continue

            if chamber:
                terms = member.get("terms", {})
                # terms may be a dict with an "item" list or a bare list
                term_list: list[dict[str, Any]] = (
                    terms.get("item", []) if isinstance(terms, dict) else terms
                )
                chamber_match = any(
                    chamber.lower() in t.get("chamber", "").lower()
                    for t in term_list
                )
                if term_list and not chamber_match:
                    continue

            raw_party = member.get("partyName", member.get("party", ""))
            party_initial = raw_party[:1].upper() if raw_party else ""

            return {
                "bioguide_id": member.get("bioguideId", ""),
                "party": party_initial,
                "state": member.get("state", ""),
            }

        return {"bioguide_id": "", "party": "", "state": ""}

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
            **kwargs: Additional arguments forwarded to the session request.

        Returns:
            The :class:`requests.Response` object.

        Raises:
            CongressGovAPIError: On network error or exhausted retries.
        """
        params: dict[str, Any] = kwargs.pop("params", {})
        params["api_key"] = self._api_key
        params.setdefault("format", "json")
        kwargs["params"] = params

        delay = 1.0
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self._session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                raise CongressGovAPIError(
                    f"Network error contacting Congress.gov: {exc}"
                ) from exc

            if response.status_code not in self.RETRY_STATUSES:
                return response

            if attempt == self.MAX_RETRIES:
                raise CongressGovAPIError(
                    f"Congress.gov API returned HTTP {response.status_code} "
                    f"after {self.MAX_RETRIES} retries for {url}"
                )

            time.sleep(delay)
            delay *= 2

        return response  # type: ignore[return-value]

    @staticmethod
    def _strip_html(html: str) -> str:
        """Strip CDATA wrappers, HTML tags, and common entities from text.

        Args:
            html: Raw HTML or CDATA-wrapped content from the API.

        Returns:
            Normalised plain text.
        """
        # Unwrap CDATA sections.
        text = re.sub(r"<!\[CDATA\[", "", html)
        text = re.sub(r"\]\]>", "", text)
        # Remove HTML tags.
        text = re.sub(r"<[^>]+>", " ", text)
        # Decode common named entities.
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
        # Remove remaining numeric/named entities.
        text = re.sub(r"&#?\w+;", " ", text)
        return re.sub(r"\s+", " ", text).strip()
