"""Utility helpers for the Bill Analyzer package."""

import re


# GovInfo package IDs follow the pattern: BILLS-{congress}{billType}{billNumber}{version}
# Example: BILLS-118hr1234ih  →  congress=118, bill_type=hr, bill_number=1234
_PACKAGE_ID_RE = re.compile(
    r"BILLS-(\d+)([a-z]+)(\d+)([a-z]+)", re.IGNORECASE
)


class PackageIDParser:
    """Convert between GovInfo package IDs and Congress.gov bill parameters.

    GovInfo uses a combined string like ``BILLS-118hr1234ih``, while the
    Congress.gov API requires separate ``congress``, ``billType``, and
    ``billNumber`` parameters.

    Examples::

        congress, bill_type, bill_number = PackageIDParser.to_congress_gov_params(
            "BILLS-118hr1234ih"
        )
        # ("118", "hr", "1234")

        package_id = PackageIDParser.from_congress_gov_params("118", "hr", "1234")
        # "BILLS-118hr1234"
    """

    @staticmethod
    def to_congress_gov_params(package_id: str) -> tuple[str, str, str]:
        """Extract (congress, bill_type, bill_number) from a GovInfo package ID.

        Args:
            package_id: GovInfo package ID, e.g. ``BILLS-118hr1234ih``.

        Returns:
            Three-tuple ``(congress, bill_type, bill_number)`` where
            ``bill_type`` is lower-cased (required by the Congress.gov API).

        Raises:
            ValueError: If *package_id* does not match the expected format.
        """
        match = _PACKAGE_ID_RE.search(package_id)
        if not match:
            raise ValueError(
                f"Cannot parse GovInfo package ID {package_id!r}. "
                "Expected format: BILLS-{{congress}}{{billType}}{{billNumber}}{{version}}, "
                "e.g. BILLS-118hr1234ih"
            )
        congress = match.group(1)
        bill_type = match.group(2).lower()
        bill_number = match.group(3)
        return congress, bill_type, bill_number

    @staticmethod
    def from_congress_gov_params(
        congress: str,
        bill_type: str,
        bill_number: str,
    ) -> str:
        """Build a canonical GovInfo package ID root from Congress.gov parameters.

        The version suffix (e.g. ``ih``, ``enr``) is omitted because it varies
        across bill stages; callers should append it when needed.

        Args:
            congress: Congress number, e.g. ``"118"``.
            bill_type: Bill type, e.g. ``"hr"`` or ``"s"``.
            bill_number: Bill number, e.g. ``"1234"``.

        Returns:
            Package ID root string, e.g. ``"BILLS-118hr1234"``.
        """
        return f"BILLS-{congress}{bill_type.lower()}{bill_number}"
