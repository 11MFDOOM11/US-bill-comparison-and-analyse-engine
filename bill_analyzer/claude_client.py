"""Claude API client for bill summarisation and analysis."""

import os
import re

import anthropic

from .exceptions import ClaudeAPIError
from .models import BillAnalysis, Discrepancy, GroundTruth, SourceMaterial

# ---------------------------------------------------------------------------
# System prompts — kept stable (no dynamic content) to maximise cache hits.
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

_COMPARISON_SYSTEM_PROMPT = """\
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
  that can be verified or refuted against the bill text or CRS summary.\
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

    def compare_to_ground_truth(
        self,
        ground_truth: GroundTruth,
        sources: list[SourceMaterial],
    ) -> list[Discrepancy]:
        """Compare source materials against the CRS ground truth.

        Sends the CRS summary, bill text, and all source materials to Claude
        and parses the structured response into :class:`Discrepancy` objects.

        The CRS summary and bill text blocks are marked for prompt caching
        because they are large and stable per bill.  The source material
        block is not cached because it changes per comparison run.

        Args:
            ground_truth: The neutral baseline containing CRS summary and
                raw bill text.
            sources: List of :class:`SourceMaterial` to analyse.

        Returns:
            Flat list of :class:`Discrepancy` objects across all sources.
            Each discrepancy's ``source_claim`` field identifies which source
            it came from.

        Raises:
            ClaudeAPIError: If the API call fails.
        """
        if not sources:
            return []

        # Build source blocks for the user prompt.
        source_blocks: list[str] = []
        for i, src in enumerate(sources, 1):
            party_info = f" ({src.party})" if src.party else ""
            source_blocks.append(
                f"[SOURCE {i}]\n"
                f"Type: {src.source_type}\n"
                f"Attribution: {src.source_name}{party_info} — {src.date}\n"
                f"URL: {src.url}\n"
                f"Title: {src.title}\n"
                f"Text:\n{src.full_text[:4000]}"
            )

        analysis_headers: list[str] = []
        for i in range(1, len(sources) + 1):
            analysis_headers.append(
                f"SOURCE_{i}_ANALYSIS:\n"
                f"FACTUAL_DISCREPANCIES:\n"
                f"- <discrepancy> | CONFIDENCE: HIGH/MEDIUM/LOW "
                f"| BILL_REF: <quoted passage>\n"
                f"FRAMING_ISSUES:\n"
                f"- <issue> | CONFIDENCE: HIGH/MEDIUM/LOW\n"
                f"OMISSIONS:\n"
                f"- <omission> | CONFIDENCE: HIGH/MEDIUM/LOW\n"
                f"ACCURACY_SCORE: <integer 0-100>\n"
                f"FRAMING_LABEL: "
                f"NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE"
            )

        user_content = (
            "GROUND_TRUTH_CRS_SUMMARY:\n"
            f"{ground_truth.crs_summary}\n\n"
            "BILL_TEXT_EXCERPT:\n"
            f"{ground_truth.raw_text[:8000]}\n\n"
            "SOURCES_TO_ANALYSE:\n"
            + "\n\n".join(source_blocks)
            + "\n\nAnalyse each source against the CRS summary and bill text. "
            "For each source respond using EXACTLY the section headers below "
            "(including trailing colon). Do not add extra headers.\n\n"
            + "\n\n".join(analysis_headers)
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._cached_comparison_system(),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            # CRS summary — cached (stable per bill)
                            {
                                "type": "text",
                                "text": (
                                    "GROUND_TRUTH_CRS_SUMMARY:\n"
                                    f"{ground_truth.crs_summary}\n\n"
                                    "BILL_TEXT_EXCERPT:\n"
                                    f"{ground_truth.raw_text[:8000]}"
                                ),
                                "cache_control": {"type": "ephemeral"},
                            },
                            # Source material — not cached (changes per run)
                            {
                                "type": "text",
                                "text": (
                                    "\n\nSOURCES_TO_ANALYSE:\n"
                                    + "\n\n".join(source_blocks)
                                    + "\n\nAnalyse each source against the CRS "
                                    "summary and bill text. For each source respond "
                                    "using EXACTLY the section headers below "
                                    "(including trailing colon). Do not add extra "
                                    "headers.\n\n"
                                    + "\n\n".join(analysis_headers)
                                ),
                            },
                        ],
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise ClaudeAPIError(
                f"Claude API request failed during comparison: {exc}"
            ) from exc

        raw_text: str = response.content[0].text  # type: ignore[union-attr]
        return self._parse_discrepancies(raw_text, sources)

    def get_source_raw_analyses(
        self,
        ground_truth: GroundTruth,
        sources: list[SourceMaterial],
    ) -> list[tuple[list[Discrepancy], int, str, str]]:
        """Like compare_to_ground_truth but also returns per-source scores/labels.

        Returns a list of tuples, one per source:
        ``(discrepancies, accuracy_score, framing_label, raw_analysis)``

        Raises:
            ClaudeAPIError: If the API call fails.
        """
        if not sources:
            return []

        source_blocks: list[str] = []
        for i, src in enumerate(sources, 1):
            party_info = f" ({src.party})" if src.party else ""
            source_blocks.append(
                f"[SOURCE {i}]\n"
                f"Type: {src.source_type}\n"
                f"Attribution: {src.source_name}{party_info} — {src.date}\n"
                f"URL: {src.url}\n"
                f"Title: {src.title}\n"
                f"Text:\n{src.full_text[:4000]}"
            )

        analysis_headers: list[str] = []
        for i in range(1, len(sources) + 1):
            analysis_headers.append(
                f"SOURCE_{i}_ANALYSIS:\n"
                f"FACTUAL_DISCREPANCIES:\n"
                f"- <discrepancy> | CONFIDENCE: HIGH/MEDIUM/LOW "
                f"| BILL_REF: <quoted passage>\n"
                f"FRAMING_ISSUES:\n"
                f"- <issue> | CONFIDENCE: HIGH/MEDIUM/LOW\n"
                f"OMISSIONS:\n"
                f"- <omission> | CONFIDENCE: HIGH/MEDIUM/LOW\n"
                f"ACCURACY_SCORE: <integer 0-100>\n"
                f"FRAMING_LABEL: "
                f"NEUTRAL | LEANS_LEFT | LEANS_RIGHT | MISLEADING | ACCURATE"
            )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._cached_comparison_system(),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "GROUND_TRUTH_CRS_SUMMARY:\n"
                                    f"{ground_truth.crs_summary}\n\n"
                                    "BILL_TEXT_EXCERPT:\n"
                                    f"{ground_truth.raw_text[:8000]}"
                                ),
                                "cache_control": {"type": "ephemeral"},
                            },
                            {
                                "type": "text",
                                "text": (
                                    "\n\nSOURCES_TO_ANALYSE:\n"
                                    + "\n\n".join(source_blocks)
                                    + "\n\nAnalyse each source against the CRS "
                                    "summary and bill text. For each source respond "
                                    "using EXACTLY the section headers below "
                                    "(including trailing colon). Do not add extra "
                                    "headers.\n\n"
                                    + "\n\n".join(analysis_headers)
                                ),
                            },
                        ],
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise ClaudeAPIError(
                f"Claude API request failed during comparison: {exc}"
            ) from exc

        raw_text: str = response.content[0].text  # type: ignore[union-attr]
        return self._parse_source_results(raw_text, sources)

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

    @staticmethod
    def _cached_comparison_system() -> list[dict]:
        """Return the comparison system prompt block with ephemeral cache control."""
        return [
            {
                "type": "text",
                "text": _COMPARISON_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _parse_discrepancies(
        raw: str,
        sources: list[SourceMaterial],
    ) -> list[Discrepancy]:
        """Parse Claude's structured comparison response into Discrepancy objects.

        Walks the response, splitting on ``SOURCE_N_ANALYSIS:`` headers and
        then extracting bullet items from FACTUAL_DISCREPANCIES, FRAMING_ISSUES,
        and OMISSIONS sections.

        Args:
            raw: Raw text returned by Claude.
            sources: Original source list (used to attribute source_claim).

        Returns:
            Flat list of :class:`Discrepancy` objects.
        """
        discrepancies: list[Discrepancy] = []

        # Split into per-source blocks.
        source_blocks = re.split(r"SOURCE_\d+_ANALYSIS:", raw)
        # source_blocks[0] is any preamble before the first header — skip it.
        for block_idx, block in enumerate(source_blocks[1:], 0):
            source = sources[block_idx] if block_idx < len(sources) else None
            source_name = source.source_name if source else f"Source {block_idx + 1}"

            for dtype, section_header in (
                ("factual", "FACTUAL_DISCREPANCIES:"),
                ("framing", "FRAMING_ISSUES:"),
                ("omission", "OMISSIONS:"),
            ):
                section_text = ClaudeClient._extract_section(
                    block, section_header
                )
                for item in ClaudeClient._parse_bullet_items(section_text):
                    confidence = ClaudeClient._extract_field(
                        item, "CONFIDENCE"
                    )
                    bill_ref = ClaudeClient._extract_field(item, "BILL_REF")
                    # Strip inline fields from the description.
                    description = re.sub(
                        r"\s*\|\s*(?:CONFIDENCE|BILL_REF):[^|]+", "", item
                    ).strip().lstrip("- •").strip()
                    if description:
                        discrepancies.append(
                            Discrepancy(
                                discrepancy_type=dtype,
                                description=description,
                                confidence=confidence or "LOW",
                                bill_reference=bill_ref,
                                source_claim=source_name,
                            )
                        )
        return discrepancies

    @staticmethod
    def _parse_source_results(
        raw: str,
        sources: list[SourceMaterial],
    ) -> list[tuple[list[Discrepancy], int, str, str]]:
        """Parse the full structured response into per-source result tuples.

        Args:
            raw: Raw text returned by Claude.
            sources: Original source list (one per block).

        Returns:
            List of ``(discrepancies, accuracy_score, framing_label, raw_block)``
            tuples, one per source.
        """
        results: list[tuple[list[Discrepancy], int, str, str]] = []

        source_blocks = re.split(r"(SOURCE_\d+_ANALYSIS:)", raw)
        # Reconstruct labelled blocks: [header, body, header, body, ...]
        blocks: list[tuple[str, str]] = []
        i = 1
        while i < len(source_blocks) - 1:
            header = source_blocks[i]
            body = source_blocks[i + 1]
            blocks.append((header, body))
            i += 2

        for block_idx, (_, block) in enumerate(blocks):
            source = sources[block_idx] if block_idx < len(sources) else None
            source_name = source.source_name if source else f"Source {block_idx + 1}"

            discrepancies: list[Discrepancy] = []
            for dtype, section_header in (
                ("factual", "FACTUAL_DISCREPANCIES:"),
                ("framing", "FRAMING_ISSUES:"),
                ("omission", "OMISSIONS:"),
            ):
                section_text = ClaudeClient._extract_section(
                    block, section_header
                )
                for item in ClaudeClient._parse_bullet_items(section_text):
                    confidence = ClaudeClient._extract_field(item, "CONFIDENCE")
                    bill_ref = ClaudeClient._extract_field(item, "BILL_REF")
                    description = re.sub(
                        r"\s*\|\s*(?:CONFIDENCE|BILL_REF):[^|]+", "", item
                    ).strip().lstrip("- •").strip()
                    if description:
                        discrepancies.append(
                            Discrepancy(
                                discrepancy_type=dtype,
                                description=description,
                                confidence=confidence or "LOW",
                                bill_reference=bill_ref,
                                source_claim=source_name,
                            )
                        )

            score_str = ClaudeClient._extract_field(block, "ACCURACY_SCORE")
            try:
                score = max(0, min(100, int(score_str)))
            except (ValueError, TypeError):
                score = 0

            label_raw = ClaudeClient._extract_field(block, "FRAMING_LABEL")
            valid_labels = {
                "NEUTRAL", "LEANS_LEFT", "LEANS_RIGHT", "MISLEADING", "ACCURATE"
            }
            framing_label = label_raw if label_raw in valid_labels else "NEUTRAL"

            results.append((discrepancies, score, framing_label, block.strip()))

        return results

    @staticmethod
    def _extract_section(text: str, header: str) -> str:
        """Return the content under *header* up to the next all-caps header."""
        pattern = re.escape(header) + r"(.*?)(?=\n[A-Z_]+:|$)"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _parse_bullet_items(text: str) -> list[str]:
        """Return non-empty bullet lines from *text*."""
        return [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith(("-", "•")) and len(line.strip()) > 1
        ]

    @staticmethod
    def _extract_field(text: str, field_name: str) -> str:
        """Extract a value like ``FIELD_NAME: value`` from *text*."""
        pattern = rf"(?:^|\|)\s*{re.escape(field_name)}:\s*([^\n|]+)"
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        return match.group(1).strip() if match else ""
