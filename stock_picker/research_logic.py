from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import isfinite


def _num(value):
    try:
        if value is None:
            return None
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _report_sort_key(report: dict) -> tuple[str, str]:
    return str(report.get("report_date") or ""), str(report.get("file") or "")


def merge_report_summaries(reports: list[dict]) -> dict:
    """Merge evidence across reports while keeping the latest narrative.

    Verified structured evidence is allowed to survive into newer builds even when
    the newest report does not repeat it. For forecasts with the same year, the
    newest report wins.
    """
    if not reports:
        return {}

    ordered = sorted(reports, key=_report_sort_key)
    latest_summary = ordered[-1].get("summary", {}) or {}
    merged = deepcopy(latest_summary)

    verified_order = None
    verified_financial = None
    forecasts_by_year: dict[int, dict] = {}
    evidence_quotes: list[dict] = []
    seen_quotes: set[tuple[str, int | None, str]] = set()

    for report in ordered:
        summary = report.get("summary", {}) or {}
        source_meta = {
            "source_file": report.get("file"),
            "source_report_date": report.get("report_date"),
        }

        order = summary.get("orders", {}) or {}
        if order.get("confirmed") and order.get("source_verified") is True:
            verified_order = {**deepcopy(order), **source_meta}

        financial = summary.get("financial_evidence", {}) or {}
        if (
            financial.get("confirmed")
            and financial.get("fact_type") == "reported_actual"
            and financial.get("source_verified") is True
        ):
            verified_financial = {**deepcopy(financial), **source_meta}

        for item in summary.get("forecasts", []) or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip().lower() != "forecast":
                continue
            if item.get("eps_source_verified") is not True:
                continue
            try:
                year = int(item.get("year"))
            except (TypeError, ValueError):
                continue
            forecasts_by_year[year] = {**deepcopy(item), **source_meta}

        for item in summary.get("evidence_quotes", []) or []:
            if not isinstance(item, dict) or item.get("source_verified") is not True:
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote:
                continue
            key = (quote, item.get("page"), str(item.get("topic") or ""))
            if key in seen_quotes:
                continue
            seen_quotes.add(key)
            evidence_quotes.append({**deepcopy(item), **source_meta})

    if verified_order is not None:
        merged["orders"] = verified_order
    if verified_financial is not None:
        merged["financial_evidence"] = verified_financial
    if forecasts_by_year:
        merged["forecasts"] = [forecasts_by_year[year] for year in sorted(forecasts_by_year)]
    if evidence_quotes:
        merged["evidence_quotes"] = evidence_quotes[-12:]

    risks: list[str] = []
    for report in reversed(ordered):
        for risk in (report.get("summary", {}) or {}).get("risks", []) or []:
            text = str(risk).strip()
            if text and text not in risks:
                risks.append(text)
    if risks:
        merged["risks"] = risks[:12]

    caveats: list[str] = []
    for report in reversed(ordered):
        for caveat in (report.get("summary", {}) or {}).get("source_caveats", []) or []:
            text = str(caveat).strip()
            if text and text not in caveats:
                caveats.append(text)
    if caveats:
        merged["source_caveats"] = caveats[:12]

    merged["merged_report_count"] = len(ordered)
    merged["latest_report_file"] = ordered[-1].get("file")
    merged["latest_report_date"] = ordered[-1].get("report_date")
    return merged


def normalized_forward_eps_growth(
    summary: dict,
    as_of: date,
    min_span_years: int = 2,
    max_span_years: int = 3,
) -> dict:
    """Calculate normalized EPS CAGR over the widest allowed verified forecast span."""
    by_year: dict[int, float] = {}
    for item in summary.get("forecasts", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).strip().lower() != "forecast":
            continue
        if item.get("eps_source_verified") is not True:
            continue
        try:
            year = int(item.get("year"))
        except (TypeError, ValueError):
            continue
        eps = _num(item.get("eps"))
        if eps is not None and eps > 0:
            by_year[year] = eps

    forecasts = sorted(by_year.items())
    if len(forecasts) < 2:
        return {"status": "missing_forecast", "reason": "verified_forecast_count"}

    eligible_starts = [entry for entry in forecasts if entry[0] >= as_of.year]
    if not eligible_starts:
        return {"status": "missing_forecast", "reason": "no_current_or_future_forecast"}

    start_year, start_eps = eligible_starts[0]
    end_candidates = [
        entry
        for entry in forecasts
        if min_span_years <= entry[0] - start_year <= max_span_years
    ]
    if not end_candidates:
        return {
            "status": "missing_forecast",
            "reason": "forecast_span_too_short",
            "start_year": start_year,
            "start_eps": start_eps,
        }

    target_year, target_eps = end_candidates[-1]
    span_years = target_year - start_year
    if span_years <= 0 or start_eps <= 0 or target_eps <= 0:
        return {"status": "missing_forecast", "reason": "invalid_eps_values"}

    growth_pct = ((target_eps / start_eps) ** (1 / span_years) - 1) * 100
    if not isfinite(growth_pct):
        return {"status": "missing_forecast", "reason": "invalid_growth"}

    return {
        "status": "calculated",
        "method": "verified_eps_cagr",
        "start_year": start_year,
        "start_eps": start_eps,
        "target_year": target_year,
        "target_eps": target_eps,
        "span_years": span_years,
        "growth_pct": growth_pct,
    }


