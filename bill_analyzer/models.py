"""Dataclasses for structured data returned by the Bill Analyzer."""

from __future__ import annotations

from dataclasses import dataclass, field


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
# Phase 2 — Comparison Engine models
# ---------------------------------------------------------------------------


@dataclass
class CRSSummary:
    """A CRS-authored bill summary fetched from Congress.gov."""

    congress: str
    bill_type: str
    bill_number: str
    action_date: str          # ISO 8601 date the action was taken
    action_description: str   # e.g. "Introduced in House"
    text: str                 # plain text (HTML stripped)
    update_date: str          # ISO 8601 datetime of the last CRS update
    version_code: str         # e.g. "00" = introduced version


@dataclass
class GroundTruth:
    """Authoritative bill representation used as the comparison baseline.

    Combines the raw GovInfo bill text with the non-partisan CRS summary so
    the comparison engine has both the verbatim legislative language and a
    plain-English authoritative description.
    """

    package_id: str
    congress: str
    bill_type: str              # lower-case, e.g. "hr", "s"
    bill_number: str
    title: str
    raw_text: str               # full bill text from GovInfo (HTML stripped)
    crs_summary: str            # CRS-authored plain text from Congress.gov
    crs_summary_date: str       # ISO 8601 date of the CRS summary
    crs_action_description: str # e.g. "Introduced in House"


@dataclass
class RecordSpeech:
    """A single floor speech or article from the Congressional Record."""

    speaker_name: str      # extracted from article text
    bioguide_id: str       # resolved via Congress.gov member lookup
    party: str             # "R" | "D" | "I"
    state: str             # two-letter abbreviation
    chamber: str           # "House" | "Senate"
    date: str              # ISO 8601
    title: str             # article title from the Record
    volume: str            # Congressional Record volume number
    issue: str             # issue number within the volume
    url: str               # canonical API URL for the article
    full_text: str         # verbatim floor speech text


@dataclass
class SourceMaterial:
    """Any external representation of a bill with provenance metadata.

    Wraps floor speeches or news articles so the comparison engine can
    attribute every discrepancy to a specific, citable source.
    """

    source_type: str        # "congressional_record" | "news_article"
    source_name: str        # speaker name or outlet name
    party: str | None       # for congressional_record entries
    date: str
    url: str
    title: str
    full_text: str
    # Congressional Record citation fields — populated for congressional_record
    volume: str = ""        # e.g. "169"
    issue: str = ""         # e.g. "42"
    chamber: str = ""       # "House" | "Senate"


@dataclass
class Discrepancy:
    """A single identified discrepancy between a source and the ground truth."""

    discrepancy_type: str  # "factual" | "framing" | "omission"
    description: str
    confidence: str        # "HIGH" | "MEDIUM" | "LOW"
    bill_reference: str    # quoted passage from bill text or CRS summary
    source_claim: str      # the specific claim made in the source


@dataclass
class SourceResult:
    """Comparison result for a single source material item."""

    source: SourceMaterial
    discrepancies: list[Discrepancy]
    accuracy_score: int    # 0-100
    framing_label: str     # NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE
    raw_analysis: str      # full Claude response for this source


@dataclass
class ComparisonResult:
    """Aggregated comparison of a bill against multiple source materials."""

    package_id: str
    bill_title: str
    ground_truth_summary: str   # CRS summary used as the baseline
    ground_truth_date: str
    source_results: list[SourceResult]
