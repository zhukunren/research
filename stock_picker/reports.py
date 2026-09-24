from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
import urllib.request
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader


TICKER_PATTERN = re.compile(r"(?<!\d)(\d{4,6})\.(SH|SZ|BJ|HK|KS)(?![A-Z])", re.I)
ORDER_CONFIRMATION = re.compile(
    r"(?P<quote>已获(?:得)?[\u3400-\u9fffA-Za-z0-9/（）()·\s]{0,32}?订单|"
    r"已签(?:订)?[\u3400-\u9fffA-Za-z0-9/（）()·\s]{0,24}?合同|签订订单|"
    r"在手订单|已有[\u3400-\u9fffA-Za-z0-9/（）()·\s]{0,12}?订单|"
    r"订单(?:已|正式)?落地|中标[\u3400-\u9fffA-Za-z0-9/（）()·\s]{0,20}|批量订单)"
)
PROMPT_VERSION = "report-summary-v4"

DELISTING_RISK_PATTERNS = (
    re.compile(r"退市风险(?:警示)?|终止上市(?:风险)?|(?:面值|财务类|重大违法|交易类).{0,8}退市|(?:可能|或将|将被|面临|触及|存在).{0,18}(?:退市|终止上市)", re.I),
    re.compile(r"delisting risk|risk of delisting|may be delisted|could be delisted|termination of (?:the )?listing", re.I),
)
DELISTING_RISK_NEGATIONS = (
    re.compile(r"(?:不存在|不涉及|不构成|未发现|未触及|未提示|未披露|无.{0,4}|无需|无须|不会|不可能|不面临).{0,10}(?:退市风险|退市|终止上市)", re.I),
    re.compile(r"(?:退市风险|退市|终止上市).{0,10}(?:已解除|已消除|已撤销|已不存在|不再存在|不会发生)", re.I),
    re.compile(r"(?:no|without|not subject to).{0,24}(?:delisting risk|risk of delisting|being delisted)", re.I),
)


def _parse_filename(path: Path) -> dict:
    stem = path.stem
    ticker_match = TICKER_PATTERN.search(stem)
    date_match = re.match(r"(20\d{6})-", stem)
    report_date = date_match.group(1) if date_match else ""
    title = stem
    if date_match:
        title = stem[len(date_match.group(0)):]
        if "-" in title:
            title = title.split("-", 1)[1]
    company = title
    code = ticker_match.group(0).upper() if ticker_match else ""
    if ticker_match:
        company_match = re.search(r"^(.*?)[（(]\s*" + re.escape(code) + r"\s*[）)]", title, flags=re.I)
        company = company_match.group(1).strip(" -：:") if company_match else title
    market = code.rsplit(".", 1)[-1] if code else "UNKNOWN"
    return {
        "code": code,
        "company": company,
        "market": market,
        "report_date": report_date,
        "title": title,
    }


def _extract_pages(path: Path) -> list[str]:
    pdftotext = shutil.which("pdftotext.exe") or shutil.which("pdftotext")
    if pdftotext:
        extracted = subprocess.run(
            [pdftotext, "-layout", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        raw_pages = extracted.stdout.split("\f")
        if raw_pages and not raw_pages[-1].strip():
            raw_pages.pop()
        pages = [page.strip() for page in raw_pages]
        if extracted.returncode == 0 and pages:
            return pages
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    if any(pages):
        return pages
    return []


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？；])\s*|[\r\n]+", text)
    return [re.sub(r"\s+", " ", p).strip(" \t-▌") for p in parts if len(p.strip()) >= 16]


def _page_for_quote(pages: list[str], quote: str) -> int | None:
    if not quote:
        return None
    needle = re.sub(r"\s+", " ", quote[:48]).strip()
    for page_num, page in enumerate(pages, 1):
        if needle in re.sub(r"\s+", " ", page):
            return page_num
    return None


def _normalized_quote(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W_]+", "", normalized)


def _quote_is_on_page(pages: list[str], quote, page) -> bool:
    if not isinstance(quote, str) or not quote.strip():
        return False
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        return False
    if page_number < 1 or page_number > len(pages):
        return False
    normalized = _normalized_quote(quote)
    if len(normalized) < 20:
        return False
    return normalized[:80] in _normalized_quote(pages[page_number - 1])


