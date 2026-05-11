Bill Analyzer & Comparison Engine
Project Overview
A Python tool that fetches US government bill text from the GovInfo API, produces plain-English summaries via the Anthropic Claude API, and runs a comparative analysis engine that measures how closely news outlets and politicians represent the legislation against neutral ground truth.

Architecture
GovInfoAPIClient  →     CongressGovClient    CongressionalRecordClient
Fetches raw bill        Fetches CRS-authored  Fetches floor speeches
text from GovInfo       summaries (ground     and debate entries from
        ↓               truth source)         the Congressional Record
        ↓                      ↓                     ↓
         GroundTruth (dataclass)          SourceMaterial (dataclass)
         Combines raw bill text           Wraps statements/articles
         + CRS summary as the            with provenance metadata
         neutral comparison baseline
                  ↓                              ↓
                   ComparisonEngine (orchestrator)
                   Sends ground truth + source material
                   to Claude for discrepancy analysis
                          ↓
                   ComparisonResult (dataclass)
                   Structured discrepancies, scores,
                   framing labels per source
                          ↑
                   BillAnalyzer (top-level orchestrator)
                   Coordinates all clients into
                   high-level public methods
Package layout
bill_analyzer/
├── __init__.py                    # Public exports
├── exceptions.py                  # BillAnalyzerError, GovInfoAPIError,
│                                  # ClaudeAPIError, CongressGovAPIError,
│                                  # CongressionalRecordAPIError
├── models.py                      # BillMetadata, BillAnalysis,
│                                  # CRSSummary, GroundTruth,
│                                  # RecordSpeech, SourceMaterial,
│                                  # Discrepancy, SourceResult,
│                                  # ComparisonResult
├── utils.py                       # PackageIDParser (GovInfo ↔ Congress.gov
│                                  # ID conversion utility)
├── govinfo_client.py              # GovInfoAPIClient
├── congress_gov_client.py         # CongressGovClient              ← NEW
├── congressional_record_client.py # CongressionalRecordClient      ← NEW
├── claude_client.py               # ClaudeClient (summarise + compare)
├── comparison_engine.py           # ComparisonEngine               ← NEW
└── analyzer.py                    # BillAnalyzer (top-level orchestrator)
main.py                            # CLI entry point (argparse)
requirements.txt                   # anthropic, requests
Environment Variables
Required — never hard-code keys in source files:

export ANTHROPIC_API_KEY="sk-ant-..."
export GOVINFO_API_KEY="your-govinfo-key"
export CONGRESS_GOV_API_KEY="your-congress-gov-key"   # register at api.congress.gov/sign-up
Optional overrides:

export CLAUDE_MODEL="claude-sonnet-4-6"   # default if unset
Ground Truth Strategy — CRITICAL
The CRS (Congressional Research Service) summary from Congress.gov is the primary comparison baseline for the comparison engine. It must NOT be replaced by the Claude-generated summary for this purpose.

Reasons:

The CRS is a non-partisan, expert, human-authored source — the only genuinely neutral summary of a bill available programmatically.
Using a Claude-generated summary as the ground truth against which other AI-generated comparisons are measured creates a circular validation problem that undermines the entire engine's credibility.
The LoC summary is also the standard reference cited by journalists and legislators themselves, so it is the most defensible baseline academically.
Rule: Claude is used to analyse discrepancies between the CRS ground truth and source material — it is never the source of truth itself.

GroundTruth dataclass
@dataclass
class GroundTruth:
    package_id: str          # GovInfo ID
    congress: str
    bill_type: str           # e.g. "hr", "s"
    bill_number: str
    title: str
    raw_text: str            # full bill text from GovInfo (stripped HTML)
    crs_summary: str         # CRS-authored plain text from Congress.gov
    crs_summary_date: str    # ISO 8601 date of CRS summary
    crs_action_description: str  # e.g. "Introduced in House"
Package ID Parsing — utils.py
GovInfo and Congress.gov use different ID formats. A PackageIDParser utility handles conversion between them.

GovInfo format:   BILLS-118hr1234ih
Congress.gov:     congress=118, bill_type=hr, bill_number=1234
Parsing logic (regex): BILLS-(\d+)([a-z]+)(\d+)([a-z]+)

class PackageIDParser:
    @staticmethod
    def to_congress_gov_params(package_id: str) -> tuple[str, str, str]:
        """Return (congress, bill_type, bill_number) from a GovInfo package ID."""
        ...

    @staticmethod
    def from_congress_gov_params(congress: str, bill_type: str, bill_number: str) -> str:
        """Return a canonical GovInfo package ID root (without version suffix)."""
        ...
Congress.gov API — CongressGovClient
Base URL: https://api.congress.gov/v3
Auth: api_key query parameter on every request
Rate limit: 5,000 requests per hour
All bill type parameters must be lower case — the API rejects upper case (e.g., use hr not HR, s not S)
Responses are JSON; format=json query param enforces this
Key endpoints
Endpoint	Purpose
GET /bill/{congress}/{billType}/{billNumber}/summaries	CRS-authored summaries list
GET /bill/{congress}/{billType}/{billNumber}	Bill detail and metadata
GET /bill/{congress}/{billType}/{billNumber}/actions	Legislative timeline
GET /summaries/{congress}/{billType}	All summaries for a congress/type
CRS summary response structure
The summaries endpoint returns a list; always take the most recent entry (highest updateDate). The text field contains HTML wrapped in CDATA — strip tags before storing or passing to Claude.

