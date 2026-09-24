from __future__ import annotations

from datetime import date

import pytest

from stock_picker.reports import _validate_summary_sources
from stock_picker.research_logic import (
    classify_reported_growth,
    industry_rank_assessment,
    merge_report_summaries,
    normalized_forward_eps_growth,
)


def _forecast(year: int, eps: float, quote: str = "verified quote with enough source characters") -> dict:
    return {
        "year": year,
        "eps": eps,
        "type": "forecast",
        "quote": quote,
        "page": 1,
        "source_verified": True,
        "eps_source_verified": True,
    }


def test_normalized_growth_uses_multi_year_cagr_instead_of_single_year_jump():
    summary = {"forecasts": [_forecast(2026, 1.0), _forecast(2027, 3.0), _forecast(2028, 4.0)]}
    growth = normalized_forward_eps_growth(summary, date(2026, 9, 24), min_span_years=2, max_span_years=3)

    assert growth["status"] == "calculated"
    assert growth["start_year"] == 2026
    assert growth["target_year"] == 2028
    assert growth["span_years"] == 2
    assert growth["growth_pct"] == pytest.approx(100.0)


def test_normalized_growth_requires_minimum_span():
    summary = {"forecasts": [_forecast(2026, 1.0), _forecast(2027, 2.0)]}
    growth = normalized_forward_eps_growth(summary, date(2026, 9, 24), min_span_years=2, max_span_years=3)

    assert growth["status"] == "missing_forecast"
    assert growth["reason"] == "forecast_span_too_short"


def test_reported_growth_does_not_accept_revenue_growth_when_profit_is_negative():
    result = classify_reported_growth({"revenue_yoy_pct": 20.0, "netprofit_yoy_pct": -5.0})

    assert result["verified"] is False
    assert result["basis"] == "netprofit_yoy"
    assert "营收同比" in result["detail"]


def test_reported_growth_uses_revenue_only_when_profit_is_missing():
    result = classify_reported_growth({"revenue_yoy_pct": 12.0, "netprofit_yoy_pct": None})

    assert result["verified"] is True
    assert result["basis"] == "revenue_yoy_fallback"


def test_broad_industry_rank_is_reference_by_default():
    settings = {
        "universe": {
            "industry_leader_rank_max": 3,
            "broad_industry_rank_mode": "reference",
            "verified_subindustry_ranks": {},
        }
    }
    result = industry_rank_assessment(
        "688807.SH",
        {"industry": "半导体", "industry_rank": 83},
        settings,
    )

    assert result["reject_reason"] is None
    assert result["basis"] == "tushare_broad_industry"
    assert "仅作参考" in result["note"]


def test_verified_subindustry_rank_can_be_a_hard_filter():
    settings = {
        "universe": {
            "industry_leader_rank_max": 3,
            "broad_industry_rank_mode": "reference",
            "verified_subindustry_ranks": {
                "688807.SH": {"name": "光通信前端收发电芯片", "rank": 4}
            },
        }
    }
    result = industry_rank_assessment(
        "688807.SH",
        {"industry": "半导体", "industry_rank": 83},
        settings,
    )

    assert result["basis"] == "verified_subindustry"
    assert result["reject_reason"] is not None
    assert "第 4" in result["reject_reason"]


def test_merge_keeps_verified_old_order_and_prefers_newer_forecast():
    reports = [
        {
            "file": "old.pdf",
            "report_date": "20260920",
            "summary": {
                "summary_mode": "ai",
                "industry_logic": "old industry narrative with enough detail",
                "company_logic": "old company narrative with enough detail",
                "profit_model": "old profit narrative with enough detail",
                "financial_quality": "old finance narrative with enough detail",
                "orders": {
                    "confirmed": True,
                    "source_verified": True,
                    "quote": "这是已经核验通过的历史订单证据，长度足够用于测试。",
                    "page": 2,
                },
                "forecasts": [_forecast(2027, 1.0), _forecast(2028, 1.5)],
                "risks": ["旧风险"],
            },
        },
        {
            "file": "new.pdf",
            "report_date": "20260923",
            "summary": {
                "summary_mode": "ai",
                "industry_logic": "new industry narrative with enough detail",
                "company_logic": "new company narrative with enough detail",
                "profit_model": "new profit narrative with enough detail",
                "financial_quality": "new finance narrative with enough detail",
                "orders": {"confirmed": False, "source_verified": False, "quote": "", "page": None},
                "forecasts": [_forecast(2027, 1.2), _forecast(2028, 1.8)],
                "risks": ["新风险"],
            },
        },
    ]

    merged = merge_report_summaries(reports)

    assert merged["orders"]["quote"].startswith("这是已经核验")
    assert merged["orders"]["source_file"] == "old.pdf"
    assert [item["eps"] for item in merged["forecasts"]] == [1.2, 1.8]
    assert all(item["source_file"] == "new.pdf" for item in merged["forecasts"])
    assert merged["industry_logic"].startswith("new industry")
    assert merged["risks"][0] == "新风险"


def test_source_validation_nulls_unverifiable_eps():
    page = "公司预计2027年每股收益EPS为1.78元，该预测来自分析师盈利预测表，供估值参考。"
    summary = {
        "orders": {},
        "financial_evidence": {},
        "forecasts": [
            {"year": 2027, "eps": 1.78, "type": "forecast", "quote": page, "page": 1},
            {"year": 2027, "eps": 2.99, "type": "forecast", "quote": page, "page": 1},
        ],
        "evidence_quotes": [],
        "source_caveats": [],
    }

    checked = _validate_summary_sources(summary, [page])

    assert checked["forecasts"][0]["eps_source_verified"] is True
    assert checked["forecasts"][0]["eps"] == 1.78
    assert checked["forecasts"][1]["eps_source_verified"] is False
    assert checked["forecasts"][1]["eps"] is None
