"""Custom exceptions for the Bill Analyzer package."""


class BillAnalyzerError(Exception):
    """Base exception for all Bill Analyzer errors."""


class GovInfoAPIError(BillAnalyzerError):
    """Raised when the GovInfo API returns an error or is unreachable."""


class ClaudeAPIError(BillAnalyzerError):
    """Raised when the Claude API returns an error or is unreachable."""


class CongressGovAPIError(BillAnalyzerError):
    """Raised when the Congress.gov API returns an error or is unreachable."""


class ProPublicaAPIError(BillAnalyzerError):
    """Raised when the ProPublica Congress API returns an error or is unreachable."""