{
  "summaries": [
    {
      "actionDate": "2023-01-09",
      "actionDesc": "Introduced in House",
      "text": "<![CDATA[<p>This bill does...</p>]]>",
      "updateDate": "2023-01-10T12:00:00Z",
      "versionCode": "00"
    }
  ]
}
CongressGovClient public methods
def get_crs_summary(
    self,
    congress: str,
    bill_type: str,
    bill_number: str,
) -> CRSSummary:
    """Fetch the most recent CRS summary for a bill."""

def get_crs_summary_by_package_id(self, package_id: str) -> CRSSummary:
    """Convenience wrapper — parses GovInfo ID then calls get_crs_summary."""
Raises CongressGovAPIError (subclass of BillAnalyzerError) on all failures.

Congressional Record API — CongressionalRecordClient
The Congressional Record is the official verbatim transcript of everything said on the House and Senate floor. It is a US Government work and is entirely public domain — no copyright concerns for storage or display.

Base URL: https://api.congress.gov/v3
Auth: same CONGRESS_GOV_API_KEY used by CongressGovClient — no additional credentials required
Rate limit: shared 5,000 requests per hour with CongressGovClient
Responses: JSON; format=json query param enforces this
Why the Congressional Record over member website scraping
Floor speeches are made at the moment a bill is debated or voted on, meaning they are directly and temporally tied to the legislation. They are attributed, dated, and paginated with a formal citation (volume, issue, page). Full article text is returned directly in the API response — no secondary fetch via newspaper3k is required, unlike scraping member websites which have inconsistent HTML structures across 535 offices.

Key endpoints
Endpoint	Purpose
GET /daily-congressional-record	List issues; filter by y (year)
GET /daily-congressional-record/{volumeNumber}/{issueNumber}	Single issue metadata
GET /daily-congressional-record/{volumeNumber}/{issueNumber}/articles	All articles/speeches in an issue
GET /bound-congressional-record	Bound volumes list
GET /bound-congressional-record/{year}/{month}/{day}	Bound entries for a specific date
Querying by bill
The Congressional Record API does not expose a direct "search by bill number" endpoint. The correct approach is:

Retrieve the bill's action timeline from CongressGovClient to identify key debate and vote dates.
Query daily-congressional-record for those specific dates.
Filter returned articles by chamber and keyword-match the bill number or title within article titles and content.
This date-scoped query strategy keeps request volume low and avoids processing Record issues unrelated to the bill.

Article response structure
{
  "articles": [
    {
      "title": "DISCUSSION OF H.R. 1234",
      "date": "2023-03-15",
      "chamber": "House",
      "part": "House Section",
      "url": "https://api.congress.gov/v3/daily-congressional-record/...",
      "fullText": "Mr. SMITH. Mr. Speaker, I rise today in support of..."
    }
  ]
}
fullText is returned directly — no secondary HTTP fetch needed.

CongressionalRecordClient public methods
def get_speeches_for_bill(
    self,
    congress: str,
    bill_type: str,
    bill_number: str,
    action_dates: list[str],    # ISO 8601 dates from bill actions
    chamber: str | None = None, # "House" | "Senate" | None for both
) -> list[RecordSpeech]:
    """
    Fetch floor speeches referencing this bill.
    Queries daily-congressional-record for each action date,
    filters articles by bill number/title keyword match.
    """

def get_speeches_by_package_id(
    self,
    package_id: str,
    chamber: str | None = None,
) -> list[RecordSpeech]:
    """
    Convenience wrapper — parses GovInfo ID, fetches bill action
    dates from CongressGovClient, then calls get_speeches_for_bill.
    Requires a CongressGovClient instance passed at construction.
    """
Raises CongressionalRecordAPIError (subclass of BillAnalyzerError) on all failures.

RecordSpeech dataclass
@dataclass
class RecordSpeech:
    speaker_name: str        # extracted from article text where possible
    bioguide_id: str         # matched via Congress.gov member lookup
    party: str               # "R" | "D" | "I" — from member lookup
    state: str               # two-letter abbreviation
    chamber: str             # "House" | "Senate"
    date: str                # ISO 8601
    title: str               # article title from the Record
    volume: str              # Congressional Record volume number
    issue: str               # issue number within the volume
    url: str                 # canonical API URL for the article
    full_text: str           # verbatim floor speech text
Speaker extraction note: The daily record articles contain the full text of speeches but do not always return structured speaker metadata separately. Speaker names are typically present at the start of each speech in the format Mr./Ms. SURNAME. — a regex pass on full_text should extract this, followed by a member lookup against the Congress.gov /member endpoint using the surname and chamber to resolve bioguide_id, party, and state. Cache member lookups — there are only ~535 current members and the data changes rarely.

