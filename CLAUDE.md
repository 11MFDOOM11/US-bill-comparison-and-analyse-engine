# Bill Analyzer & Comparison Engine

## Project Overview

A Python tool that fetches US government bill text from the GovInfo API,
produces plain-English summaries via the Anthropic Claude API, and runs a
comparative analysis engine that measures how closely news outlets and
politicians represent the legislation against neutral ground truth.

---

## Architecture

```
GovInfoAPIClient  →     CongressGovClient    ProPublicaClient
Fetches raw bill        Fetches CRS-authored  Fetches politician
text from GovInfo       summaries (ground     statements per bill
        ↓               truth source)         from ProPublica
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
```

### Package layout

```
bill_analyzer/
├── __init__.py              # Public exports
├── exceptions.py            # BillAnalyzerError, GovInfoAPIError,
│                            # ClaudeAPIError, CongressGovAPIError,
│                            # ProPublicaAPIError
├── models.py                # BillMetadata, BillAnalysis,
│                            # CRSSummary, GroundTruth,
│                            # PoliticianStatement, SourceMaterial,
│                            # Discrepancy, ComparisonResult
├── utils.py                 # PackageIDParser (GovInfo ↔ Congress.gov
│                            # ID conversion utility)
├── govinfo_client.py        # GovInfoAPIClient
├── congress_gov_client.py   # CongressGovClient  ← NEW
├── propublica_client.py     # ProPublicaClient   ← NEW
├── claude_client.py         # ClaudeClient (summarise + compare)
├── comparison_engine.py     # ComparisonEngine   ← NEW
└── analyzer.py              # BillAnalyzer (top-level orchestrator)
main.py                      # CLI entry point (argparse)
requirements.txt             # anthropic, requests
```

---

## Environment Variables

Required — never hard-code keys in source files:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GOVINFO_API_KEY="your-govinfo-key"
export CONGRESS_GOV_API_KEY="your-congress-gov-key"   # register at api.congress.gov/sign-up
export PROPUBLICA_API_KEY="your-propublica-key"       # request at propublica.org/datastore/api
```

Optional overrides:

```bash
export CLAUDE_MODEL="claude-sonnet-4-6"   # default if unset
```

---

## Ground Truth Strategy — CRITICAL

The CRS (Congressional Research Service) summary from Congress.gov is the
**primary comparison baseline** for the comparison engine. It must NOT be
replaced by the Claude-generated summary for this purpose.

Reasons:
- The CRS is a non-partisan, expert, human-authored source — the only
  genuinely neutral summary of a bill available programmatically.
- Using a Claude-generated summary as the ground truth against which other
  AI-generated comparisons are measured creates a circular validation problem
  that undermines the entire engine's credibility.
- The LoC summary is also the standard reference cited by journalists and
  legislators themselves, so it is the most defensible baseline academically.

Rule: Claude is used to **analyse discrepancies** between the CRS ground truth
and source material — it is never the source of truth itself.

### GroundTruth dataclass

```python
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
```

---

## Package ID Parsing — utils.py

GovInfo and Congress.gov use different ID formats. A `PackageIDParser`
utility handles conversion between them.

```
GovInfo format:   BILLS-118hr1234ih
Congress.gov:     congress=118, bill_type=hr, bill_number=1234
```

Parsing logic (regex): `BILLS-(\d+)([a-z]+)(\d+)([a-z]+)`

```python
class PackageIDParser:
    @staticmethod
    def to_congress_gov_params(package_id: str) -> tuple[str, str, str]:
        """Return (congress, bill_type, bill_number) from a GovInfo package ID."""
        ...

    @staticmethod
    def from_congress_gov_params(congress: str, bill_type: str, bill_number: str) -> str:
        """Return a canonical GovInfo package ID root (without version suffix)."""
        ...
```

---

## Congress.gov API — CongressGovClient

- Base URL: `https://api.congress.gov/v3`
- Auth: `api_key` query parameter on every request
- Rate limit: 5,000 requests per hour
- **All bill type parameters must be lower case** — the API rejects upper case
  (e.g., use `hr` not `HR`, `s` not `S`)
- Responses are JSON; `format=json` query param enforces this

### Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET /bill/{congress}/{billType}/{billNumber}/summaries` | CRS-authored summaries list |
| `GET /bill/{congress}/{billType}/{billNumber}` | Bill detail and metadata |
| `GET /bill/{congress}/{billType}/{billNumber}/actions` | Legislative timeline |
| `GET /summaries/{congress}/{billType}` | All summaries for a congress/type |

