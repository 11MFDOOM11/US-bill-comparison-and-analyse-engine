"""Dataclasses for structured data returned by the Bill Analyzer."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BillMetadata:
    """Metadata returned from the GovInfo /packages/{id}/summary endpoint."""

    package_id: str
    title: str
    congress: str
    bill_type: str | None = None
    bill_number: str | None = None
    date_issued: str | None = None
    session: str | None = None
    collection_code: str | None = None
    government_author: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [f"[{self.package_id}] {self.title}"]
        if self.congress:
            parts.append(f"Congress: {self.congress}")
        if self.date_issued:
            parts.append(f"Date: {self.date_issued}")
        return " | ".join(parts)


@dataclass
class BillAnalysis:
    """Structured analysis of a US congressional bill produced by Claude."""

    package_id: str
    title: str
    plain_english_summary: str
    key_provisions: list[str]
    potential_impact: str
    sponsors_and_context: str = ""

    def __str__(self) -> str:
        lines = [
            f"Bill: {self.title}",
            f"Package ID: {self.package_id}",
            "",
            "Summary:",
            self.plain_english_summary,
        ]
        if self.key_provisions:
            lines += ["", "Key Provisions:"]
            lines += [f"  • {p}" for p in self.key_provisions]
        if self.potential_impact:
            lines += ["", "Potential Impact:", self.potential_impact]
        if self.sponsors_and_context:
            lines += ["", "Context:", self.sponsors_and_context]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comparison engine dataclasses (Phase 2)
# ---------------------------------------------------------------------------


@dataclass
class CRSSummary:
    """A CRS-authored summary fetched from the Congress.gov API."""

    congress: str
    bill_type: str
    bill_number: str
    action_date: str           # ISO 8601 date of this summary version
    action_description: str    # e.g. "Introduced in House"
    text: str                  # plain text (HTML stripped)
    update_date: str           # ISO 8601 datetime of last update
    version_code: str          # e.g. "00" for introduced version


@dataclass
class GroundTruth:
    """Neutral comparison baseline combining raw bill text and CRS summary.

    The CRS summary is the primary baseline — it is written by non-partisan
    Congressional Research Service analysts and must not be substituted with
    a Claude-generated summary.
    """

    package_id: str
    congress: str
    bill_type: str
    bill_number: str
    title: str
    raw_text: str              # full bill text from GovInfo (HTML stripped)
    crs_summary: str           # CRS-authored plain text from Congress.gov
    crs_summary_date: str      # ISO 8601 date of CRS summary
    crs_action_description: str  # e.g. "Introduced in House"


@dataclass
class PoliticianStatement:
    """A congressional member statement retrieved via the ProPublica API."""

    member_id: str             # ProPublica / Bioguide ID
    member_name: str
    party: str                 # "R", "D", "I"
    state: str
    chamber: str               # "House" or "Senate"
    date: str                  # ISO 8601
    title: str
    url: str                   # source URL — full text fetched from here
    subjects: list[str] = field(default_factory=list)
    full_text: str = ""        # populated after fetching from url


@dataclass
class SourceMaterial:
    """Any external representation of a bill (statement or article).

    Wraps content with provenance metadata so every discrepancy can be
    attributed to a specific named source.
    """

    source_type: str           # "politician_statement" | "news_article"
    source_name: str           # member name or outlet name
    party: str | None          # for politician statements; None for articles
    date: str
    url: str
    title: str
    full_text: str


@dataclass
class Discrepancy:
    """A single factual error, framing issue, or omission identified by Claude."""

    discrepancy_type: str      # "factual" | "framing" | "omission"
    description: str
    confidence: str            # "HIGH" | "MEDIUM" | "LOW"
    bill_reference: str        # quoted passage from bill text or CRS summary
    source_claim: str          # the specific claim made in the source


@dataclass
class SourceResult:
    """Claude's analysis of a single source against the ground truth."""

    source: SourceMaterial
    discrepancies: list[Discrepancy] = field(default_factory=list)
    accuracy_score: int = 0    # 0-100
    framing_label: str = ""    # NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE
    raw_analysis: str = ""     # full Claude response for this source

    def __str__(self) -> str:
        lines = [
            f"Source: {self.source.source_name} ({self.source.source_type})",
            f"Accuracy Score: {self.accuracy_score}/100",
            f"Framing: {self.framing_label}",
        ]
        if self.discrepancies:
            lines += ["", f"Discrepancies ({len(self.discrepancies)}):"]
            for d in self.discrepancies:
                lines.append(
                    f"  [{d.confidence}] {d.discrepancy_type.upper()}: "
                    f"{d.description}"
                )
        return "\n".join(lines)


@dataclass
class ComparisonResult:
    """Aggregated comparison of multiple sources against a bill's ground truth."""

    package_id: str
    bill_title: str
    ground_truth_summary: str        # CRS summary used as baseline
    ground_truth_date: str
    source_results: list[SourceResult] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"Bill: {self.bill_title}",
            f"Package ID: {self.package_id}",
            f"CRS Summary Date: {self.ground_truth_date}",
            f"Sources Analysed: {len(self.source_results)}",
        ]
        for i, result in enumerate(self.source_results, 1):
            lines += [f"\n{'=' * 60}", f"Source {i}:", str(result)]
        return "\n".join(lines)
