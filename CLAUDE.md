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
```

---

## Environment Setup

```bash
pip install anthropic requests pytest
```

Required environment variables — never hard-code keys in source files:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GOVINFO_API_KEY="..."
```

## Claude API

- **SDK:** `anthropic` (official Python SDK)
- **Model:** `claude-sonnet-4-6` as default; overridable via `CLAUDE_MODEL` env var
  or the `model` constructor argument
- Calls are stateless — pass the full context in each request
- All errors should be raised as `ClaudeAPIError`

---

## GovInfo API

- Base URL: `https://api.govinfo.gov`
- Auth: `api_key` query parameter on every request
- Key endpoints:
  - `GET /packages/{package_id}/summary` — bill metadata
  - `GET /packages/{package_id}/htm` — full bill text
  - `POST /search` — search bills by keyword, congress number, date range
- Implement exponential back-off retry on `429` and `503` responses

---

## Coding Standards

- Python 3.11+
- PEP 8, 88-character line limit
- Type hints on every function
- Docstrings on every public class and method
- Dataclasses for structured data
- Never log or print API keys
- All external I/O wrapped in try/except with descriptive errors



## Out of Scope (current phase)

- Streaming responses
- Conversation / multi-turn history
- Any frontend or UI
- Any non-Anthropic LLM provider