SourceMaterial dataclass
Wraps any external representation of a bill (floor speech or news article) with provenance metadata so the comparison engine can attribute every discrepancy to a specific source.

@dataclass
class SourceMaterial:
    source_type: str         # "congressional_record" | "news_article"
    source_name: str         # speaker name or outlet name
    party: str | None        # for congressional record entries
    date: str
    url: str
    title: str
    full_text: str
    # Congressional Record citation fields — populated for source_type="congressional_record"
    volume: str = ""         # e.g. "169"
    issue: str = ""          # e.g. "42"
    chamber: str = ""        # "House" | "Senate"
ComparisonEngine — comparison_engine.py
The comparison engine coordinates CongressGovClient, CongressionalRecordClient, and ClaudeClient to produce ComparisonResult objects.

CongressionalRecordClient is injected at construction so it can share the same CONGRESS_GOV_API_KEY session and member lookup cache.

Public methods
def compare_floor_speeches(
    self,
    package_id: str,
    chamber: str | None = None,
) -> ComparisonResult:
    """
    1. Build GroundTruth (GovInfo raw text + CRS summary)
    2. Fetch bill action dates from CongressGovClient
    3. Fetch floor speeches for those dates via CongressionalRecordClient
    4. Resolve speaker metadata (party, state) for each speech
    5. Send GroundTruth + speeches to Claude for discrepancy analysis
    6. Return ComparisonResult
    """

def compare_source_materials(
    self,
    ground_truth: GroundTruth,
    sources: list[SourceMaterial],
) -> ComparisonResult:
    """Core comparison — accepts pre-built inputs for flexibility."""
Claude API
SDK: anthropic (official Python SDK, >=0.40.0)
Model: claude-sonnet-4-6 as default; overridable via CLAUDE_MODEL
All errors raised as ClaudeAPIError
Prompt caching enabled on system prompt and bill content blocks
ClaudeClient methods
def summarize_bill(self, bill_text: str, title: str = "") -> str: ...
def analyze_bill(self, bill_text: str, title: str = "") -> BillAnalysis: ...
def compare_to_ground_truth(
    self,
    ground_truth: GroundTruth,
    sources: list[SourceMaterial],
) -> list[Discrepancy]: ...
System prompt — summarisation (existing, stable, cached)
You are an expert legislative analyst specialising in US congressional bills.
Your role is to read the raw text of bills and produce clear, accurate,
plain-English summaries that ordinary citizens can understand.

Guidelines:
- Be objective and factual; avoid political bias.
- Use plain language; define any unavoidable legal or technical terms.
- Focus on what the bill actually does, not what it claims to do.
- Note significant changes from existing law where apparent.
- If the bill text is truncated or unclear, say so explicitly.
System prompt — comparison engine (NEW, stable, cached)
You are an expert legislative fact-checker specialising in US congressional
bills. Your task is to compare how politicians and media outlets represent a
bill against the bill's authoritative Congressional Research Service (CRS)
summary and full legislative text.

Guidelines:
- Treat the CRS summary as the neutral ground truth. It is written by
  non-partisan Congressional Research Service analysts and is the most
  reliable plain-language description of what a bill actually does.
- Do not introduce your own political interpretation. Your role is to
  measure accuracy and framing, not to take sides.
- Distinguish clearly between factual inaccuracy (a claim contradicts the
  bill text or CRS summary) and framing difference (a claim is technically
  accurate but selectively emphasises or omits information).
- When quoting from source material or bill text, cite the specific passage.
- Assign confidence levels to each discrepancy: HIGH, MEDIUM, or LOW.
  HIGH = directly contradicted by the bill text or CRS summary.
  MEDIUM = unsupported or significantly overstated but not directly refuted.
  LOW = selective emphasis or omission that creates a misleading impression.
- Never flag a discrepancy based on political tone alone. Only flag claims
  that can be verified or refuted against the bill text or CRS summary.
Comparison user prompt structure (NEW)
The comparison prompt passes three inputs:

CRS Ground Truth block (cached — large, stable per bill)
Raw bill text block (cached — large, stable per bill)
Source material block (not cached — changes per source)
GROUND_TRUTH_CRS_SUMMARY:
{crs_summary}

BILL_TEXT_EXCERPT:
{bill_text[:8000]}   # truncate to manage context; prioritise provisions sections

SOURCES_TO_ANALYSE:
[SOURCE 1]
Type: {source_type}
Attribution: {source_name} ({party}-{state}) | {chamber} | Cong. Record Vol. {volume}, No. {issue} — {date}
URL: {url}
Text:
{full_text}

[SOURCE 2]
...

Analyse each source against the CRS summary and bill text. For each source
respond using EXACTLY the section headers below (including trailing colon).
Do not add extra headers.

SOURCE_1_ANALYSIS:
FACTUAL_DISCREPANCIES:
- <discrepancy> | CONFIDENCE: HIGH/MEDIUM/LOW | BILL_REF: <quoted passage>
FRAMING_ISSUES:
- <issue> | CONFIDENCE: HIGH/MEDIUM/LOW
OMISSIONS:
- <omission> | CONFIDENCE: HIGH/MEDIUM/LOW
ACCURACY_SCORE: <integer 0-100>
FRAMING_LABEL: NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE

