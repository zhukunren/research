from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4


STATUS_LABELS = {
    "CORE": "核心观察",
    "PENDING": "待核验",
    "REJECTED": "已排除",
    "OUTSIDE_SCOPE": "市场未覆盖",
}
HISTORY_LIMIT = 365


def _load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _candidate_map(result: dict | None) -> dict[str, dict]:
    if not isinstance(result, dict):
        return {}
    return {
        item["code"]: item
        for item in result.get("candidates", [])
        if isinstance(item, dict) and item.get("code")
    }


def _build_changes(previous: dict | None, result: dict) -> dict:
    if previous is None:
        return {"available": False, "previous_build_id": None, "core_entered": [], "core_exited": [], "status_changes": []}

    before = _candidate_map(previous)
    after = _candidate_map(result)
    entered = []
    exited = []
    status_changes = []
    for code in sorted(before.keys() | after.keys()):
        old = before.get(code)
        new = after.get(code)
        old_status = old.get("status") if old else None
        new_status = new.get("status") if new else None
        if old_status == new_status:
            continue
        candidate = new or old
        change = {
            "code": code,
            "company": candidate.get("company", code),
            "from_status": old_status,
            "to_status": new_status,
        }
        status_changes.append(change)
        if new_status == "CORE" and old_status != "CORE":
            entered.append(change)
        if old_status == "CORE" and new_status != "CORE":
            exited.append(change)
    return {
        "available": True,
        "previous_build_id": previous.get("build_id"),
        "core_entered": entered,
        "core_exited": exited,
        "status_changes": status_changes,
    }


def _status_label(status: str | None) -> str:
    if status is None:
        return "已移出候选"
    return STATUS_LABELS.get(status, status)


def create_history_snapshot(result: dict) -> dict:
    counts = Counter(item.get("status", "UNKNOWN") for item in result.get("candidates", []))
    candidates = []
    for item in result.get("candidates", []):
        valuation = item.get("valuation", {}) or {}
        technical = item.get("technical", {}) or {}
        quote = item.get("quote", {}) or {}
        base = (valuation.get("scenario", {}) or {}).get("scenarios", {}).get("base", {}) or {}
        candidates.append({
            "code": item.get("code"),
            "company": item.get("company"),
            "market": item.get("market"),
            "status": item.get("status"),
            "price": technical.get("price", quote.get("price")),
            "quote_date": technical.get("quote_date"),
            "peg": (valuation.get("ratios", {}) or {}).get("peg"),
            "base_value_low": base.get("low"),
            "base_value_high": base.get("high"),
            "reasons": (item.get("rejection_reasons", []) + item.get("pending_reasons", []))[:6],
        })
    return {
        "build_id": result["build_id"],
        "built_at": result["built_at"],
        "generated_at": result.get("generated_at"),
        "market_data_mode": result.get("market_data_mode"),
        "report_count": result.get("report_count", 0),
        "candidate_count": len(candidates),
        "status_counts": {key: counts.get(key, 0) for key in STATUS_LABELS},
        "changes": result["changes"],
        "candidates": candidates,
    }


def _fmt(value, suffix="", digits=2):
    try:
        return f"{float(value):.{digits}f}{suffix}" if value is not None else "待补数据"
    except (TypeError, ValueError):
        return "待补数据"


def _scenario_lines(candidate: dict) -> list[str]:
    scenario = candidate["valuation"]["scenario"]
    if scenario.get("status") != "calculated":
        return ["- 情景估值：缺少至少两年可比未来 EPS 预测或正向 EPS 增速，暂不外推。"]
    quote = candidate.get("quote", {})
    price = quote.get("price")
    lines = [
        f"- 情景估值：以 {scenario['forecast_year']} 年 EPS {_fmt(scenario['eps'])} 元、EPS 增速 {_fmt(scenario['growth_pct'], '%')}、PEG 情景倍数、折现率 {_fmt(scenario['discount_rate_pct'], '%')} 折现 {scenario['discount_years']} 年计算。"
    ]
    technical = candidate.get("technical", {}) or {}
    labels = (("bear", "保守"), ("base", "基准"), ("bull", "乐观"))
    for key, label in labels:
        values = scenario.get("scenarios", {}).get(key)
        if not values:
            continue
        low = values["low"]
        high = values["high"]
        range_text = f"{low:.2f}-{high:.2f} 元/股"
        if price and technical.get("status") == "fresh":
            low_pct = (low / price - 1) * 100
            high_pct = (high / price - 1) * 100
            range_text += f"（相对现价 {_fmt(low_pct, '%')} 至 {_fmt(high_pct, '%')}）"
        lines.append(f"  - {label}：{range_text}；对应 PE {values['pe_low']:.1f}-{values['pe_high']:.1f} 倍。")
    return lines


