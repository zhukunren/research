from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from stock_picker.config import load_settings
from stock_picker.market import collect_market_data
from stock_picker.output import HISTORY_LIMIT, create_history_snapshot, write_outputs
from stock_picker.reports import load_reports
from stock_picker.screening import build_watchlist


ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
BUILD_LOCK = threading.Lock()

app = FastAPI(title="投研研究终端", docs_url="/api/docs", redoc_url=None)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


class BuildRequest(BaseModel):
    offline: bool = False
    refresh_ai: bool = False


def _load_result() -> dict:
    settings = load_settings()
    path = Path(settings["paths"]["output_dir"]) / "core_watchlist.json"
    if not path.exists():
        return {
            "generated_at": None,
            "built_at": None,
            "build_id": None,
            "market_data_mode": "未构建",
            "tushare_configured": bool(settings["env"]["tushare_token"]),
            "data_errors": [],
            "criteria": {},
            "changes": {"available": False, "core_entered": [], "core_exited": [], "status_changes": []},
            "candidates": [],
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"观察池数据读取失败：{type(exc).__name__}") from exc


def _perform_build(request: BuildRequest) -> dict:
    settings = load_settings()
    reports = load_reports(settings, refresh_ai=request.refresh_ai)
    if not reports:
        raise RuntimeError(f"研报目录中没有 PDF 文件：{settings['paths']['report_dir']}；现有观察池未更改。")
    market_data = collect_market_data(reports, settings, offline=request.offline)
    result = build_watchlist(reports, market_data, settings)
    write_outputs(result, reports, settings["paths"]["output_dir"])
    return result


@app.get("/", response_class=HTMLResponse)
def index():
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail="Web 页面文件缺失")
    return FileResponse(page, media_type="text/html; charset=utf-8")


@app.get("/api/health")
def health():
    result = _load_result()
    settings = load_settings()
    quote_dates = [
        candidate.get("technical", {}).get("quote_date")
        for candidate in result.get("candidates", [])
        if candidate.get("technical", {}).get("quote_date")
    ]
    latest_quote = max(quote_dates) if quote_dates else None
    quote_age_days = None
    if latest_quote:
        quote_age_days = (date.today() - date.fromisoformat(latest_quote)).days
    return {
        "ok": True,
        "generated_at": result.get("generated_at"),
        "built_at": result.get("built_at"),
        "build_id": result.get("build_id"),
        "market_data_mode": result.get("market_data_mode", "未构建"),
        "tushare_configured": bool(settings["env"]["tushare_token"]),
        "llm_configured": bool(settings["env"]["llm_api_key"]),
        "llm_model": settings["env"]["llm_model"],
        "llm_wire_api": settings["env"]["llm_wire_api"],
        "llm_store_responses": settings["env"]["llm_store_responses"],
        "llm_endpoint_blocked": settings["env"]["llm_base_scheme"] != "https" and not settings["env"]["llm_allow_insecure_http"],
        "latest_quote_date": latest_quote,
        "quote_age_days": quote_age_days,
        "candidate_count": len(result.get("candidates", [])),
        "report_issue_count": len(result.get("report_issues", [])),
        "report_count": result.get("report_count", 0),
        "report_ai_count": result.get("report_ai_count", 0),
    }


@app.get("/api/watchlist")
def watchlist():
    return _load_result()


@app.get("/api/validation")
def forward_validation():
    settings = load_settings()
    path = Path(settings["paths"]["output_dir"]) / "forward_validation.json"
    if not path.exists():
        configured = settings.get("validation", {})
        return {
            "measurement": "signal-day close to Nth subsequent trading-day close; research validation, not executable backtest",
            "history_build_count": 0,
            "horizons": configured.get("horizons", [5, 20, 60]),
            "benchmark_codes": configured.get("benchmark_codes", []),
            "matured_record_count": 0,
            "summary": [],
            "records": [],
        }
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"前瞻验证结果读取失败：{type(exc).__name__}") from exc
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="前瞻验证结果格式无效")
    return result


@app.get("/api/history")
def build_history():
    settings = load_settings()
    path = Path(settings["paths"]["output_dir"]) / "build_history.json"
    if not path.exists():
        current = _load_result()
        if current.get("candidates"):
            current.setdefault("build_id", f"legacy-{current.get('generated_at') or 'unknown'}")
            current.setdefault("built_at", current.get("generated_at") or "未知")
            current.setdefault("changes", {"available": False, "core_entered": [], "core_exited": [], "status_changes": []})
            return {"builds": [create_history_snapshot(current)], "retention_limit": HISTORY_LIMIT}
        return {"builds": [], "retention_limit": HISTORY_LIMIT}
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"构建历史读取失败：{type(exc).__name__}") from exc
    if not isinstance(history, list):
        raise HTTPException(status_code=500, detail="构建历史格式无效")
    return {"builds": list(reversed(history)), "retention_limit": HISTORY_LIMIT}


@app.post("/api/refresh")
async def refresh(request: BuildRequest):
    if not BUILD_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有刷新任务正在运行")
    try:
        try:
            return await run_in_threadpool(_perform_build, request)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"构建失败：{type(exc).__name__}。请查看服务日志。") from exc
    finally:
        BUILD_LOCK.release()


@app.get("/api/reports/{filename}")
def report_pdf(filename: str):
    settings = load_settings()
    report_dir = Path(settings["paths"]["report_dir"]).resolve()
    if Path(filename).name != filename or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="无效的研报文件名")
    path = (report_dir / filename).resolve()
    if path.parent != report_dir or not path.is_file():
        raise HTTPException(status_code=404, detail="研报不存在")
    return FileResponse(path, media_type="application/pdf", content_disposition_type="inline")
