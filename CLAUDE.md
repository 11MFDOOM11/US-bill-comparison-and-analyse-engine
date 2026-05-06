# Bill Analyzer & Comparison Engine

## Project Overview

A Python tool that fetches US government bill text from the GovInfo API,
produces plain-English summaries via the Anthropic Claude API, and runs a
comparative analysis engine that measures how closely news outlets, politicians,
and social media represent the legislation against neutral ground truth.

---

## Architecture

```
GovInfoAPIClient  →     CongressGovClient    CongressionalRecordClient    XClient
Fetches raw bill        Fetches CRS-authored  Fetches floor speeches       Fetches posts
text from GovInfo       summaries (ground     and debate entries from       about a bill
        ↓               truth source)         the Congressional Record      from X (Twitter)
        ↓                      ↓                     ↓                           ↓
         GroundTruth (dataclass)                    SourceMaterial (dataclass)
         Combines raw bill text                      Wraps statements/articles/posts
         + CRS summary as the                        with provenance metadata
         neutral comparison baseline
                  ↓                                         ↓
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
├── __init__.py                    # Public exports
├── exceptions.py                  # BillAnalyzerError, GovInfoAPIError,
│                                  # ClaudeAPIError, CongressGovAPIError,
│                                  # CongressionalRecordAPIError,
│                                  # XAPIError                          ← NEW
├── models.py                      # BillMetadata, BillAnalysis,
│                                  # CRSSummary, GroundTruth,
│                                  # RecordSpeech, SourceMaterial,
│                                  # Discrepancy, SourceResult,
│                                  # ComparisonResult,
│                                  # XPost                              ← NEW
├── utils.py                       # PackageIDParser (GovInfo ↔ Congress.gov
│                                  # ID conversion utility)
├── govinfo_client.py              # GovInfoAPIClient
├── congress_gov_client.py         # CongressGovClient
├── congressional_record_client.py # CongressionalRecordClient
├── x_client.py                    # XClient                           ← NEW
├── claude_client.py               # ClaudeClient (summarise + compare)
├── comparison_engine.py           # ComparisonEngine
└── analyzer.py                    # BillAnalyzer (top-level orchestrator)
main.py                            # CLI entry point (argparse)
requirements.txt                   # anthropic, requests, tweepy
```

---

## Environment Variables

Required — never hard-code keys in source files:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GOVINFO_API_KEY="your-govinfo-key"
export CONGRESS_GOV_API_KEY="your-congress-gov-key"   # register at api.congress.gov/sign-up
export X_BEARER_TOKEN="your-x-bearer-token"           # X API v2 app-only auth (NEW)
```

Optional overrides:

```bash
export CLAUDE_MODEL="claude-sonnet-4-6"   # default if unset
export X_MAX_RESULTS="100"                # posts per search page, default 100 (NEW)
export X_MAX_PAGES="5"                    # pagination cap to control costs (NEW)
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

## Congressional Record API — CongressionalRecordClient

The Congressional Record is the official verbatim transcript of everything
said on the House and Senate floor. It is a US Government work and is
entirely public domain — no copyright concerns for storage or display.

Speaker attribution follows the pattern `Mr./Mrs./Ms. SURNAME.` — a regex
pass on `full_text` should extract this, followed by a member lookup against
the Congress.gov `/member` endpoint using the surname and chamber to resolve
`bioguide_id`, `party`, and `state`. Cache member lookups — there are only
~535 current members and the data changes rarely.

---

## X (Twitter) API — XClient  ← NEW PHASE

### Feasibility Assessment

Integrating the X API v2 is **technically feasible** but comes with significant
cost and data volume constraints that must be understood before implementation.

**API access tiers (2025/2026):**

| Tier | Monthly Cost | Post Read Quota | Search Window | Suitable For |
|---|---|---|---|---|
| Free | $0 | ~1,500 posts/month (write-only focus) | None | Not usable for read/search |
| Basic | $100/month | 10,000 posts/month | 7 days recent | Academic prototyping ✓ |
| Pro | $5,000/month | 1,000,000 posts/month | Full archive | Production scale |
| Enterprise | $42,000+/month | Custom | Full archive | Large platforms |

