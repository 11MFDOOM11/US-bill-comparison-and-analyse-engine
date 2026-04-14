"""Dataclasses for structured data returned by the Bill Analyzer."""

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
