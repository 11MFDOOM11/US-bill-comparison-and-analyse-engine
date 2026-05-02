"""Bill Analyzer — fetch US bills from GovInfo and analyse with Claude."""

from .analyzer import BillAnalyzer
from .claude_client import ClaudeClient
from .comparison_engine import ComparisonEngine
from .congress_gov_client import CongressGovClient
from .exceptions import (
    BillAnalyzerError,
    ClaudeAPIError,
    CongressGovAPIError,
    GovInfoAPIError,
    ProPublicaAPIError,
)
from .govinfo_client import GovInfoAPIClient
from .models import (
    BillAnalysis,
    BillMetadata,
    ComparisonResult,
    CRSSummary,
    Discrepancy,
    GroundTruth,
    PoliticianStatement,
    SourceMaterial,
    SourceResult,
)
from .propublica_client import ProPublicaClient
from .utils import PackageIDParser

__all__ = [
    # Orchestrators
    "BillAnalyzer",
    "ComparisonEngine",
    # Clients
    "ClaudeClient",
    "GovInfoAPIClient",
    "CongressGovClient",
    "ProPublicaClient",
    # Models — Phase 1
    "BillAnalysis",
    "BillMetadata",
    # Models — Phase 2 (comparison engine)
    "CRSSummary",
    "GroundTruth",
    "PoliticianStatement",
    "SourceMaterial",
    "Discrepancy",
    "SourceResult",
    "ComparisonResult",
    # Exceptions
    "BillAnalyzerError",
    "ClaudeAPIError",
    "GovInfoAPIError",
    "CongressGovAPIError",
    "ProPublicaAPIError",
    # Utilities
    "PackageIDParser",
]