### CRS summary response structure

The summaries endpoint returns a list; always take the **most recent entry**
(highest `updateDate`). The `text` field contains HTML wrapped in CDATA —
strip tags before storing or passing to Claude.

```json
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
```

### CongressGovClient public methods

```python
def get_crs_summary(
    self,
    congress: str,
    bill_type: str,
    bill_number: str,
) -> CRSSummary:
    """Fetch the most recent CRS summary for a bill."""

def get_crs_summary_by_package_id(self, package_id: str) -> CRSSummary:
    """Convenience wrapper — parses GovInfo ID then calls get_crs_summary."""
```

Raises `CongressGovAPIError` (subclass of `BillAnalyzerError`) on all failures.

---

## ProPublica Congress API — ProPublicaClient

- Base URL: `https://api.propublica.org/congress/v1`
- Auth: `X-API-Key: {key}` **request header** (not a query parameter)
- Rate limit: 5,000 requests per day
- Responses: JSON

### Key endpoints for the comparison engine

| Endpoint | Purpose |
|---|---|
| `GET /statements/search.json?query={term}` | Search all member statements by keyword (bill name, number, or topic) |
| `GET /members/{bioguide_id}/statements/{congress}.json` | All statements by a specific member in a Congress |
| `GET /{congress}/bills/{bill_slug}/statements.json` | Statements about a specific bill (bill_slug format: `hr1234-118`) |

### Statement response structure

```json
{
  "results": [
    {
      "title": "Rep. Smith Statement on HR 1234",
      "member_id": "S000123",
      "name": "John Smith",
      "chamber": "House",
      "party": "R",
      "state": "TX",
      "date": "2023-02-14",
      "url": "https://smith.house.gov/...",
      "subjects": ["Healthcare", "Budget"]
    }
  ]
}
```

**Important:** The ProPublica API returns statement metadata and a URL — it
does not return full statement text. Full text must be fetched from the
member's `url` field using `requests` + `newspaper3k` (already in scope).
Cache these fetches aggressively to stay within the daily rate limit.

### ProPublicaClient public methods

```python
def search_statements(
    self,
    query: str,
    offset: int = 0,
) -> list[PoliticianStatement]:
    """Search member statements by keyword. Returns metadata + URL."""

def get_statements_for_bill(
    self,
    congress: str,
    bill_slug: str,           # format: "hr1234-118"
) -> list[PoliticianStatement]:
    """Fetch statements specifically tagged to a bill."""

def get_member_statements(
    self,
    bioguide_id: str,
    congress: str,
) -> list[PoliticianStatement]:
    """All statements by a specific member in a Congress."""
```

Raises `ProPublicaAPIError` (subclass of `BillAnalyzerError`) on all failures.

### PoliticianStatement dataclass

```python
@dataclass
class PoliticianStatement:
    member_id: str           # ProPublica / Bioguide ID
    member_name: str
    party: str               # "R", "D", "I"
    state: str
    chamber: str             # "House" or "Senate"
    date: str                # ISO 8601
    title: str
    url: str                 # source URL — fetch full text from here
    subjects: list[str]
    full_text: str = ""      # populated after fetching from url
```

---

## SourceMaterial dataclass

Wraps any external representation of a bill (politician statement or news
article) with provenance metadata so the comparison engine can attribute
every discrepancy to a specific source.

```python
@dataclass
class SourceMaterial:
    source_type: str         # "politician_statement" | "news_article"
    source_name: str         # member name or outlet name
    party: str | None        # for politician statements
    date: str
    url: str
    title: str
    full_text: str
```

---

## ComparisonEngine — comparison_engine.py

The comparison engine coordinates `CongressGovClient`, `ProPublicaClient`,
and `ClaudeClient` to produce `ComparisonResult` objects.

### Public methods

```python
def compare_politician_statements(
    self,
    package_id: str,
    congress: str | None = None,
) -> ComparisonResult:
    """
    1. Build GroundTruth (GovInfo raw text + CRS summary)
    2. Fetch politician statements via ProPublica
    3. Fetch full text for each statement
    4. Send GroundTruth + statements to Claude for discrepancy analysis
    5. Return ComparisonResult
    """

def compare_source_materials(
    self,
    ground_truth: GroundTruth,
    sources: list[SourceMaterial],
) -> ComparisonResult:
    """Core comparison — accepts pre-built inputs for flexibility."""
```

