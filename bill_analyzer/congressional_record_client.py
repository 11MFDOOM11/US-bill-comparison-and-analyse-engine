"""Congressional Record API client for fetching floor speeches."""

import os
import re
import time
from typing import Any

import requests

from .congress_gov_client import CongressGovClient
from .exceptions import CongressionalRecordAPIError
from .models import RecordSpeech
from .utils import PackageIDParser


class CongressionalRecordClient:
    """Client for the Congressional Record via the Congress.gov API v3.

    Floor speeches are queried by date (derived from the bill's action
    timeline) and then filtered to those referencing the target bill.
    Speaker metadata is resolved via Congress.gov member lookups with an
    in-process cache to avoid redundant requests.

    The Congressional Record is a US Government work entirely in the
    public domain — no copyright concerns for storage or display.

    Args:
        api_key: Congress.gov API key. Defaults to the
            ``CONGRESS_GOV_API_KEY`` environment variable (shared with
            :class:`CongressGovClient`).
        congress_client: An existing :class:`CongressGovClient` instance.
            Required only for :meth:`get_speeches_by_package_id` which
            needs to fetch bill action dates and resolve member metadata.

    Raises:
        CongressionalRecordAPIError: If no API key is found or a request
            fails.
    """

    BASE_URL = "https://api.congress.gov/v3"
    MAX_RETRIES = 4
    RETRY_STATUSES = {429, 503}

    # Regex for "Mr./Ms./Mrs./Madam SURNAME." at the start of a speech block.
    _SPEAKER_RE: re.Pattern[str] = re.compile(
        r"(?:Mr\.|Ms\.|Mrs\.|Madam|Mr)\s+([A-Z][A-Z\-\s]+?)(?=\s*\.)",
        re.MULTILINE,
    )

    def __init__(
        self,
        api_key: str | None = None,
        congress_client: CongressGovClient | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("CONGRESS_GOV_API_KEY")
        if not self._api_key:
            raise CongressionalRecordAPIError(
                "Congress.gov API key not found. "
                "Set the CONGRESS_GOV_API_KEY environment variable."
            )
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._congress_client = congress_client
        self._member_cache: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_speeches_for_bill(
        self,
        congress: str,
        bill_type: str,
        bill_number: str,
        action_dates: list[str],
        chamber: str | None = None,
    ) -> list[RecordSpeech]:
        """Fetch floor speeches referencing *bill_number* on *action_dates*.

        Queries ``daily-congressional-record`` for each action date, fetches
        the articles in each matching issue, and filters to those that mention
        the bill by reference in the title or opening text.

        Args:
            congress: Congress number (unused in the Record query but
                included for future filtering).
            bill_type: Bill type (e.g. ``"hr"``, ``"s"``).
            bill_number: Bill number (e.g. ``"1234"``).
            action_dates: ISO 8601 dates from the bill's action timeline.
            chamber: Optional ``"House"`` or ``"Senate"`` filter.

        Returns:
            List of :class:`RecordSpeech` objects, one per matching article.

        Raises:
            CongressionalRecordAPIError: If an API call fails unrecoverably.
        """
        bill_type_upper = bill_type.upper()
        bill_ref = f"{bill_type_upper} {bill_number}"
        alt_refs = [
            f"H.R. {bill_number}",
            f"H.R.{bill_number}",
            f"S. {bill_number}",
            f"S.{bill_number}",
            bill_ref,
        ]

        speeches: list[RecordSpeech] = []

        for date_str in action_dates:
            try:
                articles = self._get_articles_for_date(date_str, chamber)
            except CongressionalRecordAPIError:
                continue

            for article in articles:
                title: str = article.get("title", "")
                full_text: str = article.get("fullText", "")
                article_chamber: str = article.get("chamber", "")

                if chamber and chamber.lower() not in article_chamber.lower():
                    continue

                # Filter: bill must be mentioned in the title or opening text.
                excerpt = f"{title} {full_text[:800]}".upper()
                if not any(ref.upper() in excerpt for ref in alt_refs):
                    continue

                speaker_name = self._extract_speaker_name(full_text)
                member_info = self._lookup_member(speaker_name, article_chamber)

                url: str = article.get("url", "")
                volume, issue = self._extract_volume_issue(url, article)

                speeches.append(
                    RecordSpeech(
                        speaker_name=speaker_name,
                        bioguide_id=member_info.get("bioguide_id", ""),
                        party=member_info.get("party", ""),
                        state=member_info.get("state", ""),
                        chamber=article_chamber,
                        date=date_str,
                        title=title,
                        volume=volume,
                        issue=issue,
                        url=url,
                        full_text=full_text,
                    )
                )

        return speeches

    def get_speeches_by_package_id(
        self,
        package_id: str,
        chamber: str | None = None,
    ) -> list[RecordSpeech]:
        """Convenience wrapper — parses a GovInfo ID and fetches speeches.

        Fetches the bill's action dates via :class:`CongressGovClient` and
        then delegates to :meth:`get_speeches_for_bill`.

        Args:
            package_id: GovInfo package identifier (e.g. ``BILLS-118hr1234ih``).
            chamber: Optional ``"House"`` or ``"Senate"`` filter.

        Returns:
            List of :class:`RecordSpeech` objects.

        Raises:
            CongressionalRecordAPIError: If no congress_client was supplied,
                or if an API call fails.
        """
        if self._congress_client is None:
            raise CongressionalRecordAPIError(
                "A CongressGovClient instance is required for "
                "get_speeches_by_package_id. Pass congress_client at "
                "construction time."
            )

        congress, bill_type, bill_number = PackageIDParser.to_congress_gov_params(
            package_id
        )

        try:
            action_dates = self._congress_client.get_bill_action_dates(
                congress, bill_type, bill_number
            )
        except Exception as exc:
            raise CongressionalRecordAPIError(
                f"Failed to fetch action dates for {package_id!r}: {exc}"
            ) from exc

        return self.get_speeches_for_bill(
            congress, bill_type, bill_number, action_dates, chamber
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_articles_for_date(
        self,
        date_str: str,
        chamber: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all Congressional Record articles published on *date_str*."""
        year = date_str[:4] if date_str else ""
        url = f"{self.BASE_URL}/daily-congressional-record"
        params: dict[str, Any] = {}
        if year:
            params["y"] = year

        response = self._request_with_retry("GET", url, params=params)

        if response.status_code != 200:
            raise CongressionalRecordAPIError(
                f"Failed to fetch daily Congressional Record list for "
                f"{date_str}: HTTP {response.status_code}"
            )

        data: dict[str, Any] = response.json()
        issues: list[dict[str, Any]] = data.get(
            "dailyCongressionalRecord", []
        )

        all_articles: list[dict[str, Any]] = []
        for issue in issues:
            if date_str and issue.get("issueDate", "") != date_str:
                continue

            volume = str(issue.get("volumeNumber", ""))
            issue_num = str(issue.get("issueNumber", ""))

            if not volume or not issue_num:
                continue

            try:
                articles = self._get_issue_articles(volume, issue_num)
                all_articles.extend(articles)
            except CongressionalRecordAPIError:
                continue

        return all_articles

    def _get_issue_articles(
        self, volume: str, issue_num: str
    ) -> list[dict[str, Any]]:
        """Fetch all articles for a specific Congressional Record issue."""
        url = (
            f"{self.BASE_URL}/daily-congressional-record"
            f"/{volume}/{issue_num}/articles"
        )
        response = self._request_with_retry("GET", url)

        if response.status_code != 200:
            raise CongressionalRecordAPIError(
                f"Failed to fetch articles for volume {volume}, "
                f"issue {issue_num}: HTTP {response.status_code}"
            )

        return response.json().get("articles", [])

    def _extract_speaker_name(self, text: str) -> str:
        """Extract the first speaker surname from floor speech text.

        Looks for the standard Congressional Record format
        ``Mr./Ms. SURNAME.`` at the start of a speech block.

        Args:
            text: Full verbatim text of the article.

        Returns:
            Title-cased speaker surname, or ``"Unknown"`` if not found.
        """
        match = self._SPEAKER_RE.search(text[:800])
        if match:
            raw = match.group(1).strip()
            return raw.title()
        return "Unknown"

    def _lookup_member(
        self, name: str, chamber: str
    ) -> dict[str, str]:
        """Return cached or freshly fetched member metadata for *name*.

        Args:
            name: Speaker surname (title-cased).
            chamber: ``"House"`` or ``"Senate"``.

        Returns:
            Dict with ``bioguide_id``, ``party``, and ``state``.
        """
        cache_key = f"{name}:{chamber}"
        if cache_key in self._member_cache:
            return self._member_cache[cache_key]

        if name == "Unknown" or self._congress_client is None:
            result: dict[str, str] = {
                "bioguide_id": "", "party": "", "state": ""
            }
        else:
            result = self._congress_client.get_member_by_name(name, chamber)

        self._member_cache[cache_key] = result
        return result

    @staticmethod
    def _extract_volume_issue(
        url: str, article: dict[str, Any]
    ) -> tuple[str, str]:
        """Extract volume and issue numbers from a URL or article dict.

        Args:
            url: The article's canonical API URL.
            article: Raw article dict from the API.

        Returns:
            ``(volume, issue)`` strings, both empty if not found.
        """
        match = re.search(
            r"/daily-congressional-record/(\d+)/(\d+)", url
        )
        if match:
            return match.group(1), match.group(2)
        return (
            str(article.get("volume", "")),
            str(article.get("issue", "")),
        )

    def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Perform an HTTP request, retrying on 429/503 with exponential back-off.

        Raises:
            CongressionalRecordAPIError: On network error or exhausted retries.
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
                raise CongressionalRecordAPIError(
                    f"Network error contacting Congressional Record API: {exc}"
                ) from exc

            if response.status_code not in self.RETRY_STATUSES:
                return response

            if attempt == self.MAX_RETRIES:
                raise CongressionalRecordAPIError(
                    f"Congressional Record API returned "
                    f"HTTP {response.status_code} after "
                    f"{self.MAX_RETRIES} retries for {url}"
                )

            time.sleep(delay)
            delay *= 2

        return response  # type: ignore[return-value]
