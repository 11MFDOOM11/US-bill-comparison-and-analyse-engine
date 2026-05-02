"""Claude API client for bill summarisation, analysis, and comparison."""

import os
import re

import anthropic

from .exceptions import ClaudeAPIError
from .models import BillAnalysis, Discrepancy, GroundTruth, SourceMaterial, SourceResult

# ---------------------------------------------------------------------------
# System prompt — summarisation (stable, cached).
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

# ---------------------------------------------------------------------------
# System prompt — comparison engine (stable, cached).
# ---------------------------------------------------------------------------
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
    """Client that wraps the Anthropic SDK for bill analysis and comparison.

    Uses prompt caching on system prompts and bill content to reduce latency
    and cost when the same bill is queried multiple times.

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
    ) -> list[SourceResult]:
        """Compare source materials against the CRS ground truth via Claude.

        Sends the CRS summary and bill text (both cached) along with all
        source materials (not cached) to Claude for discrepancy analysis.
        Returns one :class:`SourceResult` per source in the same order as
        *sources*.

        The CRS summary and bill text blocks are marked for prompt caching
        because they are large and stable per bill. The source block changes
        with every call and is therefore not cached.

        Args:
            ground_truth: Authoritative bill representation including the
                CRS summary and raw bill text.
            sources: External representations (floor speeches, articles)
                to fact-check against *ground_truth*.

        Returns:
            List of :class:`SourceResult` in the same order as *sources*.

        Raises:
            ClaudeAPIError: If the API call fails.
        """
        sources_block = self._build_sources_block(sources)
        user_messages = self._build_comparison_messages(ground_truth, sources_block)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._cached_comparison_system(),
                messages=[{"role": "user", "content": user_messages}],
            )
        except anthropic.APIError as exc:
            raise ClaudeAPIError(
                f"Claude comparison API request failed: {exc}"
            ) from exc

        raw_text: str = response.content[0].text  # type: ignore[union-attr]
        return self._parse_comparison_response(raw_text, sources)

    # ------------------------------------------------------------------
    # Private helpers — prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _cached_system() -> list[dict]:
        """Return the summarisation system prompt block with cache control."""
        return [
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _cached_comparison_system() -> list[dict]:
        """Return the comparison system prompt block with cache control."""
        return [
            {
                "type": "text",
                "text": _COMPARISON_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _cached_text(text: str) -> list[dict]:
        """Wrap *text* in a content block with ephemeral cache control."""
        return [
            {
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _build_sources_block(sources: list[SourceMaterial]) -> str:
        """Serialise all source materials into the prompt's SOURCES section."""
        lines: list[str] = []
        for i, src in enumerate(sources, 1):
            party_state = ""
            if src.source_type == "congressional_record":
                party = src.party or ""
                state = ""
                chamber = src.chamber or ""
                vol = src.volume or ""
                iss = src.issue or ""
                party_state = (
                    f"({party}-{state}) | {chamber} | "
                    f"Cong. Record Vol. {vol}, No. {iss} — {src.date}"
                )
            else:
                party_state = src.date

            lines.append(f"[SOURCE {i}]")
            lines.append(f"Type: {src.source_type}")
            lines.append(f"Attribution: {src.source_name} {party_state}")
            lines.append(f"URL: {src.url}")
            lines.append("Text:")
            lines.append(src.full_text)
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_comparison_messages(
        ground_truth: GroundTruth,
        sources_block: str,
    ) -> list[dict]:
        """Build the user-turn content blocks for a comparison request.

        The CRS summary and bill text are the large, stable inputs — they are
        marked for prompt caching. The sources block changes per call and is
        left uncached.

        Returns:
            List of content block dicts suitable for the Anthropic messages API.
        """
        n_sources = sources_block.count("[SOURCE ")
        analysis_headers = "\n".join(
            f"SOURCE_{i}_ANALYSIS:\n"
            "FACTUAL_DISCREPANCIES:\n"
            "- <discrepancy> | CONFIDENCE: HIGH/MEDIUM/LOW"
            " | BILL_REF: <quoted passage>\n"
            "FRAMING_ISSUES:\n"
            "- <issue> | CONFIDENCE: HIGH/MEDIUM/LOW\n"
            "OMISSIONS:\n"
            "- <omission> | CONFIDENCE: HIGH/MEDIUM/LOW\n"
            f"ACCURACY_SCORE: <integer 0-100>\n"
            "FRAMING_LABEL: NEUTRAL | LEANS_LEFT | LEANS_RIGHT"
            " | MISLEADING | ACCURATE"
            for i in range(1, n_sources + 1)
        )

        crs_block = (
            "GROUND_TRUTH_CRS_SUMMARY:\n"
            f"{ground_truth.crs_summary}"
        )
        bill_block = (
            "BILL_TEXT_EXCERPT:\n"
            f"{ground_truth.raw_text[:8000]}"
        )
        instruction_block = (
            f"SOURCES_TO_ANALYSE:\n{sources_block}\n"
            "Analyse each source against the CRS summary and bill text. "
            "For each source respond using EXACTLY the section headers below "
            "(including trailing colon). Do not add extra headers.\n\n"
            f"{analysis_headers}"
        )

        return [
            {
                "type": "text",
                "text": crs_block,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": bill_block,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": instruction_block,
                # Sources change per call — do not cache.
            },
        ]

    # ------------------------------------------------------------------
    # Private helpers — response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_comparison_response(
        raw: str,
        sources: list[SourceMaterial],
    ) -> list[SourceResult]:
        """Parse Claude's structured comparison response into SourceResult objects.

        Splits the response on ``SOURCE_N_ANALYSIS:`` markers, then
        extracts discrepancies, accuracy score, and framing label from each
        source block.

        Args:
            raw: Raw text returned by Claude.
            sources: The original source materials in the same order.

        Returns:
            List of :class:`SourceResult`, one per source.
        """
        # Split on SOURCE_N_ANALYSIS: markers.
        source_blocks: list[tuple[int, str]] = []
        pattern = re.compile(r"SOURCE_(\d+)_ANALYSIS:", re.IGNORECASE)
        splits = pattern.split(raw)

        # splits alternates: [preamble, index, block, index, block, ...]
        i = 1
        while i + 1 < len(splits):
            idx = int(splits[i])
            block = splits[i + 1]
            source_blocks.append((idx, block))
            i += 2

        results: list[SourceResult] = []
        for source_idx, (src_num, block) in enumerate(source_blocks):
            src = sources[source_idx] if source_idx < len(sources) else sources[-1]
            discrepancies = ClaudeClient._parse_discrepancies(block)
            accuracy_score = ClaudeClient._parse_accuracy_score(block)
            framing_label = ClaudeClient._parse_framing_label(block)

            results.append(
                SourceResult(
                    source=src,
                    discrepancies=discrepancies,
                    accuracy_score=accuracy_score,
                    framing_label=framing_label,
                    raw_analysis=block.strip(),
                )
            )

        # Pad with empty results if Claude returned fewer blocks than sources.
        while len(results) < len(sources):
            results.append(
                SourceResult(
                    source=sources[len(results)],
                    discrepancies=[],
                    accuracy_score=50,
                    framing_label="NEUTRAL",
                    raw_analysis="",
                )
            )

        return results

    @staticmethod
    def _parse_discrepancies(block: str) -> list[Discrepancy]:
        """Extract all discrepancy bullet points from a source analysis block."""
        discrepancies: list[Discrepancy] = []

        sections = {
            "factual": re.search(
                r"FACTUAL_DISCREPANCIES:(.*?)(?=FRAMING_ISSUES:|OMISSIONS:|"
                r"ACCURACY_SCORE:|$)",
                block,
                re.DOTALL | re.IGNORECASE,
            ),
            "framing": re.search(
                r"FRAMING_ISSUES:(.*?)(?=FACTUAL_DISCREPANCIES:|OMISSIONS:|"
                r"ACCURACY_SCORE:|$)",
                block,
                re.DOTALL | re.IGNORECASE,
            ),
            "omission": re.search(
                r"OMISSIONS:(.*?)(?=FACTUAL_DISCREPANCIES:|FRAMING_ISSUES:|"
                r"ACCURACY_SCORE:|$)",
                block,
                re.DOTALL | re.IGNORECASE,
            ),
        }

        bullet_re = re.compile(r"^[-•]\s+(.+)", re.MULTILINE)
        conf_re = re.compile(r"CONFIDENCE:\s*(HIGH|MEDIUM|LOW)", re.IGNORECASE)
        ref_re = re.compile(r"BILL_REF:\s*(.+)", re.IGNORECASE)

        for dtype, match in sections.items():
            if not match:
                continue
            section_text = match.group(1)
            for bullet in bullet_re.findall(section_text):
                conf_match = conf_re.search(bullet)
                ref_match = ref_re.search(bullet)
                confidence = conf_match.group(1).upper() if conf_match else "LOW"
                bill_ref = ref_match.group(1).strip() if ref_match else ""
                description = re.sub(
                    r"\s*\|\s*CONFIDENCE:.*", "", bullet
                ).strip()
                discrepancies.append(
                    Discrepancy(
                        discrepancy_type=dtype,
                        description=description,
                        confidence=confidence,
                        bill_reference=bill_ref,
                        source_claim=description,
                    )
                )

        return discrepancies

    @staticmethod
    def _parse_accuracy_score(block: str) -> int:
        """Extract the integer ACCURACY_SCORE from a source analysis block."""
        match = re.search(r"ACCURACY_SCORE:\s*(\d+)", block, re.IGNORECASE)
        if match:
            return max(0, min(100, int(match.group(1))))
        return 50

    @staticmethod
    def _parse_framing_label(block: str) -> str:
        """Extract the FRAMING_LABEL from a source analysis block."""
        valid = {"NEUTRAL", "LEANS_LEFT", "LEANS_RIGHT", "MISLEADING", "ACCURATE"}
        match = re.search(
            r"FRAMING_LABEL:\s*(NEUTRAL|LEANS_LEFT|LEANS_RIGHT|MISLEADING|ACCURATE)",
            block,
            re.IGNORECASE,
        )
        if match:
            label = match.group(1).upper()
            return label if label in valid else "NEUTRAL"
        return "NEUTRAL"

    @staticmethod
    def _parse_analysis(raw: str, title: str) -> BillAnalysis:
        """Parse Claude's structured bill analysis into a :class:`BillAnalysis`.

        Args:
            raw: Raw text returned by Claude.
            title: Bill title carried through to the dataclass.

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
                    remainder = stripped[len(header):].strip()
                    if remainder:
                        sections[current].append(remainder)
                    matched = True
                    break
            if not matched and current is not None:
                sections[current].append(line)

        provisions: list[str] = [
            ln.lstrip("-• ").strip()
            for ln in sections["KEY_PROVISIONS"]
            if ln.strip().startswith(("-", "•"))
        ]

        return BillAnalysis(
            package_id="",  # filled in by BillAnalyzer
            title=title,
            plain_english_summary="\n".join(sections["SUMMARY"]).strip() or raw,
            key_provisions=provisions,
            potential_impact="\n".join(sections["POTENTIAL_IMPACT"]).strip(),
            sponsors_and_context="\n".join(sections["CONTEXT"]).strip(),
        )