def _number_is_in_quote(value, quote: str) -> bool:
    try:
        target = float(value)
    except (TypeError, ValueError):
        return False
    if not isinstance(quote, str):
        return False
    for token in re.findall(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?", quote):
        try:
            if abs(float(token.replace(",", "")) - target) < 1e-8:
                return True
        except ValueError:
            continue
    return False


def _validate_summary_sources(summary: dict, pages: list[str]) -> dict:
    caveats = summary.setdefault("source_caveats", [])
    if not isinstance(caveats, list):
        caveats = summary["source_caveats"] = []

    orders = summary.get("orders")
    if isinstance(orders, dict):
        verified = _quote_is_on_page(pages, orders.get("quote"), orders.get("page"))
        orders["source_verified"] = verified
        orders["confirmed"] = bool(orders.get("confirmed") and verified)
        if orders.get("quote") and not verified:
            caveat = "订单引用未能在标注页码的原文中核实，不能作为已确认订单。"
            if caveat not in caveats:
                caveats.append(caveat)

    financial = summary.get("financial_evidence")
    if isinstance(financial, dict):
        verified = _quote_is_on_page(pages, financial.get("quote"), financial.get("page"))
        financial["source_verified"] = verified
        is_actual = financial.get("fact_type") == "reported_actual"
        financial["confirmed"] = bool(financial.get("confirmed") and is_actual and verified)
        if financial.get("quote") and not financial["confirmed"]:
            caveat = "财务证据须为原文可核实的已披露实绩；预测或未核实引用不用于业绩验证。"
            if caveat not in caveats:
                caveats.append(caveat)

    evidence_quotes = summary.get("evidence_quotes", [])
    if isinstance(evidence_quotes, list):
        for item in evidence_quotes:
            if isinstance(item, dict):
                item["source_verified"] = _quote_is_on_page(pages, item.get("quote"), item.get("page"))

    forecasts = summary.get("forecasts", [])
    if isinstance(forecasts, list):
        for item in forecasts:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote")
            page = item.get("page")
            year = item.get("year")
            source_verified = _quote_is_on_page(pages, quote, page)
            try:
                year_verified = bool(re.search(rf"(?<!\d){int(year)}(?!\d)", quote or ""))
            except (TypeError, ValueError):
                year_verified = False
            is_forecast = str(item.get("type", "")).strip().lower() == "forecast"
            eps = item.get("eps")
            eps_verified = eps is None or _number_is_in_quote(eps, quote)
            item["source_verified"] = source_verified and year_verified
            item["eps_source_verified"] = bool(item["source_verified"] and is_forecast and eps is not None and eps_verified)
            if eps is not None and not item["eps_source_verified"]:
                item["eps"] = None
                caveat = f"{year or '未知年度'} EPS 缺少可核验的原文数值、页码或预测标记，不用于情景估值。"
                if caveat not in caveats:
                    caveats.append(caveat)
    return summary


def _delisting_risk_flags(pages: list[str]) -> list[dict]:
    flags = []
    for page_number, page in enumerate(pages, 1):
        sentences = re.split(r"(?<=[。！？；.!?;])\s*|[\r\n]+", page)
        for sentence in sentences:
            excerpt = re.sub(r"\s+", " ", sentence).strip()
            if not excerpt or any(pattern.search(excerpt) for pattern in DELISTING_RISK_NEGATIONS):
                continue
            if any(pattern.search(excerpt) for pattern in DELISTING_RISK_PATTERNS):
                flags.append({"page": page_number, "quote": excerpt[:500]})
                if len(flags) >= 12:
                    return flags
    return flags


def _region(text: str, starts: tuple[str, ...], stops: tuple[str, ...], limit: int = 5000) -> str:
    start_at = -1
    start_end = -1
    for heading in starts:
        match = re.search(re.escape(heading), text)
        if match and (start_at < 0 or match.start() < start_at):
            start_at, start_end = match.start(), match.end()
    if start_end < 0:
        return ""
    stop_at = len(text)
    for heading in stops:
        match = re.search(re.escape(heading), text[start_end:])
        if match:
            stop_at = min(stop_at, start_end + match.start())
    return text[start_end:stop_at][:limit]


def _fallback_summary(pages: list[str]) -> dict:
    text = " ".join(re.sub(r"\s+", " ", page).strip() for page in pages)
    sentences = _sentences(text)
    order_match = ORDER_CONFIRMATION.search(text)
    order_sentence = re.sub(r"\s+", " ", order_match.group("quote")).strip() if order_match else ""
    forecast_sentence = next((s for s in sentences if re.search(r"EPS|每股收益", s, re.I) and "分别" in s), "")
    forecasts = []
    if forecast_sentence:
        eps_match = re.search(r"EPS|每股收益", forecast_sentence, re.I)
        eps_text = forecast_sentence[eps_match.end():] if eps_match else ""
        year_match = re.search(r"(20\d{2})\s*[-~至到]\s*(20\d{2})", forecast_sentence)
        value_match = re.search(r"(?:分别为|分别是|分别达到|为：?)\s*((?:\d+(?:\.\d+)?[、,，\s]*){2,})", eps_text)
        if year_match and value_match:
            values = re.findall(r"\d+(?:\.\d+)?", value_match.group(1))
            first_year = int(year_match.group(1))
            last_year = int(year_match.group(2))
            for offset, value in enumerate(values[:last_year - first_year + 1]):
                forecasts.append({
                    "year": first_year + offset,
                    "eps": float(value),
                    "type": "forecast",
                    "quote": forecast_sentence,
                    "page": _page_for_quote(pages, forecast_sentence),
                })
    return {
        "industry_logic": "待 AI 归纳；当前结果尚未生成行业语义分析。",
        "company_logic": "待 AI 归纳；当前结果尚未生成公司竞争力分析。",
        "profit_model": "待 AI 归纳；当前结果尚未生成收入与利润驱动分析。",
        "financial_quality": "待 AI 归纳；财务质量仍需结合报表和 Tushare 指标核对。",
        "orders": {
            "confirmed": bool(order_sentence),
            "quote": order_sentence,
            "page": _page_for_quote(pages, order_sentence),
            "meaning": "规则提取到明确订单措辞，仍需人工确认订单是否已签约/交付。" if order_sentence else "未提取到可确认的订单措辞。",
        },
        "financial_evidence": {"confirmed": False, "quote": "", "page": None, "metrics": {}},
        "forecasts": forecasts,
        "risks": ["待 AI 归纳；请查看原研报风险提示。"],
        "source_caveats": ["当前摘要为原文规则摘录，尚未生成语义归纳。"],
        "summary_mode": "extractive",
    }


def _ai_summary(pages: list[str], settings: dict) -> dict:
    env = settings["env"]
    api_key = env["llm_api_key"]
    if not api_key:
        raise RuntimeError("LLM_API_KEY 未配置")
    wire_api = env["llm_wire_api"]
    if wire_api != "responses":
        raise RuntimeError("研报分析只支持原生 Responses API；请将 LLM_WIRE_API 设为 responses")
    if env["llm_base_scheme"] != "https" and not env["llm_allow_insecure_http"]:
        raise RuntimeError("LLM_BASE_URL 必须使用 HTTPS；或显式启用 LLM_ALLOW_INSECURE_HTTP")
    page_text = "\n\n".join(f"[第 {i} 页]\n{page}" for i, page in enumerate(pages, 1))
    page_text = page_text[:30000]
    prompt = f"""请分析以下证券研究报告，输出有效 JSON，不要 Markdown 代码围栏，不要补充报告没有支持的事实。
必须区分已发生事实、公司计划和分析师预测。订单证据只有已签约、中标、在手订单等明确事实才可标记 confirmed=true；送样、客户验证和预测中的订单不得标为已确认。financial_evidence 只有报告期已结束且报告明确披露的历史财务实绩才能 confirmed=true，必须将 fact_type 标为 reported_actual；分析师预测、公司指引和目标值不得作为已披露业绩。每条订单、财务证据和预测都必须给出逐字原文摘录与正确页码，不得转述后放入 quote。无法找到原文依据时 quote 为空且 confirmed=false。没有的信息用空字符串、空数组或 null。
JSON 字段：industry_logic（行业景气和供需逻辑）, company_logic（公司在产业链的位置/竞争优势）, profit_model（收入、利润具体由哪些产品/客户/产能驱动）, financial_quality（增长、盈利能力、现金流、负债、资本回报的质量）, orders（confirmed:boolean, quote:string, page:number|null, meaning:string）, financial_evidence（fact_type:reported_actual|forecast|unknown, confirmed:boolean, quote:string, page:number|null, metrics:object）, forecasts（数组，每项 year:number, eps:number|null, revenue:number|null, net_profit:number|null, type:forecast|reported_actual, quote:string, page:number|null；quote 必须包含该年度和所填 EPS 原值）, risks（字符串数组）, evidence_quotes（数组，每项 topic:string, quote:string, page:number|null）, source_caveats（字符串数组）。
将财务预测标注为 type=forecast。数值按报告原币种/单位记录，并在相关文字中保留单位。不要把研报标题改写成投资逻辑。

报告正文：
{page_text}"""
    base_url = env["llm_base_url"].rstrip("/")
    system_prompt = "你是严谨的证券研究分析助手，结论必须由用户提供的研报文本支持。"
    endpoint = base_url if base_url.endswith("/responses") else f"{base_url}/responses"
    request_body = {
        "model": env["llm_model"],
        "reasoning": {"effort": env["llm_reasoning_effort"]},
        "store": bool(env["llm_store_responses"]),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ],
    }
    payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=env["llm_request_timeout_seconds"]) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body.get("output_text")
    if not content:
        text_parts = []
        for item in body.get("output", []):
            for part in item.get("content", []):
                if part.get("type") in {"output_text", "text"} and part.get("text"):
                    text_parts.append(part["text"])
        content = "".join(text_parts)
    if not content:
        raise ValueError("Responses API 返回中没有文本内容")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", str(content), flags=re.I)
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("AI response must be a JSON object")
    for field in ("industry_logic", "company_logic", "profit_model", "financial_quality"):
        if not isinstance(result.get(field), str):
            result[field] = ""
    for field in ("orders", "financial_evidence"):
        if not isinstance(result.get(field), dict):
            result[field] = {}
    for field in ("risks", "forecasts", "evidence_quotes", "source_caveats"):
        if not isinstance(result.get(field), list):
            result[field] = [result[field]] if isinstance(result.get(field), str) else []
    result["forecasts"] = [item for item in result["forecasts"] if isinstance(item, dict)]
    result["evidence_quotes"] = [item for item in result["evidence_quotes"] if isinstance(item, dict)]
    result["summary_mode"] = "ai"
    return result