def _candidate_section(candidate: dict) -> str:
    status = STATUS_LABELS.get(candidate["status"], candidate["status"])
    summary = candidate.get("summary", {}) or {}
    quote = candidate.get("quote", {}) or {}
    technical = candidate.get("technical", {}) or {}
    valuation = candidate.get("valuation", {}) or {}
    ratios = valuation.get("ratios", {})
    lines = [f"### {candidate['company']}（{candidate['code']}）- {status}"]
    if quote.get("industry"):
        lines.append(f"- 行业/龙头位置：{quote['industry']}；行业市值排名 {_fmt(quote.get('industry_rank'), ' 名', 0)}；总市值 {_fmt(quote.get('market_cap_yi'), ' 亿元', 1)}。")
    lines.append(f"- 行业逻辑：{summary.get('industry_logic') or '研报中未提取到行业逻辑。'}")
    lines.append(f"- 公司逻辑：{summary.get('company_logic') or '研报中未提取到公司核心逻辑。'}")
    lines.append(f"- 盈利路径：{summary.get('profit_model') or '研报中未提取到明确的收入/利润驱动。'}")
    lines.append(f"- 财务质量：{candidate.get('financial_quality') or '缺少可用财务质量信息。'}")
    order = summary.get("orders", {}) or {}
    if order.get("quote"):
        proof = f"{order['quote']}（第 {order.get('page') or '?'} 页）"
    else:
        proof = "研报规则摘录未找到订单原句"
    order_confirmed = bool(order.get("confirmed") and order.get("source_verified"))
    actual = []
    for key, label in (("revenue_yoy_pct", "营收同比"), ("netprofit_yoy_pct", "净利润同比")):
        value = quote.get(key)
        if value is not None:
            actual.append(f"{label} {_fmt(value, '%')}")
    validation = "；".join(actual) if actual else "尚无 Tushare 实际业绩增速"
    lines.append(f"- 订单/业绩验证：订单证据 {'已确认且原文核验通过' if order_confirmed else '未确认或原文未核验'}；{proof}；{validation}。")
    financial_evidence = summary.get("financial_evidence", {}) or {}
    if financial_evidence.get("quote"):
        actual_confirmed = bool(
            financial_evidence.get("confirmed")
            and financial_evidence.get("fact_type") == "reported_actual"
            and financial_evidence.get("source_verified")
        )
        lines.append(
            f"- 报告业绩证据：{'已披露实绩且原文核验通过' if actual_confirmed else '待核验或非已披露实绩'}；"
            f"{financial_evidence['quote']}（第 {financial_evidence.get('page') or '?'} 页）。"
        )
    lines.append(
        "- 基础估值："
        f"PE(TTM) {_fmt(ratios.get('pe_ttm'), ' 倍')}；PB {_fmt(ratios.get('pb'), ' 倍')}；"
        f"PS(TTM) {_fmt(ratios.get('ps_ttm'), ' 倍')}；PEG {_fmt(ratios.get('peg'))}。"
    )
    peg = ratios.get("peg")
    if peg is None:
        valuation_read = "成长消化估值：待核验"
    elif peg <= 1:
        valuation_read = "成长消化估值：PEG 较低"
    elif peg <= 2.5:
        valuation_read = "成长消化估值：尚可，需结合基准情景判断"
    else:
        valuation_read = "成长消化估值：PEG 偏高，增长兑现要求较高"
    lines.append(f"- {valuation_read}")
    lines.extend(_scenario_lines(candidate))
    if technical.get("quote_date"):
        lines.append(
            f"- 技术状态：行情日 {technical['quote_date']}，收盘 {_fmt(technical.get('price'))}；"
            f"MA5/20/60 {_fmt(technical.get('ma5'))}/{_fmt(technical.get('ma20'))}/{_fmt(technical.get('ma60'))}；"
            f"5/20 日均量比 {_fmt(technical.get('volume_ratio_5d_20d'))}；20 日平均成交额 {_fmt(technical.get('avg_amount_20d_yi'), ' 亿元')}；"
            f"5/20 日均成交额比 {_fmt(technical.get('amount_ratio_5d_20d'))}；换手 {_fmt(technical.get('turnover_rate_pct'), '%')}；"
            f"趋势 {'多头通过' if technical.get('trend_ok') is True else '未通过' if technical.get('trend_ok') is False else '待核验'}；"
            f"放量滞涨 {'是' if technical.get('high_volume_stall') else '否'}；缩量上涨 {'是' if technical.get('no_volume_rise') else '否'}。"
        )
    else:
        lines.append("- 技术状态：缺少可用日线行情。")
    risks = summary.get("risks", []) or []
    lines.append(f"- 主要风险：{'；'.join(str(risk) for risk in risks[:6]) or '研报风险段未能提取。'}")
    for flag in candidate.get("report_risk_flags", [])[:6]:
        source = flag.get("file") or "研报"
        lines.append(f"- 退市风险原文线索：{source} 第 {flag.get('page') or '?'} 页：{flag.get('quote', '')}")
    evidence_quotes = summary.get("evidence_quotes", []) or []
    if evidence_quotes:
        formatted_quotes = []
        for item in evidence_quotes[:6]:
            if not isinstance(item, dict) or not item.get("quote") or item.get("source_verified") is not True:
                continue
            page = item.get("page") or "?"
            topic = item.get("topic") or "研报事实"
            formatted_quotes.append(f"{topic}第 {page} 页：{item['quote']}")
        if formatted_quotes:
            lines.append(f"- 原文证据：{'；'.join(formatted_quotes)}")
    reasons = candidate.get("rejection_reasons", []) + candidate.get("pending_reasons", [])
    if reasons:
        lines.append(f"- 状态原因：{'；'.join(reasons)}。")
    sources = []
    for report in candidate.get("reports", []):
        link = f"../research_report/{report['file']}"
        sources.append(f"[{report.get('date') or '日期未知'} 原研报](<{link}>)")
    lines.append(f"- 来源：{'；'.join(sources)}。")
    lines.append("")
    return "\n".join(lines)


