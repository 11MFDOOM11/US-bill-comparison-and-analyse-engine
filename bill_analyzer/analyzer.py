"""High-level orchestrator that combines GovInfo fetching with Claude analysis."""

from .claude_client import ClaudeClient
from .exceptions import BillAnalyzerError
from .govinfo_client import GovInfoAPIClient
from .models import BillAnalysis, BillMetadata


class BillAnalyzer:
    """Orchestrates GovInfo bill retrieval and Claude-powered analysis.

    Provides high-level methods that fetch bill content from the US GovInfo
    API and pass it to Claude for summarisation or structured analysis.

    Args:
        govinfo_api_key: GovInfo API key. Defaults to ``GOVINFO_API_KEY``.
        anthropic_api_key: Anthropic API key. Defaults to ``ANTHROPIC_API_KEY``.
        model: Claude model ID. Defaults to ``CLAUDE_MODEL`` env var or
            ``claude-sonnet-4-6``.

    Raises:
        GovInfoAPIError: If the GovInfo API key is missing.
        ClaudeAPIError: If the Anthropic API key is missing.
    """

    def __init__(
        self,
        govinfo_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._govinfo = GovInfoAPIClient(api_key=govinfo_api_key)
        self._claude = ClaudeClient(api_key=anthropic_api_key, model=model)

    # ------------------------------------------------------------------
    # Primary methods
    # ------------------------------------------------------------------

    def analyze_by_package_id(self, package_id: str) -> BillAnalysis:
        """Fetch a bill and return a full structured analysis.

        Retrieves the bill metadata and text from GovInfo, then asks Claude
        to produce a structured :class:`BillAnalysis`.

        Args:
            package_id: GovInfo package identifier (e.g. ``BILLS-118hr1234ih``).

        Returns:
            :class:`BillAnalysis` with summary, provisions, impact, and context.

        Raises:
            GovInfoAPIError: If GovInfo cannot be reached or the bill is not found.
            ClaudeAPIError: If the Claude API call fails.
        """
        metadata = self._govinfo.get_bill_metadata(package_id)
        bill_text = self._govinfo.get_bill_text(package_id)
        analysis = self._claude.analyze_bill(bill_text, title=metadata.title)
        analysis.package_id = package_id
        return analysis

    def summarize_by_package_id(self, package_id: str) -> str:
        """Fetch a bill and return a plain-English summary string.

        Args:
            package_id: GovInfo package identifier.

        Returns:
            Plain-English summary produced by Claude.

        Raises:
            GovInfoAPIError: If GovInfo cannot be reached or the bill is not found.
            ClaudeAPIError: If the Claude API call fails.
        """
        metadata = self._govinfo.get_bill_metadata(package_id)
        bill_text = self._govinfo.get_bill_text(package_id)
        return self._claude.summarize_bill(bill_text, title=metadata.title)

    def search_and_analyze(
        self,
        keyword: str,
        congress: int | None = None,
        max_results: int = 5,
        date_issued_start_date: str | None = None,
        date_issued_end_date: str | None = None,
    ) -> list[BillAnalysis]:
        """Search GovInfo for bills matching *keyword*, then analyse each one.

        Bills that cannot be fetched or analysed are skipped with a warning;
        the remaining results are returned.

        Args:
            keyword: Search query string.
            congress: Congress number filter (e.g. ``118``).
            max_results: Maximum number of bills to fetch and analyse.
            date_issued_start_date: ISO 8601 start date (``YYYY-MM-DD``).
            date_issued_end_date: ISO 8601 end date (``YYYY-MM-DD``).

        Returns:
            List of :class:`BillAnalysis` objects, one per successfully
            analysed bill.

        Raises:
            GovInfoAPIError: If the search request itself fails.
        """
        bills = self._govinfo.search_bills(
            keyword=keyword,
            congress=congress,
            date_issued_start_date=date_issued_start_date,
            date_issued_end_date=date_issued_end_date,
            page_size=max_results,
        )

        analyses: list[BillAnalysis] = []
        for bill in bills:
            try:
                bill_text = self._govinfo.get_bill_text(bill.package_id)
                analysis = self._claude.analyze_bill(
                    bill_text, title=bill.title
                )
                analysis.package_id = bill.package_id
                analyses.append(analysis)
            except BillAnalyzerError as exc:
                print(f"Warning: skipping {bill.package_id!r} — {exc}")

        return analyses

    def get_metadata(self, package_id: str) -> BillMetadata:
        """Return metadata for a bill without fetching text or running analysis.

        Useful for quickly inspecting bill details before committing to a
        full (and more expensive) analysis run.

        Args:
            package_id: GovInfo package identifier.

        Returns:
            :class:`BillMetadata` from the GovInfo summary endpoint.

        Raises:
            GovInfoAPIError: If the API call fails.
        """
        return self._govinfo.get_bill_metadata(package_id)