def _cache_key(path: Path, model: str, reasoning_effort: str) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(f"{PROMPT_VERSION}:{model}:{reasoning_effort}:{digest}".encode("utf-8")).hexdigest()


def load_reports(settings: dict, refresh_ai: bool = False) -> list[dict]:
    report_dir = Path(settings["paths"]["report_dir"])
    cache_dir = Path(settings["paths"]["cache_dir"]) / "reports"
    cache_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    if not report_dir.exists():
        return reports
    for path in sorted(report_dir.glob("*.pdf")):
        meta = _parse_filename(path)
        extraction_error = ""
        try:
            pages = _extract_pages(path)
        except Exception as exc:
            pages = []
            extraction_error = type(exc).__name__
        summary = _fallback_summary(pages)
        extraction_status = "error" if extraction_error else ("text_extracted" if any(pages) else "no_text")
        ai_error = ""
        ai_error_type = ""
        cache_error_type = ""
        if pages and settings["env"]["llm_api_key"]:
            key = _cache_key(
                path,
                settings["env"]["llm_model"],
                settings["env"]["llm_reasoning_effort"],
            )
            cache_file = cache_dir / f"{key}.json"
            cached = False
            if cache_file.exists() and not refresh_ai:
                try:
                    cached_summary = json.loads(cache_file.read_text(encoding="utf-8"))
                    if isinstance(cached_summary, dict):
                        summary = cached_summary
                        cached = True
                except (OSError, json.JSONDecodeError):
                    pass
            if not cached:
                try:
                    summary = _validate_summary_sources(_ai_summary(pages, settings), pages)
                except Exception as exc:
                    ai_error_type = type(exc).__name__
                    ai_error = f"AI 归纳失败，使用原文摘录：{type(exc).__name__}: {exc}"
                else:
                    try:
                        cache_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                    except OSError as exc:
                        cache_error_type = type(exc).__name__
        summary = _validate_summary_sources(summary, pages)
        reports.append({
            **meta,
            "file": path.name,
            "path": str(path),
            "page_count": len(pages),
            "summary": summary,
            "risk_flags": _delisting_risk_flags(pages),
            "extraction_status": extraction_status,
            "extraction_error": extraction_error,
            "ai_error": ai_error,
            "ai_error_type": ai_error_type,
            "cache_error_type": cache_error_type,
            "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
    return reports
