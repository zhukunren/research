from __future__ import annotations

import argparse
from collections import Counter

from stock_picker.config import load_settings
from stock_picker.diagnostics import run_preflight
from stock_picker.market import collect_market_data
from stock_picker.output import write_outputs
from stock_picker.reports import load_reports
from stock_picker.screening import build_watchlist
from stock_picker.validation import build_forward_validation, write_forward_validation


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an AI-assisted stock research watchlist.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Read reports and build the current watchlist")
    build.add_argument("--offline", action="store_true", help="Use the local Parquet without calling Tushare")
    build.add_argument("--refresh-ai", action="store_true", help="Refresh cached AI report summaries")
    check = subparsers.add_parser("check", help="Check local inputs and configuration without network requests")
    check.add_argument("--offline", action="store_true", help="Check whether the local Parquet supports an offline build")
    validate = subparsers.add_parser("validate", help="Measure forward returns of saved watchlist snapshots")
    validate.add_argument("--horizons", nargs="+", type=int, help="Trading-day horizons, e.g. 5 20 60")
    args = parser.parse_args()

    settings = load_settings()
    if args.command == "check":
        checks = run_preflight(settings, offline=args.offline)
        for item in checks:
            print(f"[{item['status']}] {item['name']}: {item['detail']}")
        return 1 if any(item["status"] == "ERROR" for item in checks) else 0

    if args.command == "validate":
        result = build_forward_validation(settings, horizons=args.horizons)
        md_path, json_path = write_forward_validation(result, settings["paths"]["output_dir"])
        print(f"History builds: {result['history_build_count']}; matured observations: {result['matured_record_count']}")
        print(f"Markdown: {md_path}")
        print(f"JSON: {json_path}")
        return 0

    reports = load_reports(settings, refresh_ai=args.refresh_ai)
    if not reports:
        print(f"No PDF reports found in {settings['paths']['report_dir']}; existing watchlist was left unchanged.")
        return 2
    market_data = collect_market_data(reports, settings, offline=args.offline)
    result = build_watchlist(reports, market_data, settings)
    md_path, json_path = write_outputs(result, reports, settings["paths"]["output_dir"])

    counts = Counter(candidate["status"] for candidate in result["candidates"])
    report_markets = Counter(report["market"] for report in reports)
    print(f"Scanned {len(reports)} reports: {dict(report_markets)}")
    print(f"Candidates: {dict(counts)}")
    changes = result.get("changes", {})
    if changes.get("available"):
        print(f"Core pool changes: +{len(changes.get('core_entered', []))} / -{len(changes.get('core_exited', []))}")
    print(f"Tushare configured: {result['tushare_configured']}; market data mode: {result['market_data_mode']}")
    if result.get("report_issues"):
        print(f"Report processing issues: {len(result['report_issues'])}; see the Markdown output.")
    print(f"Markdown: {md_path}")
    print(f"JSON: {json_path}")
    if result["data_errors"]:
        print(f"Data issues: {len(result['data_errors'])}; see the Markdown output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
