from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pyarrow.dataset as ds


REQUIRED_BAR_COLUMNS = {"trade_date", "stock_code", "close", "volume", "amount"}
TICKER_PATTERN = re.compile(r"(?<!\d)\d{4,6}\.(?:SH|SZ|BJ|HK|KS)(?![A-Z])", re.I)


def run_preflight(settings: dict, offline: bool = False) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append({"status": status, "name": name, "detail": detail})

    report_dir = Path(settings["paths"]["report_dir"])
    pdfs = sorted(report_dir.glob("*.pdf")) if report_dir.is_dir() else []
    if not report_dir.is_dir():
        add("ERROR", "研报目录", f"目录不存在：{report_dir}")
    elif not pdfs:
        add("ERROR", "研报目录", f"没有 PDF 文件：{report_dir}")
    else:
        coded = sum(bool(TICKER_PATTERN.search(path.stem)) for path in pdfs)
        add("OK", "研报目录", f"找到 {len(pdfs)} 份 PDF，其中 {coded} 份文件名包含可识别证券代码。")
        if coded == 0:
            add("WARN", "证券代码", "未从研报文件名识别到证券代码；这些报告不会生成候选。")
        elif coded < len(pdfs):
            add("WARN", "证券代码", f"有 {len(pdfs) - coded} 份报告文件名没有可识别证券代码。")

    parquet_path = Path(settings["paths"]["parquet_path"])
    parquet_ready = False
    if parquet_path.exists():
        try:
            schema = ds.dataset(str(parquet_path), format="parquet").schema
            missing = sorted(REQUIRED_BAR_COLUMNS - set(schema.names))
            if missing:
                add("ERROR", "本地行情", f"Parquet 缺少必需字段：{', '.join(missing)}")
            else:
                parquet_ready = True
                add("OK", "本地行情", f"数据集字段齐全：{parquet_path}")
        except Exception as exc:
            add("ERROR", "本地行情", f"Parquet 无法读取：{type(exc).__name__}")
    else:
        add("WARN", "本地行情", f"未找到 Parquet：{parquet_path}")

    tushare_ready = bool(settings["env"].get("tushare_token"))
    if offline:
        if parquet_ready:
            add("OK", "行情来源", "本次预检为离线模式，只使用本地 Parquet。")
        else:
            add("ERROR", "行情来源", "离线模式需要可读取且字段齐全的本地 Parquet。")
    elif tushare_ready:
        add("OK", "行情来源", "Tushare Token 已配置；预检不会发起网络请求。")
        if not parquet_ready:
            add("WARN", "本地回退", "本地行情不可用；Tushare 返回失败时没有本地行情可回退。")
    elif parquet_ready:
        add("WARN", "行情来源", "未配置 Tushare Token；将只使用本地行情，市值和财务字段可能待核验。")
    else:
        add("ERROR", "行情来源", "没有可用的行情来源；配置 Tushare Token 或提供本地 Parquet。")

    env = settings["env"]
    if not env.get("llm_api_key"):
        add("WARN", "研报归纳", "未配置模型 API Key；将使用规则摘录，AI 归纳门槛无法通过。")
    elif env.get("llm_wire_api") != "responses":
        add("ERROR", "研报归纳", "当前只支持 Responses API；请将 LLM_WIRE_API 设为 responses。")
    elif env.get("llm_base_scheme") != "https" and not env.get("llm_allow_insecure_http"):
        add("ERROR", "研报归纳", "模型服务地址不是 HTTPS；可信本地服务可显式设置 LLM_ALLOW_INSECURE_HTTP=true。")
    else:
        add("OK", "研报归纳", f"模型已配置（{env.get('llm_model', '未知模型')}）；预检不会发起网络请求。")

    output_path = Path(settings["paths"]["output_dir"]) / "core_watchlist.json"
    if not output_path.exists():
        add("INFO", "上次构建", "尚无观察池结果；通过预检后运行 build。")
    else:
        try:
            previous = json.loads(output_path.read_text(encoding="utf-8"))
            generated = previous.get("generated_at")
            result_date = date.fromisoformat(generated) if generated else None
            age = (date.today() - result_date).days if result_date else None
            detail = f"最近结果日期：{generated or '未知'}。"
            if age is not None:
                detail += f" 文件距今 {age} 天。"
                if age > settings["data"]["max_quote_age_days"]:
                    add("WARN", "上次构建", detail + "结果可能已过期。")
                else:
                    add("OK", "上次构建", detail)
            else:
                add("WARN", "上次构建", detail + "建议重新构建。")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            add("WARN", "上次构建", "现有 JSON 结果无法读取；重新构建可重新生成。")

    history_path = Path(settings["paths"]["output_dir"]) / "build_history.json"
    if not history_path.exists():
        add("INFO", "构建历史", "尚无历史档案；下次构建会从现有观察池结果建立基线。")
    else:
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(history, list):
                add("OK", "构建历史", f"已保存 {len(history)} 次构建。")
            else:
                add("WARN", "构建历史", "历史档案格式无效；构建不会覆盖该文件。")
        except (OSError, json.JSONDecodeError):
            add("WARN", "构建历史", "历史档案无法读取；构建不会覆盖该文件。")

    return checks
