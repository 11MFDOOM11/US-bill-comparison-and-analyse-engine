"""Bill Analyzer — fetch US bills from GovInfo and analyse with Claude."""

from .analyzer import BillAnalyzer
from .claude_client import ClaudeClient
from .comparison_engine import ComparisonEngine
from .congress_gov_client import CongressGovClient
from .congressional_record_client import CongressionalRecordClient
from .exceptions import (
    BillAnalyzerError,
    ClaudeAPIError,
    CongressGovAPIError,
    CongressionalRecordAPIError,
    GovInfoAPIError,
)
from .govinfo_client import GovInfoAPIClient
from .models import (
    BillAnalysis,
    BillMetadata,
    ComparisonResult,
    CRSSummary,
    Discrepancy,
    GroundTruth,
    RecordSpeech,
    SourceMaterial,
    SourceResult,
)
from .utils import PackageIDParser

__all__ = [
    # Orchestrators
    "BillAnalyzer",
    "ComparisonEngine",
    # Clients
    "ClaudeClient",
    "CongressGovClient",
    "CongressionalRecordClient",
    "GovInfoAPIClient",
    # Models — Phase 1
    "BillAnalysis",
    "BillMetadata",
    # Models — Phase 2
    "ComparisonResult",
    "CRSSummary",
    "Discrepancy",
    "GroundTruth",
    "RecordSpeech",
    "SourceMaterial",
    "SourceResult",
    # Exceptions
    "BillAnalyzerError",
    "ClaudeAPIError",
    "CongressGovAPIError",
    "CongressionalRecordAPIError",
    "GovInfoAPIError",
    # Utilities
    "PackageIDParser",
]