SOURCE_2_ANALYSIS:
...
Discrepancy and ComparisonResult dataclasses
@dataclass
class Discrepancy:
    discrepancy_type: str    # "factual" | "framing" | "omission"
    description: str
    confidence: str          # "HIGH" | "MEDIUM" | "LOW"
    bill_reference: str      # quoted passage from bill text or CRS summary
    source_claim: str        # the specific claim made in the source

@dataclass
class ComparisonResult:
    package_id: str
    bill_title: str
    ground_truth_summary: str        # CRS summary used as baseline
    ground_truth_date: str
    source_results: list[SourceResult]

@dataclass
class SourceResult:
    source: SourceMaterial
    discrepancies: list[Discrepancy]
    accuracy_score: int              # 0-100
    framing_label: str               # NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE
    raw_analysis: str                # full Claude response for this source
CLI Usage
# Existing commands
python main.py analyze BILLS-118hr1234ih
python main.py summarize BILLS-118hr1234ih
python main.py search "infrastructure" --congress 118 --max-results 3
python main.py metadata BILLS-118hr1234ih --json

# New comparison commands
python main.py compare BILLS-118hr1234ih
python main.py compare BILLS-118hr1234ih --sources speeches
python main.py compare BILLS-118hr1234ih --sources speeches --chamber House
python main.py compare BILLS-118hr1234ih --sources articles --json
python main.py ground-truth BILLS-118hr1234ih   # show CRS summary only

python main.py compare-x BILLS-119hr7567ih
All sub-commands accept --model MODEL_ID to override the Claude model.

Coding Standards
Python 3.11+
PEP 8, 88-character line limit
Type hints on every function
Docstrings on every public class and method
Dataclasses for structured data
Never log or print API keys
All external I/O wrapped in try/except with descriptive errors
GovInfoAPIError for all GovInfo failures
CongressGovAPIError for all Congress.gov failures
CongressionalRecordAPIError for all Congressional Record failures
ClaudeAPIError for all Claude / Anthropic SDK failures
BillAnalyzerError as the shared base (caught at the CLI layer)
Out of Scope (current phase)
Streaming responses
Conversation / multi-turn history
Any frontend or UI
Any non-Anthropic LLM provider
Real-time monitoring of news sources
Social media coverage
International legislation outside the United States
Campaign finance cross-referencing (OpenFEC — planned future phase)
Build Order and Integration Constraint — IMPORTANT
The bill summariser (Phase 1) is complete and tested. The comparison engine (Phase 2) must be built as a standalone layer before any integration touches existing files.

Bill Analyzer & Comparison Engine
Project Overview
A Python tool that fetches US government bill text from the GovInfo API, produces plain-English summaries via the Anthropic Claude API, and runs a comparative analysis engine that measures how closely news outlets and politicians represent the legislation against neutral ground truth.

Architecture
GovInfoAPIClient  →     CongressGovClient    CongressionalRecordClient
Fetches raw bill        Fetches CRS-authored  Fetches floor speeches
text from GovInfo       summaries (ground     and debate entries from
        ↓               truth source)         the Congressional Record
        ↓                      ↓                     ↓
         GroundTruth (dataclass)          SourceMaterial (dataclass)
         Combines raw bill text           Wraps statements/articles
         + CRS summary as the            with provenance metadata
         neutral comparison baseline
                  ↓                              ↓
                   ComparisonEngine (orchestrator)
                   Sends ground truth + source material
                   to Claude for discrepancy analysis
                          ↓
                   ComparisonResult (dataclass)
                   Structured discrepancies, scores,
                   framing labels per source
                          ↑
                   BillAnalyzer (top-level orchestrator)
                   Coordinates all clients into
                   high-level public methods
Package layout
bill_analyzer/
├── __init__.py                    # Public exports
├── exceptions.py                  # BillAnalyzerError, GovInfoAPIError,
│                                  # ClaudeAPIError, CongressGovAPIError,
│                                  # CongressionalRecordAPIError
├── models.py                      # BillMetadata, BillAnalysis,
│                                  # CRSSummary, GroundTruth,
│                                  # RecordSpeech, SourceMaterial,
│                                  # Discrepancy, SourceResult,
│                                  # ComparisonResult
├── utils.py                       # PackageIDParser (GovInfo ↔ Congress.gov
│                                  # ID conversion utility)
├── govinfo_client.py              # GovInfoAPIClient
├── congress_gov_client.py         # CongressGovClient              ← NEW
├── congressional_record_client.py # CongressionalRecordClient      ← NEW
├── claude_client.py               # ClaudeClient (summarise + compare)
├── comparison_engine.py           # ComparisonEngine               ← NEW
└── analyzer.py                    # BillAnalyzer (top-level orchestrator)
main.py                            # CLI entry point (argparse)
requirements.txt                   # anthropic, requests
Environment Variables
Required — never hard-code keys in source files:

export ANTHROPIC_API_KEY="sk-ant-..."
export GOVINFO_API_KEY="your-govinfo-key"
export CONGRESS_GOV_API_KEY="your-congress-gov-key"   # register at api.congress.gov/sign-up
Optional overrides:

