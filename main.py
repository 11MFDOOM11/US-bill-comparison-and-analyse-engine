#!/usr/bin/env python3
"""Command-line interface for the Bill Analyzer.

Usage examples
--------------
Analyse a bill by its GovInfo package ID::

    python main.py analyze BILLS-118hr1234ih

Get a plain-English summary::

    python main.py summarize BILLS-118hr1234ih

Search for bills and analyse the top results::

    python main.py search "infrastructure" --congress 118 --max-results 3

Fetch metadata only (no Claude call, no cost)::

    python main.py metadata BILLS-118hr1234ih --json

Show the CRS ground truth for a bill::

    python main.py ground-truth BILLS-118hr1234ih

Compare a bill against Congressional Record floor speeches::

    python main.py compare BILLS-118hr1234ih --sources speeches
    python main.py compare BILLS-118hr1234ih --sources speeches --chamber House
    python main.py compare BILLS-118hr1234ih --sources speeches --json
"""

import argparse
import json
import sys
from dataclasses import asdict

from bill_analyzer import BillAnalyzer
from bill_analyzer.exceptions import BillAnalyzerError


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------

def cmd_analyze(args: argparse.Namespace, analyzer: BillAnalyzer) -> None:
    """Run a full structured analysis and print the result."""
    print(f"Fetching and analysing {args.package_id!r} …", flush=True)
    analysis = analyzer.analyze_by_package_id(args.package_id)

    if args.json:
        print(json.dumps(asdict(analysis), indent=2))
        return

    print(f"\nTitle: {analysis.title}")
    print(f"\nSummary:\n{analysis.plain_english_summary}")

    if analysis.key_provisions:
        print("\nKey Provisions:")
        for provision in analysis.key_provisions:
            print(f"  • {provision}")

    if analysis.potential_impact:
        print(f"\nPotential Impact:\n{analysis.potential_impact}")

    if analysis.sponsors_and_context:
        print(f"\nContext:\n{analysis.sponsors_and_context}")


def cmd_summarize(args: argparse.Namespace, analyzer: BillAnalyzer) -> None:
    """Fetch a bill and print a plain-English summary."""
    print(f"Fetching and summarising {args.package_id!r} …", flush=True)
    summary = analyzer.summarize_by_package_id(args.package_id)
    print(f"\n{summary}")


def cmd_search(args: argparse.Namespace, analyzer: BillAnalyzer) -> None:
    """Search for bills and print brief summaries of each result."""
    print(f"Searching for {args.keyword!r} …", flush=True)
    analyses = analyzer.search_and_analyze(
        keyword=args.keyword,
        congress=args.congress,
        max_results=args.max_results,
    )

    if not analyses:
        print("No results found.")
        return

    for i, analysis in enumerate(analyses, 1):
        print(f"\n{'=' * 60}")
        print(f"Result {i}: {analysis.title}  [{analysis.package_id}]")
        print(f"\nSummary:\n{analysis.plain_english_summary}")
        if analysis.key_provisions:
            print("\nKey Provisions:")
            for provision in analysis.key_provisions[:5]:
                print(f"  • {provision}")
            if len(analysis.key_provisions) > 5:
                extra = len(analysis.key_provisions) - 5
                print(f"  … and {extra} more provision(s)")


def cmd_metadata(args: argparse.Namespace, analyzer: BillAnalyzer) -> None:
    """Fetch and display bill metadata without running an analysis."""
    meta = analyzer.get_metadata(args.package_id)

    if args.json:
        print(json.dumps(asdict(meta), indent=2))
        return

    print(f"Package ID:  {meta.package_id}")
    print(f"Title:       {meta.title}")
    print(f"Congress:    {meta.congress}")
    print(f"Bill Type:   {meta.bill_type or 'N/A'}")
    print(f"Bill Number: {meta.bill_number or 'N/A'}")
    print(f"Date Issued: {meta.date_issued or 'N/A'}")
    if meta.session:
        print(f"Session:     {meta.session}")
    if meta.government_author:
        print(f"Author(s):   {', '.join(meta.government_author)}")


def cmd_ground_truth(args: argparse.Namespace, analyzer: BillAnalyzer) -> None:
    """Fetch and display the CRS ground truth for a bill."""
    print(
        f"Fetching CRS ground truth for {args.package_id!r} …", flush=True
    )
    gt = analyzer.get_ground_truth(args.package_id)

    if args.json:
        print(json.dumps(asdict(gt), indent=2))
        return

    print(f"\nBill:    {gt.title}")
    print(f"Package: {gt.package_id}")
    print(f"Congress/{gt.bill_type.upper()}/{gt.bill_number}")
    print(f"\nCRS Summary ({gt.crs_action_description}, {gt.crs_summary_date}):")
    print(f"\n{gt.crs_summary}")


