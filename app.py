"""Flask web UI for the Bill Analyzer."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from flask import Flask, jsonify, render_template, request

from bill_analyzer import BillAnalyzer
from bill_analyzer.exceptions import BillAnalyzerError, XAPIError
from bill_analyzer.x_client import XClient

app = Flask(__name__)


def _get_analyzer(model: str | None = None) -> BillAnalyzer:
    return BillAnalyzer(model=model or None)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/bill", methods=["POST"])
def api_bill():
    """Run metadata, summary, and analysis concurrently for a single bill."""
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

    def fetch_x_comparison():
        if not os.environ.get("X_BEARER_TOKEN"):
            return {"skipped": True, "reason": "X_BEARER_TOKEN not configured"}
        x_client = XClient()
        engine = analyzer._get_comparison_engine()
        engine._x = x_client
        return engine.compare_x_posts(package_id=package_id, min_engagement=10)

    tasks = {
        "metadata": fetch_metadata,
        "summary": fetch_summary,
        "analysis": fetch_analysis,
        "x_comparison": fetch_x_comparison,
    }

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                value = future.result()
                result[key] = (
                    asdict(value) if hasattr(value, "__dataclass_fields__") else value
                )
            except BillAnalyzerError as exc:
                result[f"{key}_error"] = str(exc)

    return jsonify({"result": result})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(force=True)
    package_id = (data.get("package_id") or "").strip()
    model = (data.get("model") or "").strip() or None
    if not package_id:
        return jsonify({"error": "package_id is required"}), 400
    try:
        analysis = _get_analyzer(model).analyze_by_package_id(package_id)
        return jsonify({"result": asdict(analysis)})
    except BillAnalyzerError as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/summarize", methods=["POST"])
def api_summarize():
    data = request.get_json(force=True)
    package_id = (data.get("package_id") or "").strip()
    model = (data.get("model") or "").strip() or None
    if not package_id:
        return jsonify({"error": "package_id is required"}), 400
    try:
        summary = _get_analyzer(model).summarize_by_package_id(package_id)
        return jsonify({"result": summary})
    except BillAnalyzerError as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(force=True)
    keyword = (data.get("keyword") or "").strip()
    model = (data.get("model") or "").strip() or None
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400

    congress = data.get("congress")
    if congress:
        try:
            congress = int(congress)
        except (ValueError, TypeError):
            return jsonify({"error": "congress must be an integer"}), 400
    else:
        congress = None

    max_results = data.get("max_results", 3)
    try:
        max_results = int(max_results)
    except (ValueError, TypeError):
        max_results = 3
    max_results = max(1, min(max_results, 10))

    date_start = (data.get("date_start") or "").strip() or None
    date_end = (data.get("date_end") or "").strip() or None

    try:
        analyses = _get_analyzer(model).search_and_analyze(
            keyword=keyword,
            congress=congress,
            max_results=max_results,
            date_issued_start_date=date_start,
            date_issued_end_date=date_end,
        )
        return jsonify({"result": [asdict(a) for a in analyses]})
    except BillAnalyzerError as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/metadata", methods=["POST"])
def api_metadata():
    data = request.get_json(force=True)
    package_id = (data.get("package_id") or "").strip()
    if not package_id:
        return jsonify({"error": "package_id is required"}), 400
    try:
        meta = _get_analyzer().get_metadata(package_id)
        return jsonify({"result": asdict(meta)})
    except BillAnalyzerError as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