export CLAUDE_MODEL="claude-sonnet-4-6"   # default if unset
Ground Truth Strategy — CRITICAL
The CRS (Congressional Research Service) summary from Congress.gov is the primary comparison baseline for the comparison engine. It must NOT be replaced by the Claude-generated summary for this purpose.

Reasons:

The CRS is a non-partisan, expert, human-authored source — the only genuinely neutral summary of a bill available programmatically.
Using a Claude-generated summary as the ground truth against which other AI-generated comparisons are measured creates a circular validation problem that undermines the entire engine's credibility.
The LoC summary is also the standard reference cited by journalists and legislators themselves, so it is the most defensible baseline academically.
Rule: Claude is used to analyse discrepancies between the CRS ground truth and source material — it is never the source of truth itself.

GroundTruth dataclass
@dataclass
class GroundTruth:
    package_id: str          # GovInfo ID
    congress: str
    bill_type: str           # e.g. "hr", "s"
    bill_number: str
    title: str
    raw_text: str            # full bill text from GovInfo (stripped HTML)
    crs_summary: str         # CRS-authored plain text from Congress.gov
    crs_summary_date: str    # ISO 8601 date of CRS summary
    crs_action_description: str  # e.g. "Introduced in House"
Package ID Parsing — utils.py
GovInfo and Congress.gov use different ID formats. A PackageIDParser utility handles conversion between them.

GovInfo format:   BILLS-118hr1234ih
Congress.gov:     congress=118, bill_type=hr, bill_number=1234
Parsing logic (regex): BILLS-(\d+)([a-z]+)(\d+)([a-z]+)

class PackageIDParser:
    @staticmethod
    def to_congress_gov_params(package_id: str) -> tuple[str, str, str]:
        """Return (congress, bill_type, bill_number) from a GovInfo package ID."""
        ...

    @staticmethod
    def from_congress_gov_params(congress: str, bill_type: str, bill_number: str) -> str:
        """Return a canonical GovInfo package ID root (without version suffix)."""
        ...
Congress.gov API — CongressGovClient
Base URL: https://api.congress.gov/v3
Auth: api_key query parameter on every request
Rate limit: 5,000 requests per hour
All bill type parameters must be lower case — the API rejects upper case (e.g., use hr not HR, s not S)
Responses are JSON; format=json query param enforces this
Key endpoints
Endpoint	Purpose
GET /bill/{congress}/{billType}/{billNumber}/summaries	CRS-authored summaries list
GET /bill/{congress}/{billType}/{billNumber}	Bill detail and metadata
GET /bill/{congress}/{billType}/{billNumber}/actions	Legislative timeline
GET /summaries/{congress}/{billType}	All summaries for a congress/type
CRS summary response structure
The summaries endpoint returns a list; always take the most recent entry (highest updateDate). The text field contains HTML wrapped in CDATA — strip tags before storing or passing to Claude.

{
  "summaries": [
    {
      "actionDate": "2023-01-09",
      "actionDesc": "Introduced in House",
      "text": "<![CDATA[<p>This bill does...</p>]]>",
      "updateDate": "2023-01-10T12:00:00Z",
      "versionCode": "00"
    }
  ]
}
CongressGovClient public methods
def get_crs_summary(
    self,
    congress: str,
    bill_type: str,
    bill_number: str,
) -> CRSSummary:
    """Fetch the most recent CRS summary for a bill."""

def get_crs_summary_by_package_id(self, package_id: str) -> CRSSummary:
    """Convenience wrapper — parses GovInfo ID then calls get_crs_summary."""
Raises CongressGovAPIError (subclass of BillAnalyzerError) on all failures.

Congressional Record API — CongressionalRecordClient
The Congressional Record is the official verbatim transcript of everything said on the House and Senate floor. It is a US Government work and is entirely public domain — no copyright concerns for storage or display.

Base URL: https://api.congress.gov/v3
Auth: same CONGRESS_GOV_API_KEY used by CongressGovClient — no additional credentials required
Rate limit: shared 5,000 requests per hour with CongressGovClient
Responses: JSON; format=json query param enforces this
Why the Congressional Record over member website scraping
Floor speeches are made at the moment a bill is debated or voted on, meaning they are directly and temporally tied to the legislation. They are attributed, dated, and paginated with a formal citation (volume, issue, page). Full article text is returned directly in the API response — no secondary fetch via newspaper3k is required, unlike scraping member websites which have inconsistent HTML structures across 535 offices.

Key endpoints
Endpoint	Purpose
GET /daily-congressional-record	List issues; filter by y (year)
GET /daily-congressional-record/{volumeNumber}/{issueNumber}	Single issue metadata
GET /daily-congressional-record/{volumeNumber}/{issueNumber}/articles	All articles/speeches in an issue
GET /bound-congressional-record	Bound volumes list
GET /bound-congressional-record/{year}/{month}/{day}	Bound entries for a specific date
Querying by bill
The Congressional Record API does not expose a direct "search by bill number" endpoint. The correct approach is:

