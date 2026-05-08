"""Flask web UI for the Bill Analyzer."""

import os
from dataclasses import asdict

from flask import Flask, jsonify, render_template, request

from bill_analyzer import BillAnalyzer
from bill_analyzer.exceptions import BillAnalyzerError

app = Flask(__name__)


def _get_analyzer(model: str | None = None) -> BillAnalyzer:
    return BillAnalyzer(model=model or None)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/bill", methods=["POST"])
def api_bill():
    """Run all four modules for a single bill and return results together."""
    data = request.get_json(force=True)
    package_id = (data.get("package_id") or "").strip()
    model = (data.get("model") or "").strip() or None
    if not package_id:
        return jsonify({"error": "package_id is required"}), 400

    analyzer = _get_analyzer(model)
    result: dict = {}

    try:
        meta = analyzer.get_metadata(package_id)
        result["metadata"] = asdict(meta)
    except BillAnalyzerError as exc:
        result["metadata_error"] = str(exc)

    try:
        summary = analyzer.summarize_by_package_id(package_id)
        result["summary"] = summary
    except BillAnalyzerError as exc:
        result["summary_error"] = str(exc)

    try:
        analysis = analyzer.analyze_by_package_id(package_id)
        result["analysis"] = asdict(analysis)
    except BillAnalyzerError as exc:
        result["analysis_error"] = str(exc)

    return jsonify({"result": result})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
