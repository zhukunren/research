from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds


def _number(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dates(values: pd.Series) -> pd.Series:
    strings = values.astype(str)
    if strings.str.fullmatch(r"\d{8}").all():
        return pd.to_datetime(strings, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(values, errors="coerce")


def _load_local_bars(parquet_path: Path, codes: list[str]) -> dict[str, pd.DataFrame]:
    if not parquet_path.exists() or not codes:
        return {}
    dataset = ds.dataset(str(parquet_path), format="parquet")
    table = dataset.to_table(
        columns=["trade_date", "stock_code", "close", "volume", "amount"],
        filter=ds.field("stock_code").isin(codes),
    )
    if table.num_rows == 0:
        return {}
    frame = table.to_pandas()
    frame["trade_date"] = _parse_dates(frame["trade_date"])
    return {code: group.sort_values("trade_date").reset_index(drop=True) for code, group in frame.groupby("stock_code")}


def _merge_bars(local: pd.DataFrame | None, remote: pd.DataFrame | None) -> pd.DataFrame:
    frames = []
    if local is not None and not local.empty:
        frames.append(local[["trade_date", "close", "volume", "amount"]].copy())
    if remote is not None and not remote.empty:
        data = remote.copy()
        if "vol" in data.columns and "volume" not in data.columns:
            data = data.rename(columns={"vol": "volume"})
        data["trade_date"] = _parse_dates(data["trade_date"])
        for column in ("close", "volume", "amount"):
            if column not in data.columns:
                data[column] = None
        frames.append(data[["trade_date", "close", "volume", "amount"]])
    if not frames:
        return pd.DataFrame(columns=["trade_date", "close", "volume", "amount"])
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.dropna(subset=["trade_date", "close"])
    return merged.drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)


def _technical(bars: pd.DataFrame, turnover_rate: float | None, settings: dict, as_of: date) -> dict:
    if bars.empty:
        return {"status": "missing", "quote_date": None, "age_days": None}
    close = pd.to_numeric(bars["close"], errors="coerce").dropna()
    volume = pd.to_numeric(bars["volume"], errors="coerce").reindex(close.index).fillna(0)
    amount = pd.to_numeric(bars["amount"], errors="coerce").reindex(close.index)
    if close.empty:
        return {"status": "missing", "quote_date": None, "age_days": None}
    last_date = pd.Timestamp(bars.loc[close.index[-1], "trade_date"]).date()
    age_days = (as_of - last_date).days
    ma5 = float(close.tail(5).mean()) if len(close) >= 5 else None
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None
    volume20 = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    volume_ratio = float(volume.tail(5).mean() / volume20) if volume20 and volume20 > 0 and len(volume) >= 20 else None
    amount20_values = amount.tail(20).dropna()
    amount5_values = amount.tail(5).dropna()
    amount20 = float(amount20_values.mean()) if len(amount20_values) == 20 else None
    amount5 = float(amount5_values.mean()) if len(amount5_values) == 5 else None
    amount_ratio = float(amount5 / amount20) if amount5 is not None and amount20 and amount20 > 0 and len(amount) >= 20 else None
    amount20_yi = amount20 / 100000 if amount20 is not None else None
    return_5d_pct = float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) >= 6 and close.iloc[-6] else None
    high20 = float(close.tail(20).max()) if len(close) >= 20 else None
    price_position_20d_pct = float(close.iloc[-1] / high20 * 100) if high20 else None
    trend_ok = None if None in (ma5, ma20, ma60) else bool(ma5 > ma20 > ma60 and close.iloc[-1] > ma20)
    rules = settings["technical"]
    high_volume_stall = bool(
        volume_ratio is not None and return_5d_pct is not None and price_position_20d_pct is not None
        and price_position_20d_pct >= 90
        and volume_ratio > rules["high_volume_stall_ratio"]
        and abs(return_5d_pct) <= rules["high_volume_stall_return_abs_pct"]
    )
    no_volume_rise = bool(
        volume_ratio is not None and return_5d_pct is not None
        and return_5d_pct >= rules["no_volume_rise_return_pct"]
        and volume_ratio < rules["no_volume_rise_ratio"]
    )
    status = "fresh" if age_days <= settings["data"]["max_quote_age_days"] else "stale"
    return {
        "status": status,
        "quote_date": last_date.isoformat(),
        "age_days": age_days,
        "price": float(close.iloc[-1]),
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "trend_ok": trend_ok,
        "volume_ratio_5d_20d": volume_ratio,
        "avg_amount_20d_yi": amount20_yi,
        "amount_ratio_5d_20d": amount_ratio,
        "return_5d_pct": return_5d_pct,
        "price_position_20d_pct": price_position_20d_pct,
        "turnover_rate_pct": turnover_rate,
        "high_volume_stall": high_volume_stall,
        "no_volume_rise": no_volume_rise,
    }