Retrieve the bill's action timeline from CongressGovClient to identify key debate and vote dates.
Query daily-congressional-record for those specific dates.
Filter returned articles by chamber and keyword-match the bill number or title within article titles and content.
This date-scoped query strategy keeps request volume low and avoids processing Record issues unrelated to the bill.

Article response structure
{
  "articles": [
    {
      "title": "DISCUSSION OF H.R. 1234",
      "date": "2023-03-15",
      "chamber": "House",
      "part": "House Section",
      "url": "https://api.congress.gov/v3/daily-congressional-record/...",
      "fullText": "Mr. SMITH. Mr. Speaker, I rise today in support of..."
    }
  ]
}
fullText is returned directly — no secondary HTTP fetch needed.

CongressionalRecordClient public methods
def get_speeches_for_bill(
    self,
    congress: str,
    bill_type: str,
    bill_number: str,
    action_dates: list[str],    # ISO 8601 dates from bill actions
    chamber: str | None = None, # "House" | "Senate" | None for both
) -> list[RecordSpeech]:
    """
    Fetch floor speeches referencing this bill.
    Queries daily-congressional-record for each action date,
    filters articles by bill number/title keyword match.
    """

def get_speeches_by_package_id(
    self,
    package_id: str,
    chamber: str | None = None,
) -> list[RecordSpeech]:
    """
    Convenience wrapper — parses GovInfo ID, fetches bill action
    dates from CongressGovClient, then calls get_speeches_for_bill.
    Requires a CongressGovClient instance passed at construction.
    """
Raises CongressionalRecordAPIError (subclass of BillAnalyzerError) on all failures.

RecordSpeech dataclass
@dataclass
class RecordSpeech:
    speaker_name: str        # extracted from article text where possible
    bioguide_id: str         # matched via Congress.gov member lookup
    party: str               # "R" | "D" | "I" — from member lookup
    state: str               # two-letter abbreviation
    chamber: str             # "House" | "Senate"
    date: str                # ISO 8601
    title: str               # article title from the Record
    volume: str              # Congressional Record volume number
    issue: str               # issue number within the volume
    url: str                 # canonical API URL for the article
    full_text: str           # verbatim floor speech text
Speaker extraction note: The daily record articles contain the full text of speeches but do not always return structured speaker metadata separately. Speaker names are typically present at the start of each speech in the format Mr./Ms. SURNAME. — a regex pass on full_text should extract this, followed by a member lookup against the Congress.gov /member endpoint using the surname and chamber to resolve bioguide_id, party, and state. Cache member lookups — there are only ~535 current members and the data changes rarely.

SourceMaterial dataclass
Wraps any external representation of a bill (floor speech or news article) with provenance metadata so the comparison engine can attribute every discrepancy to a specific source.

@dataclass
class SourceMaterial:
    source_type: str         # "congressional_record" | "news_article"
    source_name: str         # speaker name or outlet name
    party: str | None        # for congressional record entries
    date: str
    url: str
    title: str
    full_text: str
    # Congressional Record citation fields — populated for source_type="congressional_record"
    volume: str = ""         # e.g. "169"
    issue: str = ""          # e.g. "42"
    chamber: str = ""        # "House" | "Senate"
ComparisonEngine — comparison_engine.py
The comparison engine coordinates CongressGovClient, CongressionalRecordClient, and ClaudeClient to produce ComparisonResult objects.

CongressionalRecordClient is injected at construction so it can share the same CONGRESS_GOV_API_KEY session and member lookup cache.

Public methods
def compare_floor_speeches(
    self,
    package_id: str,
    chamber: str | None = None,
) -> ComparisonResult:
    """
    1. Build GroundTruth (GovInfo raw text + CRS summary)
    2. Fetch bill action dates from CongressGovClient
    3. Fetch floor speeches for those dates via CongressionalRecordClient
    4. Resolve speaker metadata (party, state) for each speech
    5. Send GroundTruth + speeches to Claude for discrepancy analysis
    6. Return ComparisonResult
    """

def compare_source_materials(
    self,
    ground_truth: GroundTruth,
    sources: list[SourceMaterial],
) -> ComparisonResult:
    """Core comparison — accepts pre-built inputs for flexibility."""
Claude API
SDK: anthropic (official Python SDK, >=0.40.0)
Model: claude-sonnet-4-6 as default; overridable via CLAUDE_MODEL
All errors raised as ClaudeAPIError
Prompt caching enabled on system prompt and bill content blocks
ClaudeClient methods
def summarize_bill(self, bill_text: str, title: str = "") -> str: ...
def analyze_bill(self, bill_text: str, title: str = "") -> BillAnalysis: ...
def compare_to_ground_truth(
    self,
    ground_truth: GroundTruth,
    sources: list[SourceMaterial],
) -> list[Discrepancy]: ...
System prompt — summarisation (existing, stable, cached)
You are an expert legislative analyst specialising in US congressional bills.
Your role is to read the raw text of bills and produce clear, accurate,
plain-English summaries that ordinary citizens can understand.