def render_markdown(result: dict, reports: list[dict]) -> str:
    candidates = result["candidates"]
    core = [c for c in candidates if c["status"] == "CORE"]
    pending = [c for c in candidates if c["status"] == "PENDING"]
    rejected = [c for c in candidates if c["status"] == "REJECTED"]
    outside = [c for c in candidates if c["status"] == "OUTSIDE_SCOPE"]
    generated = result["generated_at"]
    mode = result["market_data_mode"]
    criteria = result.get("criteria", {})
    volume_range = criteria.get("volume_ratio_5d_20d_range", [None, None])
    turnover_range = criteria.get("turnover_rate_pct_range", [None, None])
    lines = [
        "# 核心观察池",
        "",
        f"生成日期：{generated}；数据模式：{mode}；识别研报：{len(reports)} 份；A 股候选：{len(core) + len(pending) + len(rejected)} 只。",
        f"构建时间：{result.get('built_at', '未知')}；构建编号：{result.get('build_id', '未知')}。",
        f"筛选门槛：总市值 ≥ {criteria.get('min_market_cap_yi', '待核验')} 亿元；行业市值排名前 {criteria.get('industry_leader_rank_max', '待核验')}；PEG ≤ {criteria.get('max_peg_for_core', '待核验')}；新鲜现价不高于基准估值上沿；行情不超过 {criteria.get('max_quote_age_days', '待核验')} 天；20 日平均成交额 ≥ {criteria.get('min_avg_amount_20d_yi', '待核验')} 亿元、5/20 日均成交额比 ≥ {criteria.get('min_amount_ratio_5d_20d', '待核验')}、5/20 日均量比 {volume_range[0]}-{volume_range[1]}、换手率 {turnover_range[0]}%-{turnover_range[1]}%；订单/实绩证据和技术信号均需可核验。",
        "",
        "## 本次构建变化",
        "",
    ]
    changes = result.get("changes", {})
    if not changes.get("available"):
        lines.append("首次留档；后续构建将记录核心观察池进出和候选状态变化。")
    elif not changes.get("status_changes"):
        lines.append("与上次构建相比，候选状态没有变化。")
    else:
        entered = changes.get("core_entered", [])
        exited = changes.get("core_exited", [])
        lines.append(f"核心观察新增 {len(entered)} 只，移出 {len(exited)} 只；候选状态变化 {len(changes['status_changes'])} 只。")
        for item in entered:
            lines.append(f"- 新增核心观察：{item['company']}（{item['code']}），原状态 {_status_label(item['from_status'])}。")
        for item in exited:
            lines.append(f"- 移出核心观察：{item['company']}（{item['code']}），现状态 {_status_label(item['to_status'])}。")
        other_changes = [item for item in changes["status_changes"] if item not in entered and item not in exited]
        for item in other_changes:
            lines.append(f"- 状态变化：{item['company']}（{item['code']}），{_status_label(item['from_status'])} → {_status_label(item['to_status'])}。")
    lines.extend(["", "## 核心观察池", ""])
    if core:
        lines.extend(_candidate_section(item) for item in core)
    else:
        lines.append("当前没有标的同时满足全部数据验证和筛选条件。缺少 Tushare/AI 凭证、过期行情或未通过硬筛的标的不会被放入核心池。")
        lines.append("")
    lines.extend(["## 待核验候选", ""])
    if pending:
        lines.extend(_candidate_section(item) for item in pending)
    else:
        lines.extend(["无。", ""])
    lines.extend(["## 已排除", ""])
    if rejected:
        lines.extend(_candidate_section(item) for item in rejected)
    else:
        lines.extend(["无。", ""])
    lines.extend(["## 市场未覆盖", ""])
    if outside:
        lines.extend(_candidate_section(item) for item in outside)
    else:
        lines.extend(["无。", ""])
    if not result["tushare_configured"]:
        lines.extend([
            "## 数据状态",
            "",
            "未配置 TUSHARE_TOKEN。当前 Parquet 最新数据日期为候选各自最新交易日；由于本地文件不含上市状态、市值、财务、换手和估值字段，这些条件均不能通过离线行情推断。",
            "",
        ])
    if result.get("data_errors"):
        lines.extend(["## 数据获取提示", ""])
        lines.extend(f"- {error}" for error in result["data_errors"])
        lines.append("")
    if result.get("report_issues"):
        lines.extend(["## 研报处理提示", ""])
        for item in result["report_issues"]:
            lines.append(f"- `{item['file']}`：{'；'.join(item['issues'])}。")
        lines.append("")
    return "\n".join(lines)