def cmd_compare(args: argparse.Namespace, analyzer: BillAnalyzer) -> None:
    """Compare a bill against floor speeches or articles and print results."""
    sources_mode: str = args.sources
    chamber: str | None = args.chamber

    print(
        f"Comparing {args.package_id!r} against {sources_mode} …",
        flush=True,
    )

    if sources_mode == "speeches":
        result = analyzer.compare_floor_speeches(
            args.package_id, chamber=chamber
        )
    else:
        print(
            f"Error: unsupported --sources value {sources_mode!r}. "
            "Only 'speeches' is currently supported.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
        return

    print(f"\nBill: {result.bill_title}  [{result.package_id}]")
    print(f"CRS Ground Truth Date: {result.ground_truth_date}")
    print(f"\nCRS Summary:\n{result.ground_truth_summary[:500]}…")

    if not result.source_results:
        print("\nNo source materials found for this bill.")
        return

    for i, sr in enumerate(result.source_results, 1):
        src = sr.source
        print(f"\n{'=' * 60}")
        print(f"Source {i}: {src.source_name}")
        if src.source_type == "congressional_record":
            citation = (
                f"Cong. Record Vol. {src.volume}, No. {src.issue} "
                f"— {src.date} | {src.chamber}"
            )
            print(f"  {citation}")
        else:
            print(f"  {src.date} | {src.url}")

        print(f"  Accuracy Score: {sr.accuracy_score}/100")
        print(f"  Framing Label:  {sr.framing_label}")

        if sr.discrepancies:
            print(f"\n  Discrepancies ({len(sr.discrepancies)}):")
            for disc in sr.discrepancies:
                conf_badge = f"[{disc.confidence}]"
                dtype = disc.discrepancy_type.upper()
                print(f"    {conf_badge} {dtype}: {disc.description}")
                if disc.bill_reference:
                    print(f"      Bill ref: {disc.bill_reference}")
        else:
            print("\n  No discrepancies identified.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="bill-analyzer",
        description=(
            "Fetch US congressional bills from GovInfo and "
            "analyse them with Claude."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--model",
        metavar="MODEL_ID",
        help=(
            "Claude model to use "
            "(default: claude-sonnet-4-6 or $CLAUDE_MODEL)"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- analyze ----
    p_analyze = subparsers.add_parser(
        "analyze",
        help="Full structured analysis of a bill via Claude",
    )
    p_analyze.add_argument(
        "package_id",
        help="GovInfo package ID (e.g. BILLS-118hr1234ih)",
    )
    p_analyze.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    # ---- summarize ----
    p_summarize = subparsers.add_parser(
        "summarize",
        help="Plain-English summary of a bill",
    )
    p_summarize.add_argument(
        "package_id",
        help="GovInfo package ID",
    )

    # ---- search ----
    p_search = subparsers.add_parser(
        "search",
        help="Search bills by keyword and analyse the top results",
    )
    p_search.add_argument("keyword", help="Search keyword or phrase")
    p_search.add_argument(
        "--congress",
        type=int,
        metavar="N",
        help="Filter by congress number (e.g. 118)",
    )
    p_search.add_argument(
        "--max-results",
        type=int,
        default=3,
        metavar="N",
        help="Maximum number of bills to analyse (default: 3)",
    )

    # ---- metadata ----
    p_meta = subparsers.add_parser(
        "metadata",
        help="Fetch bill metadata without running an analysis",
    )
    p_meta.add_argument(
        "package_id",
        help="GovInfo package ID",
    )
    p_meta.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    # ---- ground-truth ----
    p_gt = subparsers.add_parser(
        "ground-truth",
        help="Show the CRS ground truth summary for a bill (no Claude call)",
    )
    p_gt.add_argument(
        "package_id",
        help="GovInfo package ID",
    )
    p_gt.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    # ---- compare ----
    p_compare = subparsers.add_parser(
        "compare",
        help=(
            "Compare a bill against floor speeches or articles "
            "and report discrepancies"
        ),
    )
    p_compare.add_argument(
        "package_id",
        help="GovInfo package ID",
    )
    p_compare.add_argument(
        "--sources",
        choices=["speeches", "articles"],
        default="speeches",
        help="Source type to compare against (default: speeches)",
    )
    p_compare.add_argument(
        "--chamber",
        choices=["House", "Senate"],
        default=None,
        help="Restrict to one chamber (default: both)",
    )
    p_compare.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments, build the analyser, and dispatch to the handler."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        analyzer = BillAnalyzer(model=args.model)
    except BillAnalyzerError as exc:
        print(f"Initialisation error: {exc}", file=sys.stderr)
        sys.exit(1)

    dispatch = {
        "analyze": cmd_analyze,
        "summarize": cmd_summarize,
        "search": cmd_search,
        "metadata": cmd_metadata,
        "ground-truth": cmd_ground_truth,
        "compare": cmd_compare,
    }

    try:
        dispatch[args.command](args, analyzer)
    except BillAnalyzerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
