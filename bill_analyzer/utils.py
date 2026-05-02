"""Utility helpers for the Bill Analyzer package."""

import re


class PackageIDParser:
    """Convert between GovInfo package IDs and Congress.gov parameter tuples.

    GovInfo format:   ``BILLS-118hr1234ih``
    Congress.gov:     ``congress=118``, ``bill_type=hr``, ``bill_number=1234``

    The suffix (e.g. ``ih`` — Introduced in House) is captured but discarded
    for Congress.gov lookups, which identify a bill by congress/type/number only.
    """

    _PATTERN: re.Pattern[str] = re.compile(
        r"BILLS-(\d+)([a-z]+)(\d+)([a-z]+)", re.IGNORECASE
    )

    @staticmethod
    def to_congress_gov_params(package_id: str) -> tuple[str, str, str]:
        """Return ``(congress, bill_type, bill_number)`` from a GovInfo package ID.

        Args:
            package_id: GovInfo identifier such as ``BILLS-118hr1234ih``.

        Returns:
            Three-tuple ``(congress, bill_type, bill_number)`` where
            ``bill_type`` is always lower-case.

        Raises:
            ValueError: If *package_id* does not match the expected pattern.
        """
        match = PackageIDParser._PATTERN.match(package_id)
        if not match:
            raise ValueError(
                f"Cannot parse GovInfo package ID: {package_id!r}. "
                "Expected format: BILLS-<congress><type><number><suffix> "
                "(e.g. BILLS-118hr1234ih)."
            )
        congress, bill_type, bill_number, _suffix = match.groups()
        return congress, bill_type.lower(), bill_number

    @staticmethod
    def from_congress_gov_params(
        congress: str, bill_type: str, bill_number: str
    ) -> str:
        """Return a canonical GovInfo package ID root (without version suffix).

        Args:
            congress: Congress number as a string (e.g. ``"118"``).
            bill_type: Bill type in any case (e.g. ``"hr"``, ``"HR"``).
            bill_number: Bill number as a string (e.g. ``"1234"``).

        Returns:
            Root package ID string, e.g. ``"BILLS-118hr1234"``.
        """
        return f"BILLS-{congress}{bill_type.lower()}{bill_number}"