Guidelines:
- Be objective and factual; avoid political bias.
- Use plain language; define any unavoidable legal or technical terms.
- Focus on what the bill actually does, not what it claims to do.
- Note significant changes from existing law where apparent.
- If the bill text is truncated or unclear, say so explicitly.
System prompt — comparison engine (NEW, stable, cached)
You are an expert legislative fact-checker specialising in US congressional
bills. Your task is to compare how politicians and media outlets represent a
bill against the bill's authoritative Congressional Research Service (CRS)
summary and full legislative text.

Guidelines:
- Treat the CRS summary as the neutral ground truth. It is written by
  non-partisan Congressional Research Service analysts and is the most
  reliable plain-language description of what a bill actually does.
- Do not introduce your own political interpretation. Your role is to
  measure accuracy and framing, not to take sides.
- Distinguish clearly between factual inaccuracy (a claim contradicts the
  bill text or CRS summary) and framing difference (a claim is technically
  accurate but selectively emphasises or omits information).
- When quoting from source material or bill text, cite the specific passage.
- Assign confidence levels to each discrepancy: HIGH, MEDIUM, or LOW.
  HIGH = directly contradicted by the bill text or CRS summary.
  MEDIUM = unsupported or significantly overstated but not directly refuted.
  LOW = selective emphasis or omission that creates a misleading impression.
- Never flag a discrepancy based on political tone alone. Only flag claims
  that can be verified or refuted against the bill text or CRS summary.
Comparison user prompt structure (NEW)
The comparison prompt passes three inputs:

CRS Ground Truth block (cached — large, stable per bill)
Raw bill text block (cached — large, stable per bill)
Source material block (not cached — changes per source)
GROUND_TRUTH_CRS_SUMMARY:
{crs_summary}

BILL_TEXT_EXCERPT:
{bill_text[:8000]}   # truncate to manage context; prioritise provisions sections

SOURCES_TO_ANALYSE:
[SOURCE 1]
Type: {source_type}
Attribution: {source_name} ({party}-{state}) | {chamber} | Cong. Record Vol. {volume}, No. {issue} — {date}
URL: {url}
Text:
{full_text}

[SOURCE 2]
...

Analyse each source against the CRS summary and bill text. For each source
respond using EXACTLY the section headers below (including trailing colon).
Do not add extra headers.

SOURCE_1_ANALYSIS:
FACTUAL_DISCREPANCIES:
- <discrepancy> | CONFIDENCE: HIGH/MEDIUM/LOW | BILL_REF: <quoted passage>
FRAMING_ISSUES:
- <issue> | CONFIDENCE: HIGH/MEDIUM/LOW
OMISSIONS:
- <omission> | CONFIDENCE: HIGH/MEDIUM/LOW
ACCURACY_SCORE: <integer 0-100>
FRAMING_LABEL: NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE

SOURCE_2_ANALYSIS:
...
Discrepancy and ComparisonResult dataclasses
@dataclass
class Discrepancy:
    discrepancy_type: str    # "factual" | "framing" | "omission"
    description: str
    confidence: str          # "HIGH" | "MEDIUM" | "LOW"
    bill_reference: str      # quoted passage from bill text or CRS summary
    source_claim: str        # the specific claim made in the source

@dataclass
class ComparisonResult:
    package_id: str
    bill_title: str
    ground_truth_summary: str        # CRS summary used as baseline
    ground_truth_date: str
    source_results: list[SourceResult]

@dataclass
class SourceResult:
    source: SourceMaterial
    discrepancies: list[Discrepancy]
    accuracy_score: int              # 0-100
    framing_label: str               # NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE
    raw_analysis: str                # full Claude response for this source
CLI Usage
# Existing commands
python main.py analyze BILLS-118hr1234ih
python main.py summarize BILLS-118hr1234ih
python main.py search "infrastructure" --congress 118 --max-results 3
python main.py metadata BILLS-118hr1234ih --json

# New comparison commands
python main.py compare BILLS-118hr1234ih
python main.py compare BILLS-118hr1234ih --sources speeches
python main.py compare BILLS-118hr1234ih --sources speeches --chamber House
python main.py compare BILLS-118hr1234ih --sources articles --json
python main.py ground-truth BILLS-118hr1234ih   # show CRS summary only

python main.py compare-x BILLS-119hr7567ih
All sub-commands accept --model MODEL_ID to override the Claude model.

Coding Standards
Python 3.11+
PEP 8, 88-character line limit
Type hints on every function
Docstrings on every public class and method
Dataclasses for structured data
Never log or print API keys
All external I/O wrapped in try/except with descriptive errors
GovInfoAPIError for all GovInfo failures
CongressGovAPIError for all Congress.gov failures
CongressionalRecordAPIError for all Congressional Record failures
ClaudeAPIError for all Claude / Anthropic SDK failures
BillAnalyzerError as the shared base (caught at the CLI layer)
Out of Scope (current phase)
Streaming responses
Conversation / multi-turn history
Any frontend or UI
Any non-Anthropic LLM provider
Real-time monitoring of news sources
Social media coverage
International legislation outside the United States
Campaign finance cross-referencing (OpenFEC — planned future phase)
Build Order and Integration Constraint — IMPORTANT
The bill summariser (Phase 1) is complete and tested. The comparison engine (Phase 2) must be built as a standalone layer before any integration touches existing files.

