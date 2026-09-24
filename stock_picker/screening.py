from __future__ import annotations

from datetime import date
import re


def _num(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none", "<na>"} else text


def _forward_eps_growth(summary: dict, as_of: date) -> tuple[int | None, float | None, float | None]:
    forecasts = []
    for item in summary.get("forecasts", []) or []:
        try:
            if str(item.get("type", "")).strip().lower() != "forecast" or item.get("eps_source_verified") is not True:
                continue
            year = int(item.get("year"))
            eps = _num(item.get("eps"))
            if eps is not None and eps > 0:
                forecasts.append((year, eps))
        except (TypeError, ValueError, AttributeError):
            continue
    forecasts = sorted(set(forecasts))
    if len(forecasts) < 2:
        return None, None, None
    target_options = [entry for entry in forecasts if entry[0] > as_of.year]
    target = target_options[0] if target_options else forecasts[-1]
    prior_options = [entry for entry in forecasts if entry[0] < target[0]]
    if not prior_options:
        return target[0], target[1], None
    prior = prior_options[-1]
    if prior[1] <= 0:
        return target[0], target[1], None
    growth_pct = (target[1] / prior[1] - 1) * 100
    return target[0], target[1], growth_pct


def _scenario_valuation(summary: dict, settings: dict, as_of: date) -> dict:
    year, eps, growth_pct = _forward_eps_growth(summary, as_of)
    if year is None or eps is None or growth_pct is None or growth_pct <= 0:
        return {"status": "missing_forecast", "forecast_year": year, "eps": eps, "growth_pct": growth_pct, "scenarios": {}}
    assumptions = settings["valuation"]
    discount_rate = float(assumptions["discount_rate"])
    months_to_year_end = (12 - as_of.month) / 12
    discount_years = max(0.1, year - as_of.year + months_to_year_end)
    discount_factor = (1 + discount_rate) ** discount_years
    pe_floor = float(assumptions["pe_floor"])
    pe_cap = float(assumptions["pe_cap"])
    results = {}
    for name, values in assumptions["scenarios"].items():
        peg_low, peg_high = map(float, values["peg_range"])
        eps_low, eps_high = map(float, values["eps_factor_range"])
        pe_low = min(pe_cap, max(pe_floor, growth_pct * peg_low))
        pe_high = min(pe_cap, max(pe_floor, growth_pct * peg_high))
        low = eps * eps_low * pe_low / discount_factor
        high = eps * eps_high * pe_high / discount_factor
        results[name] = {
            "low": round(low, 2),
            "high": round(high, 2),
            "pe_low": round(pe_low, 2),
            "pe_high": round(pe_high, 2),
        }
    return {
        "status": "calculated",
        "forecast_year": year,
        "eps": eps,
        "growth_pct": growth_pct,
        "discount_rate_pct": discount_rate * 100,
        "discount_years": round(discount_years, 2),
        "scenarios": results,
    }


def _is_st_or_delist(name: str) -> bool:
    upper = _text(name).upper()
    return bool(re.search(r"(?:^|[^A-Z])S?\*?ST(?:[^A-Z]|$)", upper) or "退市" in upper or "退" == upper[:1])


def evaluate_candidate(reports: list[dict], market_data: dict, settings: dict) -> dict:
    latest = sorted(reports, key=lambda r: (r.get("report_date", ""), r.get("file", "")))[-1]
    code = latest.get("code", "")
    summary = latest.get("summary", {}) or {}
    quote = market_data.get("candidates", {}).get(code, {}) or {}
    technical = quote.get("technical", {}) or {}
    today = date.fromisoformat(market_data["as_of"])
    valuation_scenarios = _scenario_valuation(summary, settings, today)
    growth_pct = valuation_scenarios.get("growth_pct")
    pe_raw = _num(quote.get("pe_ttm"))
    pe = pe_raw if pe_raw is not None and pe_raw > 0 else None
    pb_raw = _num(quote.get("pb"))
    ps_raw = _num(quote.get("ps_ttm"))
    pb = pb_raw if pb_raw is not None and pb_raw > 0 else None
    ps = ps_raw if ps_raw is not None and ps_raw > 0 else None
    peg = pe / growth_pct if pe is not None and growth_pct is not None and growth_pct > 0 else None
    ratios = {"pe_ttm": pe, "pb": pb, "ps_ttm": ps, "peg": peg}
    rejection_reasons = []
    pending_reasons = []
    report_risk_flags = [
        {"file": report.get("file"), **flag}
        for report in reports
        for flag in report.get("risk_flags", [])
        if isinstance(flag, dict)
    ]

    if latest.get("market") not in settings["universe"]["markets"]:
        status = "OUTSIDE_SCOPE"
        pending_reasons.append(f"{latest.get('market')} 市场暂未接入筛选数据适配器")
    else:
        status = "PENDING"
        list_status = quote.get("listing_status")
        name = _text(quote.get("name")) or latest.get("company", "")
        delist_date = _text(quote.get("delist_date"))
        if list_status == "D" or delist_date:
            rejection_reasons.append("已退市或存在退市日期")
        elif list_status == "P":
            rejection_reasons.append("当前不是正常上市状态")
        elif list_status != "L":
            pending_reasons.append("上市状态未验证")
        if _is_st_or_delist(name):
            rejection_reasons.append("ST、*ST 或退市标记公司")

        min_cap = float(settings["universe"]["min_market_cap_yi"])
        market_cap = _num(quote.get("market_cap_yi"))
        if market_cap is None:
            pending_reasons.append("总市值缺失，无法排除小盘股")
        elif market_cap < min_cap:
            rejection_reasons.append(f"总市值 {market_cap:.1f} 亿元低于 {min_cap:.0f} 亿元门槛")

        leader_rank = _num(quote.get("industry_rank"))
        if leader_rank is None:
            pending_reasons.append("行业市值排名缺失，无法确认龙头/中军")
        elif leader_rank > float(settings["universe"]["industry_leader_rank_max"]):
            rejection_reasons.append(f"行业市值排名第 {leader_rank:.0f}，未进入前 {settings['universe']['industry_leader_rank_max']} 名")

        evidence = summary.get("orders", {}) or {}
        financial_evidence = summary.get("financial_evidence", {}) or {}
        netprofit_yoy = _num(quote.get("netprofit_yoy_pct"))
        revenue_yoy = _num(quote.get("revenue_yoy_pct"))
        reported_growth = (netprofit_yoy is not None and netprofit_yoy > 0) or (revenue_yoy is not None and revenue_yoy > 0)
        order_verified = bool(evidence.get("confirmed") and evidence.get("quote") and evidence.get("page"))
        reported_financial_verified = bool(
            financial_evidence.get("confirmed")
            and financial_evidence.get("fact_type") == "reported_actual"
            and financial_evidence.get("source_verified") is True
            and financial_evidence.get("quote")
            and financial_evidence.get("page")
        )
        order_verified = order_verified and evidence.get("source_verified") is True
        if not (order_verified or reported_financial_verified or reported_growth):
            pending_reasons.append("未验证真实订单或正向已披露业绩")

        if report_risk_flags:
            rejection_reasons.append("研报明确提示退市或终止上市风险")

        if any(value is None for value in (pe, pb, ps)):
            pending_reasons.append("PE/PB/PS 数据不完整")
        if valuation_scenarios["status"] != "calculated":
            pending_reasons.append("缺少可用于 PEG 情景估值的正向未来 EPS 增速")
        elif peg is not None:
            max_peg = float(settings["valuation"]["max_peg_for_core"])
            if peg > max_peg:
                rejection_reasons.append(f"PEG {peg:.2f} 高于核心池上限 {max_peg:.2f}，当前估值对增长要求偏高")
            current_price = _num(technical.get("price")) if technical.get("status") == "fresh" else None
            base_scenario = valuation_scenarios.get("scenarios", {}).get("base", {})
            if current_price is not None and base_scenario.get("high") is not None and current_price > base_scenario["high"]:
                rejection_reasons.append("最新价格高于基准情景估值上沿")

        max_age = int(settings["data"]["max_quote_age_days"])
        if technical.get("status") == "missing":
            pending_reasons.append("没有可用日线数据")
        elif technical.get("age_days") is None or technical.get("age_days") > max_age:
            pending_reasons.append(f"行情过期（{technical.get('quote_date') or '日期未知'}），技术形态不作有效判断")
        else:
            if technical.get("trend_ok") is False:
                rejection_reasons.append("均线未形成 MA5 > MA20 > MA60 多头结构")
            elif technical.get("trend_ok") is None:
                pending_reasons.append("日线长度不足，不能计算 MA5/20/60")
            vol_ratio = _num(technical.get("volume_ratio_5d_20d"))
            if vol_ratio is None:
                pending_reasons.append("成交量历史不足")
            elif vol_ratio < float(settings["technical"]["min_volume_ratio_5d_20d"]):
                rejection_reasons.append(f"成交量未放大（5/20 日均量比 {vol_ratio:.2f}）")
            elif vol_ratio > float(settings["technical"]["max_volume_ratio_5d_20d"]):
                rejection_reasons.append(f"短期成交量过热（5/20 日均量比 {vol_ratio:.2f}）")
            turnover = _num(technical.get("turnover_rate_pct"))
            if turnover is None:
                pending_reasons.append("换手率缺失，无法确认资金参与度")
            elif turnover < float(settings["technical"]["min_turnover_rate_pct"]) or turnover > float(settings["technical"]["max_turnover_rate_pct"]):
                rejection_reasons.append(f"换手率 {turnover:.2f}% 不在温和活跃区间")
            amount20 = _num(technical.get("avg_amount_20d_yi"))
            if amount20 is None:
                pending_reasons.append("20 日平均成交额缺失，无法确认交易活跃度")
            elif amount20 < float(settings["technical"]["min_avg_amount_20d_yi"]):
                rejection_reasons.append(
                    f"20 日平均成交额 {amount20:.2f} 亿元低于 {float(settings['technical']['min_avg_amount_20d_yi']):.2f} 亿元流动性门槛"
                )
            amount_ratio = _num(technical.get("amount_ratio_5d_20d"))
            if amount_ratio is None:
                pending_reasons.append("成交额历史不足，无法确认近期资金参与度变化")
            elif amount_ratio < float(settings["technical"]["min_amount_ratio_5d_20d"]):
                rejection_reasons.append(f"近 5 日成交额未较 20 日均值增强（成交额比 {amount_ratio:.2f}）")
            if technical.get("high_volume_stall"):
                rejection_reasons.append("高位放量但近 5 日涨幅有限，疑似放量滞涨")
            if technical.get("no_volume_rise"):
                rejection_reasons.append("近 5 日上涨缺少成交量配合")

        if not _text(quote.get("industry")):
            pending_reasons.append("行业分类缺失")
        required_summary = ("industry_logic", "company_logic", "profit_model", "financial_quality")
        summary_complete = all(isinstance(summary.get(field), str) and len(summary[field].strip()) >= 20 for field in required_summary)
        if summary.get("summary_mode") != "ai" or not summary_complete or not summary.get("risks"):
            pending_reasons.append("研报尚未完成 AI 归纳，当前逻辑为原文规则摘录")

        status = "REJECTED" if rejection_reasons else ("PENDING" if pending_reasons else "CORE")

    financial_quality = summary.get("financial_quality", "")
    finance_facts = []
    for field, label in (("revenue_yoy_pct", "营收同比"), ("netprofit_yoy_pct", "净利润同比"), ("roe_pct", "ROE"), ("gross_margin_pct", "毛利率")):
        value = _num(quote.get(field))
        if value is not None:
            finance_facts.append(f"{label} {value:.1f}%")
    if finance_facts:
        financial_quality = (financial_quality + "；" if financial_quality else "") + "Tushare 最新披露指标：" + "、".join(finance_facts)

    return {
        "code": code,
        "company": _text(quote.get("name")) or latest.get("company", ""),
        "market": latest.get("market"),
        "status": status,
        "reports": [{"file": r["file"], "date": r.get("report_date"), "title": r.get("title")} for r in reports],
        "summary": summary,
        "quote": {key: value for key, value in quote.items() if key != "technical"},
        "technical": technical,
        "valuation": {"ratios": ratios, "scenario": valuation_scenarios},
        "rejection_reasons": rejection_reasons,
        "pending_reasons": pending_reasons,
        "report_risk_flags": report_risk_flags,
        "financial_quality": financial_quality,
    }


def build_watchlist(reports: list[dict], market_data: dict, settings: dict) -> dict:
    by_code = {}
    for report in reports:
        if report.get("code"):
            by_code.setdefault(report["code"], []).append(report)
    candidates = [evaluate_candidate(group, market_data, settings) for group in by_code.values()]
    candidates.sort(key=lambda item: (item["status"] != "CORE", item["company"], item["code"]))
    report_issues = []
    for report in reports:
        issues = []
        if report.get("extraction_status") == "error":
            issues.append(f"PDF 解析失败（{report.get('extraction_error') or '未知错误'}）")
        elif report.get("extraction_status") == "no_text":
            issues.append("未提取到文本，可能是扫描版 PDF；当前未配置 OCR")
        if report.get("ai_error"):
            error_type = report.get("ai_error_type") or "未知错误"
            issues.append(f"AI 归纳失败（{error_type}），已回退到原文摘录")
        if report.get("cache_error_type"):
            issues.append(f"AI 摘要缓存失败（{report['cache_error_type']}），下次构建会重新调用模型")
        if issues:
            report_issues.append({"file": report.get("file", "未知文件"), "issues": issues})
    technical_records = [item.get("technical", {}) or {} for item in market_data["candidates"].values()]
    return {
        "generated_at": market_data["as_of"],
        "market_data_mode": market_data["mode"],
        "tushare_configured": market_data["tushare_configured"],
        "data_errors": market_data["errors"],
        "report_issues": report_issues,
        "report_count": len(reports),
        "report_text_count": sum(report.get("extraction_status") == "text_extracted" for report in reports),
        "report_ai_count": sum((report.get("summary") or {}).get("summary_mode") == "ai" for report in reports),
        "report_issue_count": len(report_issues),
        "fresh_quote_count": sum(item.get("status") == "fresh" for item in technical_records),
        "criteria": {
            "min_market_cap_yi": settings["universe"]["min_market_cap_yi"],
            "industry_leader_rank_max": settings["universe"]["industry_leader_rank_max"],
            "max_quote_age_days": settings["data"]["max_quote_age_days"],
            "max_peg_for_core": settings["valuation"]["max_peg_for_core"],
            "min_avg_amount_20d_yi": settings["technical"]["min_avg_amount_20d_yi"],
            "min_amount_ratio_5d_20d": settings["technical"]["min_amount_ratio_5d_20d"],
            "volume_ratio_5d_20d_range": [
                settings["technical"]["min_volume_ratio_5d_20d"],
                settings["technical"]["max_volume_ratio_5d_20d"],
            ],
            "turnover_rate_pct_range": [
                settings["technical"]["min_turnover_rate_pct"],
                settings["technical"]["max_turnover_rate_pct"],
            ],
        },
        "candidates": candidates,
    }
