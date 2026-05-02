"""Claude API client for bill summarisation and analysis."""

import os

import anthropic

from .exceptions import ClaudeAPIError
from .models import BillAnalysis

# ---------------------------------------------------------------------------
# System prompt — shared across all requests and eligible for prompt caching.
# Keeping it stable (no dynamic content) maximises cache hits.
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are an expert legislative analyst specialising in US congressional bills.
Your role is to read the raw text of bills and produce clear, accurate,
plain-English summaries that ordinary citizens can understand.

Guidelines:
- Be objective and factual; avoid political bias.
- Use plain language; define any unavoidable legal or technical terms.
- Focus on what the bill actually does, not what it claims to do.
- Note significant changes from existing law where apparent.
- If the bill text is truncated or unclear, say so explicitly.\
"""


class ClaudeClient:
    """Client that wraps the Anthropic SDK for bill analysis tasks.

    Uses prompt caching on the system prompt and bill text to reduce
    latency and cost when the same bill is queried multiple times.

    Args:
        api_key: Anthropic API key. Defaults to the ``ANTHROPIC_API_KEY``
            environment variable.
        model: Claude model ID. Defaults to the ``CLAUDE_MODEL`` environment
            variable, or ``claude-sonnet-4-6`` if unset.

    Raises:
        ClaudeAPIError: If no API key is found or an API call fails.
    """

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ClaudeAPIError(
                "Anthropic API key not found. "
                "Set the ANTHROPIC_API_KEY environment variable."
            )
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self._model = (
            model
            or os.environ.get("CLAUDE_MODEL")
            or self.DEFAULT_MODEL
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def summarize_bill(self, bill_text: str, title: str = "") -> str:
        """Return a plain-English summary of the supplied bill text.

        The system prompt and bill text are marked for prompt caching, which
        reduces cost and latency on repeated calls against the same content.

        Args:
            bill_text: Plain-text content of the bill.
            title: Optional bill title included in the prompt for context.

        Returns:
            Plain-English summary string produced by Claude.

        Raises:
            ClaudeAPIError: If the API call fails.
        """
        header = f"Bill title: {title}\n\n" if title else ""
        user_content = (
            f"{header}"
            "Please provide a comprehensive plain-English summary of this bill. "
            "Cover: what it does, its key provisions, and likely impact on citizens.\n\n"
            f"Bill text:\n{bill_text}"
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=self._cached_system(),
                messages=[
                    {
                        "role": "user",
                        "content": self._cached_text(user_content),
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise ClaudeAPIError(f"Claude API request failed: {exc}") from exc

        return response.content[0].text  # type: ignore[union-attr]

    def analyze_bill(self, bill_text: str, title: str = "") -> BillAnalysis:
        """Return a structured :class:`BillAnalysis` for the supplied bill text.

        Asks Claude to respond in a specific structured format and parses the
        sections into a :class:`BillAnalysis` dataclass.

        Args:
            bill_text: Plain-text content of the bill.
            title: Optional bill title for additional context.

        Returns:
            :class:`BillAnalysis` with parsed sections.

        Raises:
            ClaudeAPIError: If the API call fails.
        """
        header = f"Bill title: {title}\n\n" if title else ""
        user_content = (
            f"{header}"
            "Analyse this bill and respond using **exactly** the section headers below "
            "(including the trailing colon). Do not add extra headers.\n\n"
            "SUMMARY:\n"
            "<2–3 paragraph plain-English summary of what the bill does>\n\n"
            "KEY_PROVISIONS:\n"
            "- <provision 1>\n"
            "- <provision 2>\n"
            "(list every significant provision)\n\n"
            "POTENTIAL_IMPACT:\n"
            "<1–2 paragraphs on how this bill would affect ordinary citizens>\n\n"
            "CONTEXT:\n"
            "<background on the bill, sponsors, or political context if known>\n\n"
            f"Bill text:\n{bill_text}"
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=3000,
                system=self._cached_system(),
                messages=[
                    {
                        "role": "user",
                        "content": self._cached_text(user_content),
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise ClaudeAPIError(f"Claude API request failed: {exc}") from exc

        raw_text: str = response.content[0].text  # type: ignore[union-attr]
        analysis = self._parse_analysis(raw_text, title)
        return analysis

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cached_system() -> list[dict]:
        """Return the system prompt block with ephemeral cache control.

        Marking the system prompt as cacheable reduces cost and latency when
        many bills are processed in sequence (the stable prefix is reused).
        """
        return [
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _cached_text(text: str) -> list[dict]:
        """Wrap *text* in a content block with ephemeral cache control.

        Bill texts can be large; caching them avoids re-tokenising identical
        content when the same bill is analysed more than once per session.
        """
        return [
            {
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _parse_analysis(raw: str, title: str) -> BillAnalysis:
        """Parse Claude's structured response into a :class:`BillAnalysis`.

        Walks the response line-by-line, splitting on the known section
        headers. Falls back gracefully if a section is missing.

        Args:
            raw: Raw text returned by Claude.
            title: Bill title (carried through to the dataclass).

        Returns:
            Populated :class:`BillAnalysis`.
        """
        sections: dict[str, list[str]] = {
            "SUMMARY": [],
            "KEY_PROVISIONS": [],
            "POTENTIAL_IMPACT": [],
            "CONTEXT": [],
        }
        _HEADERS = {
            "SUMMARY:": "SUMMARY",
            "KEY_PROVISIONS:": "KEY_PROVISIONS",
            "POTENTIAL_IMPACT:": "POTENTIAL_IMPACT",
            "CONTEXT:": "CONTEXT",
        }
        current: str | None = None

        for line in raw.splitlines():
            stripped = line.strip()
            matched = False
            for header, key in _HEADERS.items():
                if stripped.upper().startswith(header):
                    current = key
                    # Inline content after the header marker.
                    remainder = stripped[len(header):].strip()
                    if remainder:
                        sections[current].append(remainder)
                    matched = True
                    break
            if not matched and current is not None:
                sections[current].append(line)

        # Parse bullet-list provisions.
        provisions: list[str] = [
            ln.lstrip("-• ").strip()
            for ln in sections["KEY_PROVISIONS"]
            if ln.strip().startswith(("-", "•"))
        ]

        return BillAnalysis(
            package_id="",  # filled in by BillAnalyzer
            title=title,
            plain_english_summary="\n".join(sections["SUMMARY"]).strip()
            or raw,
            key_provisions=provisions,
            potential_impact="\n".join(sections["POTENTIAL_IMPACT"]).strip(),
            sponsors_and_context="\n".join(sections["CONTEXT"]).strip(),
        )