**Recommendation for this project:** The Basic tier at $100/month is the only
realistic option. It provides the `GET /2/tweets/search/recent` endpoint with
a **7-day rolling search window** and a monthly cap of 10,000 posts. Given
that a high-profile bill can generate thousands of posts in a single day,
the search strategy must be highly targeted (see Query Design below) to stay
within budget.

> **Important:** The free tier removed search functionality. Any search-based
> implementation requires at minimum the Basic tier. Factor this into the
> project risk register — this is a recurring cost unlike the one-off APIs
> already in use.

**Authentication:** App-only authentication using a Bearer Token is sufficient
for read-only public post searches. No user OAuth flow is needed.

---

### X API v2 — Key Endpoint

```
GET https://api.twitter.com/2/tweets/search/recent
```

This is the only search endpoint available below the Pro tier. It searches
the most recent 7 days of public posts.

**Required query parameters:**

| Parameter | Description |
|---|---|
| `query` | Search string (supports operators — see Query Design) |
| `max_results` | Posts per page, 10–100 (default 10) |
| `tweet.fields` | Comma-separated fields to include on each post |
| `expansions` | Related objects to expand (e.g. author info) |
| `user.fields` | Fields to include on expanded author objects |
| `next_token` | Pagination cursor — returned in `meta.next_token` |

**Recommended `tweet.fields`:**
```
id,text,created_at,author_id,public_metrics,entities,context_annotations,lang
```

**Recommended `expansions`:**
```
author_id
```

**Recommended `user.fields`:**
```
id,name,username,verified,public_metrics,description
```

**Rate limits (Basic tier):**
- 60 requests per 15-minute window on the search endpoint
- 10,000 posts consumed per calendar month (shared across all search calls)
- HTTP 429 returned when either limit is hit; `Retry-After` header present

---

### Query Design — Bill-Specific Search Strategy

Poorly constructed queries will waste the monthly post quota on irrelevant
content. The following strategy is recommended:

**Primary query structure:**
```
(<bill_short_title> OR <bill_number_variants>) lang:en -is:retweet
```

**Example for a known bill:**
```python
query = (
    '("Big Beautiful Bill" OR "HR 1 2025" OR "HR1" OR "#HR1") '
    'lang:en -is:retweet'
)
```

**Query operators to always include:**
- `lang:en` — filter to English posts only; reduces noise
- `-is:retweet` — exclude retweets; original content only preserves analytical
  value and avoids double-counting identical text
- `-is:reply` — optionally exclude replies to keep to top-level posts

**Query operators for targeted filtering:**
- `from:<username>` — limit to a specific account (e.g. a politician)
- `has:links` — posts containing URLs (often higher quality sources)
- `min_faves:10` — engagement threshold to filter out low-reach noise

**Bill number variants to include in queries:**
The `PackageIDParser` utility already parses GovInfo IDs into congress, type,
and number. Extend it to generate X query variants:

```python
@staticmethod
def to_x_query_variants(package_id: str) -> list[str]:
    """
    Return a list of bill reference strings suitable for X search queries.
    e.g. BILLS-119hr1ih → ["HR 1", "HR1", "H.R. 1", "H.R.1"]
    """
    congress, bill_type, bill_number = PackageIDParser.to_congress_gov_params(package_id)
    type_upper = bill_type.upper()  # "HR", "S", "HJRES", etc.
    return [
        f"{type_upper} {bill_number}",
        f"{type_upper}{bill_number}",
        f"{'.'.join(type_upper)}.{bill_number}",   # "H.R.1"
        f"{'.'.join(type_upper)}. {bill_number}",  # "H.R. 1"
    ]
```

---

### XPost dataclass — models.py addition

```python
@dataclass
class XPost:
    post_id: str
    text: str
    author_id: str
    author_username: str
    author_name: str
    author_verified: bool
    created_at: str                  # ISO 8601
    like_count: int
    retweet_count: int
    reply_count: int
    quote_count: int
    url: str                         # https://x.com/{username}/status/{post_id}
    lang: str                        # "en"
    context_annotations: list[dict]  # X's own topic classification — useful for
                                     # validating that the post is actually about
                                     # the intended bill
```