def _active_market_frame(pro, list_status: str) -> pd.DataFrame:
    return pro.stock_basic(
        exchange="",
        list_status=list_status,
        fields="ts_code,symbol,name,industry,list_date,delist_date,list_status",
    )


def _load_tushare(codes: list[str], settings: dict) -> tuple[dict, list[str]]:
    import tushare as ts

    pro = ts.pro_api(settings["env"]["tushare_token"])
    now = date.today()
    today_str = now.strftime("%Y%m%d")
    calendar = pro.trade_cal(
        exchange="SSE",
        is_open="1",
        start_date=(now - timedelta(days=20)).strftime("%Y%m%d"),
        end_date=today_str,
    )
    if calendar is None or calendar.empty:
        raise RuntimeError("Tushare 未返回近期 A 股交易日历")
    errors = []
    trade_dates = sorted(calendar["cal_date"].astype(str).drop_duplicates(), reverse=True)
    trade_date = None
    daily_basic = pd.DataFrame()
    for candidate_date in trade_dates:
        frame = pro.daily_basic(
            trade_date=candidate_date,
            fields="ts_code,trade_date,close,turnover_rate,pe_ttm,pb,ps_ttm,total_mv",
        )
        if frame is not None and not frame.empty:
            trade_date = candidate_date
            daily_basic = frame
            break
    if trade_date is None:
        errors.append("daily_basic 近期交易日均无数据；PE/PB/PS/市值待补")
    frames = {}
    status_frames = {}
    for status in ("L", "D", "P"):
        try:
            frame = _active_market_frame(pro, status)
            if frame is not None and not frame.empty:
                frame["list_status"] = status
                status_frames[status] = frame
        except Exception as exc:
            errors.append(f"stock_basic[{status}] 失败: {type(exc).__name__}: {exc}")
    active = status_frames.get("L", pd.DataFrame())
    all_status = pd.concat(status_frames.values(), ignore_index=True) if status_frames else pd.DataFrame()
    if not active.empty and not daily_basic.empty and "total_mv" in daily_basic.columns:
        leaders = active[["ts_code", "industry"]].merge(
            daily_basic[["ts_code", "total_mv"]], on="ts_code", how="inner"
        )
        leaders["industry_rank"] = leaders.groupby("industry")["total_mv"].rank(method="min", ascending=False)
        rank_map = leaders.set_index("ts_code")["industry_rank"].to_dict()
    else:
        rank_map = {}
    basic_by_code = all_status.drop_duplicates("ts_code", keep="first").set_index("ts_code") if not all_status.empty else pd.DataFrame()
    daily_by_code = daily_basic.drop_duplicates("ts_code", keep="last").set_index("ts_code") if not daily_basic.empty else pd.DataFrame()
    bars_by_code = {}
    finance_by_code = {}
    start_date = (now - timedelta(days=settings["data"]["local_history_days"])).strftime("%Y%m%d")
    for code in codes:
        try:
            bars_by_code[code] = pro.daily(ts_code=code, start_date=start_date, end_date=today_str)
        except Exception as exc:
            errors.append(f"daily[{code}] 失败: {type(exc).__name__}: {exc}")
        try:
            finance = pro.fina_indicator(
                ts_code=code,
                fields="ts_code,ann_date,end_date,roe,grossprofit_margin,netprofit_yoy,or_yoy",
            )
            if finance is not None and not finance.empty:
                announced = finance["ann_date"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
                finance = finance.loc[announced.str.fullmatch(r"\d{8}") & announced.le(today_str)].copy()
                if not finance.empty:
                    finance["ann_date"] = announced.loc[finance.index]
                    finance["ann_date_sort"] = finance["ann_date"].fillna("")
                    finance_by_code[code] = finance.sort_values(["end_date", "ann_date_sort"]).iloc[-1].to_dict()
        except Exception as exc:
            errors.append(f"fina_indicator[{code}] 失败: {type(exc).__name__}: {exc}")
    result = {}
    for code in codes:
        basic = basic_by_code.loc[code].to_dict() if not basic_by_code.empty and code in basic_by_code.index else {}
        daily = daily_by_code.loc[code].to_dict() if not daily_by_code.empty and code in daily_by_code.index else {}
        status = str(basic.get("list_status", "")) or None
        fin = finance_by_code.get(code, {})
        result[code] = {
            "name": basic.get("name"),
            "industry": basic.get("industry"),
            "listing_status": status,
            "list_date": basic.get("list_date"),
            "delist_date": basic.get("delist_date"),
            "industry_rank": _number(rank_map.get(code)),
            "market_cap_yi": (_number(daily.get("total_mv")) / 10000) if _number(daily.get("total_mv")) is not None else None,
            "pe_ttm": _number(daily.get("pe_ttm")),
            "pb": _number(daily.get("pb")),
            "ps_ttm": _number(daily.get("ps_ttm")),
            "turnover_rate_pct": _number(daily.get("turnover_rate")),
            "netprofit_yoy_pct": _number(fin.get("netprofit_yoy")),
            "revenue_yoy_pct": _number(fin.get("or_yoy")),
            "roe_pct": _number(fin.get("roe")),
            "gross_margin_pct": _number(fin.get("grossprofit_margin")),
            "price": _number(daily.get("close")),
            "financial_period": fin.get("end_date"),
            "financial_announce_date": fin.get("ann_date"),
            "tushare_trade_date": trade_date,
            "remote_bars": bars_by_code.get(code),
        }
    return result, errors


def collect_market_data(reports: list[dict], settings: dict, offline: bool = False) -> dict:
    codes = sorted({report["code"] for report in reports if report.get("code") and report.get("market") in settings["universe"]["markets"]})
    parquet_path = Path(settings["paths"]["parquet_path"])
    local_bars = _load_local_bars(parquet_path, codes)
    remote: dict[str, dict] = {}
    errors = []
    use_tushare = bool(settings["env"]["tushare_token"]) and not offline
    if use_tushare:
        try:
            remote, errors = _load_tushare(codes, settings)
        except Exception as exc:
            errors.append(f"Tushare 数据获取失败: {type(exc).__name__}: {exc}")
    as_of = date.today()
    candidates = {}
    for code in codes:
        data = remote.get(code, {})
        remote_bars = data.get("remote_bars")
        bars = _merge_bars(local_bars.get(code), remote_bars)
        technical = _technical(bars, data.get("turnover_rate_pct"), settings, as_of)
        price = _number(data.get("price")) or _number(technical.get("price"))
        if price is not None:
            technical["price"] = price
        candidates[code] = {
            key: value for key, value in data.items() if key != "remote_bars"
        } | {
            "price": price,
            "market_data_mode": "tushare" if data else "local_parquet",
            "technical": technical,
            "price_date": technical.get("quote_date"),
        }
    return {
        "as_of": as_of.isoformat(),
        "mode": "tushare+local" if use_tushare and remote else "local_parquet",
        "tushare_configured": bool(settings["env"]["tushare_token"]),
        "errors": errors,
        "candidates": candidates,
    }
