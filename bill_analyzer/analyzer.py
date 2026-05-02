"""High-level orchestrator that combines GovInfo fetching with Claude analysis."""

from .claude_client import ClaudeClient
from .comparison_engine import ComparisonEngine
from .congress_gov_client import CongressGovClient
from .congressional_record_client import CongressionalRecordClient
from .exceptions import BillAnalyzerError
from .govinfo_client import GovInfoAPIClient
from .models import (
    BillAnalysis,
    BillMetadata,
    ComparisonResult,
    GroundTruth,
    SourceMaterial,
)


class BillAnalyzer:
    """Orchestrates GovInfo bill retrieval and Claude-powered analysis.

    Provides high-level methods that fetch bill content from the US GovInfo
    API and pass it to Claude for summarisation or structured analysis.
    Also exposes comparison methods that measure how accurately politicians
    and media represent a bill against the authoritative CRS ground truth.

    Args:
        govinfo_api_key: GovInfo API key. Defaults to ``GOVINFO_API_KEY``.
        anthropic_api_key: Anthropic API key. Defaults to ``ANTHROPIC_API_KEY``.
        congress_gov_api_key: Congress.gov API key. Defaults to
            ``CONGRESS_GOV_API_KEY``. Required only for comparison methods.
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
        congress_gov_api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._govinfo = GovInfoAPIClient(api_key=govinfo_api_key)
        self._claude = ClaudeClient(api_key=anthropic_api_key, model=model)

        # Comparison clients — initialised lazily on first use so that
        # missing CONGRESS_GOV_API_KEY does not break Phase 1 operations.
        self._congress_gov_api_key = congress_gov_api_key
        self._congress: CongressGovClient | None = None
        self._record: CongressionalRecordClient | None = None
        self._comparison_engine: ComparisonEngine | None = None

    # ------------------------------------------------------------------
    # Phase 1 — summarisation and analysis
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

    # ------------------------------------------------------------------
    # Phase 2 — comparison engine
    # ------------------------------------------------------------------

    def get_ground_truth(self, package_id: str) -> GroundTruth:
        """Fetch and return the CRS ground truth for a bill.

        Builds the authoritative :class:`GroundTruth` combining GovInfo raw
        text with the CRS summary from Congress.gov. Does not invoke Claude.

        Args:
            package_id: GovInfo package identifier.

        Returns:
            :class:`GroundTruth` with CRS summary and raw bill text.

        Raises:
            GovInfoAPIError: If GovInfo cannot be reached.
            CongressGovAPIError: If Congress.gov cannot be reached or has no
                CRS summary for the bill.
        """
        engine = self._get_comparison_engine()
        return engine._build_ground_truth(package_id)  # noqa: SLF001

    def compare_floor_speeches(
        self,
        package_id: str,
        chamber: str | None = None,
    ) -> ComparisonResult:
        """Compare a bill against Congressional Record floor speeches.

        Args:
            package_id: GovInfo package identifier.
            chamber: Optional ``"House"`` or ``"Senate"`` filter.

        Returns:
            :class:`ComparisonResult` with per-speech discrepancy analysis.

        Raises:
            BillAnalyzerError: If any API call fails.
        """
        engine = self._get_comparison_engine()
        return engine.compare_floor_speeches(package_id, chamber=chamber)

    def compare_sources(
        self,
        ground_truth: GroundTruth,
        sources: list[SourceMaterial],
    ) -> ComparisonResult:
        """Compare pre-built source materials against a ground truth.

        Args:
            ground_truth: Authoritative bill representation.
            sources: External representations to compare.

        Returns:
            :class:`ComparisonResult` with per-source discrepancy analysis.

        Raises:
            BillAnalyzerError: If the Claude API call fails.
        """
        engine = self._get_comparison_engine()
        return engine.compare_source_materials(ground_truth, sources)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_comparison_engine(self) -> ComparisonEngine:
        """Return the lazily-initialised ComparisonEngine, creating it if needed."""
        if self._comparison_engine is None:
            self._congress = CongressGovClient(api_key=self._congress_gov_api_key)
            self._record = CongressionalRecordClient(
                api_key=self._congress_gov_api_key,
                congress_client=self._congress,
            )
            self._comparison_engine = ComparisonEngine(
                govinfo_client=self._govinfo,
                congress_client=self._congress,
                record_client=self._record,
                claude_client=self._claude,
            )
        return self._comparison_engine
