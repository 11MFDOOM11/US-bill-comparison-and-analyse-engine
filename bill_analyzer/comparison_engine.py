"""Comparison engine that measures how accurately sources represent a bill."""

from .claude_client import ClaudeClient
from .congress_gov_client import CongressGovClient
from .exceptions import BillAnalyzerError
from .govinfo_client import GovInfoAPIClient
from .models import (
    ComparisonResult,
    GroundTruth,
    SourceMaterial,
    SourceResult,
)
from .propublica_client import ProPublicaClient
from .utils import PackageIDParser


class ComparisonEngine:
    """Orchestrate ground-truth assembly and source comparison via Claude.

    Combines :class:`GovInfoAPIClient`, :class:`CongressGovClient`,
    :class:`ProPublicaClient`, and :class:`ClaudeClient` to measure how
    closely politicians and media outlets represent a bill against its CRS
    ground truth.

    Args:
        govinfo_api_key: GovInfo API key. Defaults to ``GOVINFO_API_KEY``.
        congress_gov_api_key: Congress.gov API key. Defaults to
            ``CONGRESS_GOV_API_KEY``.
        propublica_api_key: ProPublica API key. Defaults to
            ``PROPUBLICA_API_KEY``.
        anthropic_api_key: Anthropic API key. Defaults to
            ``ANTHROPIC_API_KEY``.
        model: Claude model ID. Defaults to ``CLAUDE_MODEL`` env var or
            ``claude-sonnet-4-6``.

    Raises:
        GovInfoAPIError: If the GovInfo API key is missing.
        CongressGovAPIError: If the Congress.gov API key is missing.
        ProPublicaAPIError: If the ProPublica API key is missing.
        ClaudeAPIError: If the Anthropic API key is missing.
    """

    def __init__(
        self,
        govinfo_api_key: str | None = None,
        congress_gov_api_key: str | None = None,
        propublica_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._govinfo = GovInfoAPIClient(api_key=govinfo_api_key)
        self._congress = CongressGovClient(api_key=congress_gov_api_key)
        self._propublica = ProPublicaClient(api_key=propublica_api_key)
        self._claude = ClaudeClient(api_key=anthropic_api_key, model=model)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def build_ground_truth(self, package_id: str) -> GroundTruth:
        """Build a :class:`GroundTruth` for a bill from GovInfo and Congress.gov.

        Fetches the raw bill text from GovInfo and the CRS summary from
        Congress.gov, combining them into the neutral comparison baseline.

        Args:
            package_id: GovInfo package ID, e.g. ``BILLS-118hr1234ih``.

        Returns:
            :class:`GroundTruth` with raw text and CRS summary populated.

        Raises:
            GovInfoAPIError: If the bill text or metadata cannot be fetched.
            CongressGovAPIError: If the CRS summary cannot be fetched.
            ValueError: If *package_id* cannot be parsed.
        """
        metadata = self._govinfo.get_bill_metadata(package_id)
        raw_text = self._govinfo.get_bill_text(package_id)
        crs = self._congress.get_crs_summary_by_package_id(package_id)

        congress, bill_type, bill_number = (
            PackageIDParser.to_congress_gov_params(package_id)
        )

        return GroundTruth(
            package_id=package_id,
            congress=congress,
            bill_type=bill_type,
            bill_number=bill_number,
            title=metadata.title,
            raw_text=raw_text,
            crs_summary=crs.text,
            crs_summary_date=crs.action_date,
            crs_action_description=crs.action_description,
        )

    def compare_politician_statements(
        self,
        package_id: str,
        congress: str | None = None,
    ) -> ComparisonResult:
        """Fetch politician statements and compare them against the bill's ground truth.

        Steps:
        1. Build :class:`GroundTruth` (GovInfo raw text + CRS summary).
        2. Determine the bill slug and congress number.
        3. Fetch politician statements via ProPublica.
        4. Fetch full text for each statement from the source URL.
        5. Send ground truth + statements to Claude for discrepancy analysis.
        6. Return a :class:`ComparisonResult`.

        Args:
            package_id: GovInfo package ID, e.g. ``BILLS-118hr1234ih``.
            congress: Override congress number (parsed from *package_id* if
                not supplied).

        Returns:
            :class:`ComparisonResult` with per-source scores and discrepancies.

        Raises:
            GovInfoAPIError: If bill data cannot be fetched.
            CongressGovAPIError: If the CRS summary cannot be fetched.
            ClaudeAPIError: If the Claude API call fails.
        """
        ground_truth = self.build_ground_truth(package_id)

        if congress is None:
            congress = ground_truth.congress

        # Build ProPublica bill slug: "hr1234-118"
        bill_slug = f"{ground_truth.bill_type}{ground_truth.bill_number}-{congress}"

        raw_statements = self._propublica.get_statements_for_bill(
            congress=congress,
            bill_slug=bill_slug,
        )

        sources: list[SourceMaterial] = []
        for stmt in raw_statements:
            full_text = self._propublica.fetch_full_text(stmt)
            if not full_text:
                continue
            sources.append(
                SourceMaterial(
                    source_type="politician_statement",
                    source_name=stmt.member_name,
                    party=stmt.party or None,
                    date=stmt.date,
                    url=stmt.url,
                    title=stmt.title,
                    full_text=full_text,
                )
            )

        return self.compare_source_materials(ground_truth, sources)

    def compare_source_materials(
        self,
        ground_truth: GroundTruth,
        sources: list[SourceMaterial],
    ) -> ComparisonResult:
        """Core comparison — accepts pre-built inputs for maximum flexibility.

        Sends the ground truth and all source materials to Claude and returns
        a :class:`ComparisonResult` with per-source :class:`SourceResult`
        objects containing discrepancies, accuracy scores, and framing labels.

        Args:
            ground_truth: The CRS-based neutral baseline for the bill.
            sources: List of :class:`SourceMaterial` to compare.

        Returns:
            :class:`ComparisonResult` aggregating all source analyses.

        Raises:
            ClaudeAPIError: If the Claude API call fails.
        """
        if not sources:
            return ComparisonResult(
                package_id=ground_truth.package_id,
                bill_title=ground_truth.title,
                ground_truth_summary=ground_truth.crs_summary,
                ground_truth_date=ground_truth.crs_summary_date,
                source_results=[],
            )

        # Batch sources in groups of 5 to avoid exceeding context limits.
        all_source_results: list[SourceResult] = []
        batch_size = 5

        for batch_start in range(0, len(sources), batch_size):
            batch = sources[batch_start: batch_start + batch_size]
            try:
                per_source = self._claude.get_source_raw_analyses(
                    ground_truth, batch
                )
            except BillAnalyzerError as exc:
                # If a batch fails, create empty results for each source in it.
                for src in batch:
                    all_source_results.append(
                        SourceResult(
                            source=src,
                            discrepancies=[],
                            accuracy_score=0,
                            framing_label="NEUTRAL",
                            raw_analysis=f"Analysis failed: {exc}",
                        )
                    )
                continue

            for src, (discrepancies, score, label, raw) in zip(batch, per_source):
                all_source_results.append(
                    SourceResult(
                        source=src,
                        discrepancies=discrepancies,
                        accuracy_score=score,
                        framing_label=label,
                        raw_analysis=raw,
                    )
                )

        return ComparisonResult(
            package_id=ground_truth.package_id,
            bill_title=ground_truth.title,
            ground_truth_summary=ground_truth.crs_summary,
            ground_truth_date=ground_truth.crs_summary_date,
            source_results=all_source_results,
        )