---

## Claude API

- **SDK:** `anthropic` (official Python SDK, `>=0.40.0`)
- **Model:** `claude-sonnet-4-6` as default; overridable via `CLAUDE_MODEL`
- All errors raised as `ClaudeAPIError`
- **Prompt caching** enabled on system prompt and bill content blocks

### ClaudeClient methods

```python
def summarize_bill(self, bill_text: str, title: str = "") -> str: ...
def analyze_bill(self, bill_text: str, title: str = "") -> BillAnalysis: ...
def compare_to_ground_truth(
    self,
    ground_truth: GroundTruth,
    sources: list[SourceMaterial],
) -> list[Discrepancy]: ...
```

### System prompt — summarisation (existing, stable, cached)

```
You are an expert legislative analyst specialising in US congressional bills.
Your role is to read the raw text of bills and produce clear, accurate,
plain-English summaries that ordinary citizens can understand.

Guidelines:
- Be objective and factual; avoid political bias.
- Use plain language; define any unavoidable legal or technical terms.
- Focus on what the bill actually does, not what it claims to do.
- Note significant changes from existing law where apparent.
- If the bill text is truncated or unclear, say so explicitly.
```

### System prompt — comparison engine (NEW, stable, cached)

```
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
```

### Comparison user prompt structure (NEW)

The comparison prompt passes three inputs:

1. **CRS Ground Truth block** (cached — large, stable per bill)
2. **Raw bill text block** (cached — large, stable per bill)
3. **Source material block** (not cached — changes per source)

```
GROUND_TRUTH_CRS_SUMMARY:
{crs_summary}

BILL_TEXT_EXCERPT:
{bill_text[:8000]}   # truncate to manage context; prioritise provisions sections

SOURCES_TO_ANALYSE:
[SOURCE 1]
Type: {source_type}
Attribution: {source_name} ({party}, {state}) — {date}
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
```

### Discrepancy and ComparisonResult dataclasses

```python
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
```

---

## CLI Usage

```bash
# Existing commands
python main.py analyze BILLS-118hr1234ih
python main.py summarize BILLS-118hr1234ih
python main.py search "infrastructure" --congress 118 --max-results 3
python main.py metadata BILLS-118hr1234ih --json

# New comparison commands
python main.py compare BILLS-118hr1234ih
python main.py compare BILLS-118hr1234ih --sources politicians
python main.py compare BILLS-118hr1234ih --sources articles --json
python main.py ground-truth BILLS-118hr1234ih   # show CRS summary only
```

All sub-commands accept `--model MODEL_ID` to override the Claude model.

---

## Coding Standards

- Python 3.11+
- PEP 8, 88-character line limit
- Type hints on every function
- Docstrings on every public class and method
- Dataclasses for structured data
- Never log or print API keys
- All external I/O wrapped in try/except with descriptive errors
- `GovInfoAPIError` for all GovInfo failures
- `CongressGovAPIError` for all Congress.gov failures
- `ProPublicaAPIError` for all ProPublica failures
- `ClaudeAPIError` for all Claude / Anthropic SDK failures
- `BillAnalyzerError` as the shared base (caught at the CLI layer)

---

## Out of Scope (current phase)

- Streaming responses
- Conversation / multi-turn history
- Any frontend or UI
- Any non-Anthropic LLM provider
- Real-time monitoring of news sources
- Social media coverage
- International legislation outside the United States
- Campaign finance cross-referencing (OpenFEC — planned future phase)
## Project Overview

A Python tool that fetches US government bill text from the GovInfo API and
uses the Anthropic Claude API to summarise and analyse legislation in plain
English.

## GovInfo API

- Base URL: `https://api.govinfo.gov`
- Auth: `api_key` query parameter on every request (injected by `_request_with_retry`)
- Key endpoints:
  - `GET /packages/{package_id}/summary` — bill metadata
  - `GET /packages/{package_id}/htm` — full bill HTML (stripped to plain text)
  - `POST /search` — search bills by keyword, congress number, date range
- Exponential back-off retry on `429` and `503` responses
  (up to 4 retries: 1 s → 2 s → 4 s → 8 s delays)
- HTML is stripped via regex in `GovInfoAPIClient._strip_html()`