`XPost` objects are converted to `SourceMaterial` before being passed to the
`ComparisonEngine`. The conversion populates `source_type="social_media"` and
maps engagement metrics into the `title` field as a human-readable label.

```python
def to_source_material(post: XPost) -> SourceMaterial:
    return SourceMaterial(
        source_type="social_media",
        source_name=f"@{post.author_username}",
        party=None,          # cannot be inferred from X data alone
        date=post.created_at,
        url=post.url,
        title=(
            f"X post by @{post.author_username} "
            f"({post.like_count} likes, {post.retweet_count} retweets)"
        ),
        full_text=post.text,
    )
```

---

### XClient — x_client.py

```python
class XClient:
    """Client for the X API v2 recent search endpoint.

    Uses app-only Bearer Token authentication. Suitable for reading public
    posts only — no user context or write operations.

    Args:
        bearer_token: X API v2 Bearer Token. Defaults to ``X_BEARER_TOKEN``.
        max_results: Posts per page (10–100). Defaults to ``X_MAX_RESULTS``
            env var or 100.
        max_pages: Maximum pagination depth per search call. Defaults to
            ``X_MAX_PAGES`` env var or 5. Acts as a cost control guard.

    Raises:
        XAPIError: If the Bearer Token is missing or any API call fails.
    """

    BASE_URL = "https://api.twitter.com/2"

    def __init__(
        self,
        bearer_token: str | None = None,
        max_results: int | None = None,
        max_pages: int | None = None,
    ) -> None: ...

    def search_bill_posts(
        self,
        package_id: str,
        bill_short_title: str | None = None,
        additional_operators: str = "-is:retweet lang:en",
        since_id: str | None = None,
    ) -> list[XPost]:
        """
        Search recent X posts referencing a bill.

        Constructs a query from bill number variants derived from package_id,
        optionally supplemented with bill_short_title. Paginates up to
        max_pages deep and returns a deduplicated list of XPost objects.

        Args:
            package_id: GovInfo bill ID (e.g. "BILLS-119hr1ih").
            bill_short_title: Optional human-readable bill name to include
                in the query (e.g. "Big Beautiful Bill").
            additional_operators: X query operators appended to every query.
                Defaults to "-is:retweet lang:en".
            since_id: If provided, only fetch posts newer than this post ID.
                Useful for incremental fetches without re-consuming quota.

        Returns:
            List of XPost objects, sorted newest-first.

        Raises:
            XAPIError: On authentication failure, rate limit exhaustion, or
                any non-200 response from the X API.
        """

    def _build_query(
        self,
        package_id: str,
        bill_short_title: str | None,
        operators: str,
    ) -> str:
        """Build a well-formed X query string from bill identifiers."""

    def _fetch_page(
        self,
        query: str,
        max_results: int,
        next_token: str | None = None,
    ) -> tuple[list[XPost], str | None]:
        """Fetch a single page; return (posts, next_token or None)."""

    def _parse_post(self, raw: dict, users_by_id: dict) -> XPost:
        """Map a raw API response dict to an XPost dataclass."""
```

---

### Integration into ComparisonEngine

`ComparisonEngine` gains a new public method alongside the existing
`compare_floor_speeches` and `compare_source_materials`:

```python
def compare_x_posts(
    self,
    package_id: str,
    bill_short_title: str | None = None,
    min_engagement: int = 10,
) -> ComparisonResult:
    """
    1. Build GroundTruth (GovInfo raw text + CRS summary)
    2. Fetch recent X posts via XClient.search_bill_posts()
    3. Filter posts below min_engagement threshold (like_count + retweet_count)
       to reduce noise from low-reach accounts
    4. Convert XPost objects to SourceMaterial via to_source_material()
    5. Pass GroundTruth + SourceMaterials to ClaudeClient.compare_to_ground_truth()
    6. Return ComparisonResult

    Args:
        package_id: GovInfo bill ID.
        bill_short_title: Optional short name for the bill, used in the
            X search query.
        min_engagement: Minimum combined likes + retweets a post must have
            to be included in the comparison. Defaults to 10.
    """
```