def classify_reported_growth(quote: dict) -> dict:
    """Use profit growth when available; revenue growth is fallback only when profit is missing."""
    profit = _num(quote.get("netprofit_yoy_pct"))
    revenue = _num(quote.get("revenue_yoy_pct"))

    if profit is not None:
        verified = profit > 0
        if verified:
            detail = f"净利润同比 {profit:.2f}% 为正"
        elif revenue is not None and revenue > 0:
            detail = f"营收同比 {revenue:.2f}% 为正，但净利润同比 {profit:.2f}% 未正增长"
        else:
            detail = f"净利润同比 {profit:.2f}% 未正增长"
        return {"verified": verified, "basis": "netprofit_yoy", "detail": detail}

    if revenue is not None:
        verified = revenue > 0
        return {
            "verified": verified,
            "basis": "revenue_yoy_fallback",
            "detail": f"净利润同比缺失，营收同比 {revenue:.2f}%" + (" 为正" if verified else " 未正增长"),
        }

    return {"verified": False, "basis": "missing", "detail": "营收和净利润同比均缺失"}


def industry_rank_assessment(code: str, quote: dict, settings: dict) -> dict:
    universe = settings.get("universe", {})
    max_rank = float(universe.get("industry_leader_rank_max", 3))
    broad_rank = _num(quote.get("industry_rank"))
    broad_industry = str(quote.get("industry") or "").strip()

    overrides = universe.get("verified_subindustry_ranks", {}) or {}
    override = overrides.get(code)
    if override is not None:
        if isinstance(override, dict):
            rank = _num(override.get("rank"))
            industry = str(override.get("name") or override.get("industry") or "已核验细分行业").strip()
        else:
            rank = _num(override)
            industry = "已核验细分行业"
        if rank is None:
            return {
                "basis": "verified_subindustry",
                "rank": None,
                "industry": industry,
                "reject_reason": None,
                "pending_reason": "细分行业排名覆盖值无效",
                "note": None,
            }
        reject = rank > max_rank
        return {
            "basis": "verified_subindustry",
            "rank": rank,
            "industry": industry,
            "reject_reason": f"{industry}已核验排名第 {rank:.0f}，未进入前 {max_rank:.0f} 名" if reject else None,
            "pending_reason": None,
            "note": f"采用已核验细分行业排名：{industry}第 {rank:.0f} 名",
        }

    mode = str(universe.get("broad_industry_rank_mode", "reference")).strip().lower()
    if broad_rank is None:
        return {
            "basis": "tushare_broad_industry",
            "rank": None,
            "industry": broad_industry,
            "reject_reason": None,
            "pending_reason": "行业市值排名缺失，无法提供宽口径行业位置参考" if mode == "hard" else None,
            "note": "Tushare 宽口径行业排名缺失" if mode != "off" else None,
        }

    if mode == "hard" and broad_rank > max_rank:
        reject_reason = f"宽口径行业市值排名第 {broad_rank:.0f}，未进入前 {max_rank:.0f} 名"
    else:
        reject_reason = None

    note = None
    if mode != "off":
        label = broad_industry or "Tushare 行业"
        note = f"{label}宽口径市值排名第 {broad_rank:.0f}；仅作参考，不等同于细分赛道龙头排名" if mode != "hard" else f"{label}市值排名第 {broad_rank:.0f}"

    return {
        "basis": "tushare_broad_industry",
        "rank": broad_rank,
        "industry": broad_industry,
        "reject_reason": reject_reason,
        "pending_reason": None,
        "note": note,
    }
