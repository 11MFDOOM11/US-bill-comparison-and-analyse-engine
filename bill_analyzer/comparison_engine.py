"""Orchestrator that assembles ground truth and drives Claude comparison analysis."""

from .claude_client import ClaudeClient
from .congress_gov_client import CongressGovClient
from .congressional_record_client import CongressionalRecordClient
from .exceptions import BillAnalyzerError
from .govinfo_client import GovInfoAPIClient
from .models import ComparisonResult, GroundTruth, SourceMaterial
from .utils import PackageIDParser


class ComparisonEngine:
    """Coordinate bill comparison against floor speeches and news articles.

    Assembles a :class:`GroundTruth` from GovInfo raw text and the CRS
    summary, fetches source materials (floor speeches or caller-supplied
    articles), and delegates to :class:`ClaudeClient` for discrepancy
    analysis.

    Args:
        govinfo_client: Authenticated :class:`GovInfoAPIClient` instance.
        congress_client: Authenticated :class:`CongressGovClient` instance.
        record_client: Authenticated :class:`CongressionalRecordClient`
            instance; must share the same API key as *congress_client*.
        claude_client: Authenticated :class:`ClaudeClient` instance.
    """

    def __init__(
        self,
        govinfo_client: GovInfoAPIClient,
        congress_client: CongressGovClient,
        record_client: CongressionalRecordClient,
        claude_client: ClaudeClient,
    ) -> None:
        self._govinfo = govinfo_client
        self._congress = congress_client
        self._record = record_client
        self._claude = claude_client

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def compare_floor_speeches(
        self,
        package_id: str,
        chamber: str | None = None,
    ) -> ComparisonResult:
        """Compare a bill against Congressional Record floor speeches.

        Steps:
        1. Build :class:`GroundTruth` from GovInfo text + CRS summary.
        2. Fetch bill action dates from Congress.gov.
        3. Query the Congressional Record for speeches on those dates.
        4. Convert speeches to :class:`SourceMaterial` wrappers.
        5. Send everything to Claude for analysis.
        6. Return a :class:`ComparisonResult`.

        Args:
            package_id: GovInfo package identifier (e.g. ``BILLS-118hr1234ih``).
            chamber: Optional ``"House"`` or ``"Senate"`` filter.

        Returns:
            :class:`ComparisonResult` with per-speech discrepancy analysis.

        Raises:
            BillAnalyzerError: If any upstream API call fails.
        """
        ground_truth = self._build_ground_truth(package_id)

        action_dates = self._congress.get_bill_action_dates(
            ground_truth.congress,
            ground_truth.bill_type,
            ground_truth.bill_number,
        )

        speeches = self._record.get_speeches_for_bill(
            ground_truth.congress,
            ground_truth.bill_type,
            ground_truth.bill_number,
            action_dates,
            chamber,
        )

        sources: list[SourceMaterial] = [
            SourceMaterial(
                source_type="congressional_record",
                source_name=speech.speaker_name,
                party=speech.party or None,
                date=speech.date,
                url=speech.url,
                title=speech.title,
                full_text=speech.full_text,
                volume=speech.volume,
                issue=speech.issue,
                chamber=speech.chamber,
            )
            for speech in speeches
        ]

        return self.compare_source_materials(ground_truth, sources)

    def compare_source_materials(
        self,
        ground_truth: GroundTruth,
        sources: list[SourceMaterial],
    ) -> ComparisonResult:
        """Core comparison — accepts pre-built inputs for flexibility.

        Useful for comparing news articles or other pre-fetched content
        without going through the Congressional Record pipeline.

        Args:
            ground_truth: Authoritative bill representation.
            sources: External representations to compare against *ground_truth*.

        Returns:
            :class:`ComparisonResult` with per-source discrepancy analysis.

        Raises:
            BillAnalyzerError: If the Claude API call fails.
        """
        if not sources:
            return ComparisonResult(
                package_id=ground_truth.package_id,
                bill_title=ground_truth.title,
                ground_truth_summary=ground_truth.crs_summary,
                ground_truth_date=ground_truth.crs_summary_date,
                source_results=[],
            )

        source_results = self._claude.compare_to_ground_truth(
            ground_truth, sources
        )

        return ComparisonResult(
            package_id=ground_truth.package_id,
            bill_title=ground_truth.title,
            ground_truth_summary=ground_truth.crs_summary,
            ground_truth_date=ground_truth.crs_summary_date,
            source_results=source_results,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_ground_truth(self, package_id: str) -> GroundTruth:
        """Assemble a :class:`GroundTruth` from GovInfo and Congress.gov.

        Args:
            package_id: GovInfo package identifier.

        Returns:
            :class:`GroundTruth` combining raw bill text and CRS summary.

        Raises:
            BillAnalyzerError: If any API call fails.
        """
        congress, bill_type, bill_number = PackageIDParser.to_congress_gov_params(
            package_id
        )

        metadata = self._govinfo.get_bill_metadata(package_id)
        raw_text = self._govinfo.get_bill_text(package_id)
        crs = self._congress.get_crs_summary(congress, bill_type, bill_number)

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