**Cost control note:** The `min_engagement` filter is not just about quality —
it also reduces the number of posts passed to Claude, directly lowering
Anthropic API costs per comparison run. For a bill generating thousands of
mentions, filtering to posts with `min_engagement >= 50` or higher is advisable.

---

### New Exception — exceptions.py addition

Append to `exceptions.py` without modifying existing classes:

```python
class XAPIError(BillAnalyzerError):
    """Raised on any failure communicating with the X API v2."""
```

---

### New Environment Variable

```bash
export X_BEARER_TOKEN="AAA..."   # App-only Bearer Token from X Developer Portal
```

Obtain via: developer.twitter.com → Project & Apps → Keys and Tokens →
Bearer Token. The Basic tier ($100/month) is the minimum required for search.

---

### CLI Commands — main.py additions

```bash
# Compare X posts about a bill against CRS ground truth
python main.py compare-x BILLS-119hr1ih
python main.py compare-x BILLS-119hr1ih --title "Big Beautiful Bill"
python main.py compare-x BILLS-119hr1ih --min-engagement 50
python main.py compare-x BILLS-119hr1ih --json

# All sub-commands accept --model MODEL_ID to override the Claude model
```

---

### Analytical Value — Why X Data Is Worth the Cost

X posts represent a qualitatively different source type from floor speeches
and news articles. Three properties make them analytically interesting for
this project:

1. **Unmediated political framing** — politicians' own accounts post about
   bills without editorial filtering. A senator's thread about a healthcare
   bill they voted for is a direct data point on how they chose to frame it
   to their constituents versus what the bill actually says.

2. **Speed of framing** — X posts often appear within hours of a bill being
   introduced, before media coverage has consolidated around a narrative.
   Comparing early X framing against CRS summaries (published days later) can
   reveal where the initial misrepresentation originated.

3. **Engagement-weighted reach** — unlike floor speeches (equal weight
   regardless of audience) or news articles (hard to measure reach without
   subscription data), X posts carry public engagement metrics. The comparison
   engine can weight discrepancies by audience reach, flagging a post with
   500,000 impressions more severely than one with 12.

---

### Known Limitations and Mitigations

| Limitation | Impact | Mitigation |
|---|---|---|
| 7-day search window (Basic tier) | Cannot analyse how a bill was framed at introduction if more than 7 days have passed | Run searches immediately after bill introduction; cache results locally |
| 10,000 post/month quota | A single high-profile bill can exhaust the monthly quota | Use `min_engagement` filter; restrict to verified/notable accounts only |
| No party affiliation in API response | Cannot auto-label posts as left/right leaning | Cross-reference `author_username` against known politician account lists (e.g. Congress.gov member data already in use) |
| Posts are short (280 chars) | Limited context for discrepancy analysis | Group posts by author into a single `SourceMaterial.full_text` block rather than treating each post individually |
| Context pollution | Queries may return posts about an unrelated bill with the same number from a different congress | Filter by `context_annotations` where X has classified the post's topic; validate bill number references in text |

---

### Build Order — X Integration

X integration is **Phase 3** and must not begin until the comparison engine
(Phase 2) is fully tested and stable.

```
Phase 2 complete and tested
        ↓
1. exceptions.py — append XAPIError only
2. models.py    — append XPost dataclass and to_source_material() helper only
3. utils.py     — append to_x_query_variants() to PackageIDParser only
4. x_client.py  — build and test independently against live X API
5. comparison_engine.py — add compare_x_posts() method only
6. main.py      — add compare-x sub-command only
```

Do NOT modify `analyzer.py`, `govinfo_client.py`, `congress_gov_client.py`,
`claude_client.py`, or `congressional_record_client.py` during this phase.

---

## SourceMaterial dataclass

Wraps any external representation of a bill (floor speech, news article, or
social media post) with provenance metadata so the comparison engine can
attribute every discrepancy to a specific source.

```python
@dataclass
class SourceMaterial:
    source_type: str         # "congressional_record" | "news_article" | "social_media"
    source_name: str         # speaker name, outlet name, or @username
    party: str | None        # for congressional record entries; None for social media
    date: str
    url: str
    title: str
    full_text: str
    # Congressional Record citation fields — populated for source_type="congressional_record"
    volume: str = ""         # e.g. "169"
    issue: str = ""          # e.g. "42"
    chamber: str = ""        # "House" | "Senate"
```