def write_outputs(result: dict, reports: list[dict], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "core_watchlist.md"
    json_path = output_dir / "core_watchlist.json"
    history_path = output_dir / "build_history.json"

    try:
        previous = _load_json(json_path)
    except json.JSONDecodeError:
        previous = None
    if not isinstance(previous, dict):
        previous = None
    elif not previous.get("build_id"):
        previous["build_id"] = f"legacy-{previous.get('generated_at') or 'unknown'}"
        previous["built_at"] = previous.get("built_at") or previous.get("generated_at") or "未知"
        previous.setdefault("changes", {"available": False, "core_entered": [], "core_exited": [], "status_changes": []})
    now = datetime.now().astimezone()
    result["build_id"] = now.strftime("%Y%m%dT%H%M%S%f%z")
    result["built_at"] = now.isoformat(timespec="seconds")
    result["changes"] = _build_changes(previous, result)

    try:
        history = _load_json(history_path)
    except json.JSONDecodeError as exc:
        raise ValueError(f"构建历史 JSON 无法解析，为避免覆盖该文件已停止：{history_path}") from exc
    if history is None:
        history = []
        if previous is not None:
            history.append(create_history_snapshot(previous))
    if not isinstance(history, list):
        raise ValueError(f"构建历史格式无效：{history_path}")
    history.append(create_history_snapshot(result))
    history = history[-HISTORY_LIMIT:]

    _write_text_atomic(md_path, render_markdown(result, reports))
    _write_text_atomic(json_path, json.dumps(result, ensure_ascii=False, indent=2, default=str))
    _write_text_atomic(history_path, json.dumps(history, ensure_ascii=False, indent=2, default=str))
    return md_path, json_path