Do NOT modify any of the following until explicitly instructed:

bill_analyzer/models.py
bill_analyzer/analyzer.py
bill_analyzer/init.py
main.py
Build and verify these new modules independently first:

utils.py — no dependencies on existing code
congress_gov_client.py — depends only on exceptions.py
congressional_record_client.py — depends on congress_gov_client.py for member lookups and action dates
comparison_engine.py — depends on new clients + existing ClaudeClient
Only after all four pass independent tests should integration into analyzer.py, models.py, init.py, and main.py be attempted.
---

## UI & Concurrency Refactor — app.py

### Objective

Refactor `app.py` so that all `BillAnalyzer` module calls execute
concurrently within a single HTTP request, rather than sequentially.
The frontend should present exactly one input section — the GovInfo
Package ID — and render all results together once every concurrent
task has resolved.

Do NOT introduce async frameworks (e.g. asyncio, aiohttp). Use
`concurrent.futures.ThreadPoolExecutor` only. Flask's WSGI threading
model is compatible with this approach without further configuration.

---

### Backend — app.py

Modify the existing `/api/bill` route only. Do not add new routes,
rename existing ones, or alter the response envelope shape
(`{"result": {...}}`).

Replace the sequential try/except blocks with a `ThreadPoolExecutor`
that submits all tasks in parallel and collects results via
`Future.result()`. Structure:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

@app.route("/api/bill", methods=["POST"])
def api_bill():
    data = request.get_json(force=True)
    package_id = (data.get("package_id") or "").strip()
    model = (data.get("model") or "").strip() or None
    if not package_id:
        return jsonify({"error": "package_id is required"}), 400

    analyzer = _get_analyzer(model)
    result: dict = {}

    def fetch_metadata():
        return analyzer.get_metadata(package_id)

    def fetch_summary():
        return analyzer.summarize_by_package_id(package_id)

    def fetch_analysis():
        return analyzer.analyze_by_package_id(package_id)

    tasks = {
        "metadata": fetch_metadata,
        "summary":  fetch_summary,
        "analysis": fetch_analysis,
    }

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                value = future.result()
                result[key] = asdict(value) if hasattr(value, "__dataclass_fields__") else value
            except BillAnalyzerError as exc:
                result[f"{key}_error"] = str(exc)

    return jsonify({"result": result})
```

Key rules:
- Each task must catch only `BillAnalyzerError` — let unexpected
  exceptions propagate so Flask's error handler surfaces them.
- The `_get_analyzer()` helper is called once before the executor;
  do not instantiate a new `BillAnalyzer` per thread.
- `asdict()` must remain the serialisation method for dataclass
  return types (`BillMetadata`, `BillAnalysis`). The plain string
  return of `summarize_by_package_id` is serialised directly.

---

### Frontend — templates/index.html

The existing form in `index.html` already has one Package ID input
and one optional Model input. Remove the Model input field entirely
to simplify the UI to a single field. The model should default
internally via `CLAUDE_MODEL` env var or the hardcoded fallback in
`_get_analyzer()`.

Updated input section should contain:
- One labelled text input: `id="bill-pkg"`, placeholder
  `"e.g. BILLS-118hr1234ih"`, `autofocus`
- One primary action button that calls `runBill()`
- A spinner indicator tied to the button during the request

The `runBill()` JavaScript function should:
1. Read only `bill-pkg` from the DOM (drop `bill-model` references)
2. POST `{ "package_id": packageId }` to `/api/bill`
3. On success, call `renderBill(data.result)` — no changes to
   `renderBill()` itself; the response shape is unchanged
4. On error, display `data.error` in the result area

Do not modify `renderBill()`, `escHtml()`, or the result-area markup.
The CSS and `style.css` are out of scope for this task.

---

### Files permitted to change

| File | Change |
|---|---|
| `app.py` | Concurrency refactor of `/api/bill` route only |
| `templates/index.html` | Remove model input; simplify `runBill()` JS |

### Files that must not be touched

- `bill_analyzer/analyzer.py`
- `bill_analyzer/models.py`
- `bill_analyzer/__init__.py`
- `bill_analyzer/claude_client.py`
- `bill_analyzer/govinfo_client.py`
- `bill_analyzer/congress_gov_client.py`
- `bill_analyzer/congressional_record_client.py`
- `bill_analyzer/comparison_engine.py`
- `bill_analyzer/exceptions.py`
- `main.py`
- `static/style.css`

---

### Verification steps (run after changes)

1. `python -m pytest tests/` — full test suite must pass without
   modification.
2. Start the app with `python app.py` and submit a known Package ID
   (e.g. `BILLS-118hr1234ih`). Confirm metadata, summary, and
   analysis all appear in a single response.
3. Confirm the wall-clock time of the request is closer to the
   slowest individual task than the sum of all three — this validates
   true concurrency.
4. Introduce a deliberate bad Package ID and confirm the error keys
   (`metadata_error`, `summary_error`, `analysis_error`) still
   appear correctly alongside any successful results.