---

## ComparisonEngine — comparison_engine.py

The comparison engine coordinates `CongressGovClient`,
`CongressionalRecordClient`, `XClient`, and `ClaudeClient` to produce
`ComparisonResult` objects.

### Public methods

```python
def compare_floor_speeches(
    self,
    package_id: str,
    chamber: str | None = None,
) -> ComparisonResult: ...

def compare_source_materials(
    self,
    ground_truth: GroundTruth,
    sources: list[SourceMaterial],
) -> ComparisonResult: ...

def compare_x_posts(                      # ← NEW
    self,
    package_id: str,
    bill_short_title: str | None = None,
    min_engagement: int = 10,
) -> ComparisonResult: ...
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
```

### Source material block (not cached — changes per source)

```
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
- `CongressionalRecordAPIError` for all Congressional Record failures
- `ClaudeAPIError` for all Claude / Anthropic SDK failures
- `XAPIError` for all X API failures
- `BillAnalyzerError` as the shared base (caught at the CLI layer)

---

## CLI Usage

```bash
# Existing commands
python main.py analyze BILLS-118hr1234ih
python main.py summarize BILLS-118hr1234ih
python main.py search "infrastructure" --congress 118 --max-results 3
python main.py metadata BILLS-118hr1234ih --json

# Comparison commands
python main.py compare BILLS-118hr1234ih
python main.py compare BILLS-118hr1234ih --sources speeches
python main.py compare BILLS-118hr1234ih --sources speeches --chamber House
python main.py compare BILLS-118hr1234ih --sources articles --json
python main.py ground-truth BILLS-118hr1234ih   # show CRS summary only

# X (Twitter) comparison commands  ← NEW
python main.py compare-x BILLS-119hr1ih
python main.py compare-x BILLS-119hr1ih --title "Big Beautiful Bill"
python main.py compare-x BILLS-119hr1ih --min-engagement 50
python main.py compare-x BILLS-119hr1ih --json
```

All sub-commands accept `--model MODEL_ID` to override the Claude model.

---

## Out of Scope (current phase)

- Streaming responses
- Conversation / multi-turn history
- Any frontend or UI
- Any non-Anthropic LLM provider
- Real-time monitoring of news sources
- International legislation outside the United States
- Campaign finance cross-referencing (OpenFEC — planned future phase)
- X full archive search (requires Pro tier at $5,000/month — out of budget)
- X streaming / firehose (Enterprise only — out of scope)
- Comprehensive social media coverage beyond X (Instagram, TikTok, Reddit)

---

## Build Order and Integration Constraint — IMPORTANT

The bill summariser (Phase 1) is complete and tested. The comparison engine
(Phase 2) must be built as a standalone layer before any integration touches
existing files. The X integration (Phase 3) must not begin until Phase 2 is
fully stable.

Do NOT modify any of the following until explicitly instructed:
- bill_analyzer/models.py
- bill_analyzer/analyzer.py
- bill_analyzer/__init__.py
- main.py

Build and verify new modules independently first.

**Phase 2 build order:**
1. utils.py               — no dependencies on existing code
2. congress_gov_client.py — depends only on exceptions.py
3. congressional_record_client.py — depends on congress_gov_client.py
                                    for member lookups and action dates
4. comparison_engine.py   — depends on new clients + existing ClaudeClient

**Phase 3 build order (X integration):**
1. exceptions.py          — append XAPIError only
2. models.py              — append XPost + to_source_material() only
3. utils.py               — append to_x_query_variants() to PackageIDParser only
4. x_client.py            — build and test independently
5. comparison_engine.py   — add compare_x_posts() method only
6. main.py                — add compare-x sub-command only

Only after all Phase 3 modules pass independent tests should integration into
`analyzer.py` and `__init__.py` be attempted.

`exceptions.py` is the only existing file that may be touched during Phase 2
or Phase 3 build — append new exception classes only. Do not alter or remove
existing exception classes.
