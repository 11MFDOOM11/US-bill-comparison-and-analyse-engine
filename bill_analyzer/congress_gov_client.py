"""Congress.gov API client for fetching CRS bill summaries."""

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

    Fetches CRS (Congressional Research Service) summaries, which serve as
    the neutral ground-truth baseline for the comparison engine.  CRS
    summaries are authored by non-partisan analysts and are the most
    defensible plain-language description of what a bill actually does.

    Args:
        api_key: Congress.gov API key.  Defaults to the
            ``CONGRESS_GOV_API_KEY`` environment variable.

    Raises:
        CongressGovAPIError: If no API key is found or a request fails.

    Note:
        All bill-type parameters **must be lower case** — the API rejects
        upper-case values (e.g. use ``"hr"`` not ``"HR"``).
    """

    BASE_URL = "https://api.congress.gov/v3"
    MAX_RETRIES = 4
    RETRY_STATUSES = {429, 503}

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("CONGRESS_GOV_API_KEY")
        if not self._api_key:
            raise CongressGovAPIError(
                "Congress.gov API key not found. "
                "Set the CONGRESS_GOV_API_KEY environment variable. "
                "Register at https://api.congress.gov/sign-up"
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

        Calls ``GET /bill/{congress}/{billType}/{billNumber}/summaries`` and
        returns the entry with the latest ``updateDate``.

        Args:
            congress: Congress number, e.g. ``"118"``.
            bill_type: Bill type in **lower case**, e.g. ``"hr"`` or ``"s"``.
            bill_number: Bill number, e.g. ``"1234"``.

        Returns:
            :class:`CRSSummary` populated from the most recent API entry.

        Raises:
            CongressGovAPIError: If the API call fails or no summaries exist.
        """
        bill_type_lc = bill_type.lower()
        url = (
            f"{self.BASE_URL}/bill/{congress}/{bill_type_lc}"
            f"/{bill_number}/summaries"
        )
        params: dict[str, str] = {"format": "json"}
        response = self._request_with_retry("GET", url, params=params)

        if response.status_code == 404:
            raise CongressGovAPIError(
                f"No bill found on Congress.gov for "
                f"congress={congress}, type={bill_type_lc}, number={bill_number}"
            )
        if response.status_code != 200:
            raise CongressGovAPIError(
                f"Congress.gov API returned HTTP {response.status_code} "
                f"for bill {congress}/{bill_type_lc}/{bill_number}/summaries"
            )

        data: dict[str, Any] = response.json()
        summaries: list[dict[str, Any]] = data.get("summaries", [])

        if not summaries:
            raise CongressGovAPIError(
                f"No CRS summaries available on Congress.gov for "
                f"{congress}/{bill_type_lc}/{bill_number}. "
                "The bill may be too new or not yet summarised."
            )

        # Take the most recent entry by updateDate.
        latest = max(summaries, key=lambda s: s.get("updateDate", ""))

        return CRSSummary(
            congress=congress,
            bill_type=bill_type_lc,
            bill_number=bill_number,
            action_date=latest.get("actionDate", ""),
            action_description=latest.get("actionDesc", ""),
            text=self._strip_html(latest.get("text", "")),
            update_date=latest.get("updateDate", ""),
            version_code=latest.get("versionCode", ""),
        )

    def get_crs_summary_by_package_id(self, package_id: str) -> CRSSummary:
        """Convenience wrapper that converts a GovInfo package ID then fetches.

        Parses a GovInfo-format package ID (e.g. ``BILLS-118hr1234ih``) into
        its component parts and delegates to :meth:`get_crs_summary`.

        Args:
            package_id: GovInfo package ID, e.g. ``BILLS-118hr1234ih``.

        Returns:
            :class:`CRSSummary` for the bill.

        Raises:
            ValueError: If *package_id* cannot be parsed.
            CongressGovAPIError: If the API call fails.
        """
        congress, bill_type, bill_number = (
            PackageIDParser.to_congress_gov_params(package_id)
        )
        return self.get_crs_summary(congress, bill_type, bill_number)

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

        Injects the ``api_key`` query parameter on every request.

        Args:
            method: HTTP method (``"GET"``, …).
            url: Request URL.
            **kwargs: Additional arguments forwarded to
                :func:`requests.Session.request`.

        Returns:
            The :class:`requests.Response` object.

        Raises:
            CongressGovAPIError: On network error or exhausted retries.
        """
        params = kwargs.pop("params", {})
        params["api_key"] = self._api_key
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
        """Strip HTML tags, CDATA wrappers, and decode common entities.

        The CRS summary ``text`` field is HTML wrapped in CDATA sections.
        This method removes both the CDATA markers and the HTML tags before
        the plain text is stored or passed to Claude.

        Args:
            html: Raw HTML string (may contain CDATA wrappers).

        Returns:
            Normalised plain text.
        """
        # Unwrap CDATA sections: <![CDATA[...]]>
        text = re.sub(r"<!\[CDATA\[", "", html)
        text = re.sub(r"\]\]>", "", text)
        # Remove all HTML tags.
        text = re.sub(r"<[^>]+>", " ", text)
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
