# Bill Analyzer

## Project Overview

A Python tool that fetches US government bill text from the GovInfo API and
uses the Anthropic Claude API to summarise and analyse legislation in plain
English.

---

## Architecture

```
GovInfoAPIClient  →     ClaudeClient
Fetches raw bill        Summarises &
text from GovInfo       analyses via Claude
        ↓                      ↓
         BillAnalyzer (orchestrator)
         Combines both clients into
         high-level public methods
```

### Package layout

```
bill_analyzer/
├── __init__.py          # Public exports
├── exceptions.py        # BillAnalyzerError, GovInfoAPIError, ClaudeAPIError
├── models.py            # BillMetadata, BillAnalysis (dataclasses)
├── govinfo_client.py    # GovInfoAPIClient
├── claude_client.py     # ClaudeClient
└── analyzer.py          # BillAnalyzer (orchestrator)
main.py                  # CLI entry point (argparse)
requirements.txt         # anthropic, requests
```

---

## Environment Variables

Required — never hard-code keys in source files:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GOVINFO_API_KEY=4fZPpiwJJS24ScVMPVU4ZjgLxe8AZl9GwPzxOn7m
```

Optional overrides:

```bash
export CLAUDE_MODEL="claude-sonnet-4-6"   # default if unset
```

---

## Claude API

- **SDK:** `anthropic` (official Python SDK, `>=0.40.0`)
- **Model:** `claude-sonnet-4-6` as default; overridable via `CLAUDE_MODEL` env var
  or the `model` constructor argument on `ClaudeClient` / `BillAnalyzer`
- Calls are stateless — the full bill text is passed in each request
- All errors are raised as `ClaudeAPIError`
- **Prompt caching** is enabled on both the system prompt and the bill text
  content block (`cache_control: {"type": "ephemeral"}`).  This reduces cost
  and latency when the same bill is queried more than once in a session.
  Cache hits are visible in `response.usage.cache_read_input_tokens`.

### Prompt structure

1. **System prompt** (cached) — stable legislative analyst persona; never
   contains dynamic content so the cache prefix is never invalidated.
2. **User message** (cached) — optional title header + instruction + bill text.
   Marked cacheable because bill texts are large and reused across calls.

---

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

### Package ID format

GovInfo package IDs follow this pattern:

```
BILLS-{congress}{bill_type}{bill_number}{version}
e.g.  BILLS-118hr1234ih   (118th Congress, House Resolution 1234, introduced)
```

---

## CLI Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Full structured analysis
python main.py analyze BILLS-118hr1234ih

# Plain-English summary only
python main.py summarize BILLS-118hr1234ih

# Search and analyse (top N results)
python main.py search "infrastructure" --congress 118 --max-results 3

# Metadata only (no Claude call)
python main.py metadata BILLS-118hr1234ih --json
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
- `ClaudeAPIError` for all Claude / Anthropic SDK failures
- `BillAnalyzerError` as the shared base (caught at the CLI layer)

---

## Out of Scope (current phase)

- Streaming responses
- Conversation / multi-turn history
- Any frontend or UI
- Any non-Anthropic LLM provider
- Bill comparison across multiple package IDs (future work)
