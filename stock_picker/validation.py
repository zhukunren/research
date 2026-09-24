from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import pandas as pd
import pyarrow.dataset as ds


def _parse_dates(values: pd.Series) -> pd.Series:
    strings = values.astype(str).str.replace(r"\.0$", "", regex=True)
    yyyymmdd = strings.str.fullmatch(r"\d{8}")
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    if yyyymmdd.any():
        parsed.loc[yyyymmdd] = pd.to_datetime(strings.loc[yyyymmdd], format="%Y%m%d", errors="coerce")
    if (~yyyymmdd).any():
        parsed.loc[~yyyymmdd] = pd.to_datetime(values.loc[~yyyymmdd], errors="coerce")
    return parsed


def _load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"构建历史格式无效：{path}")
    return data


def _load_bars(parquet_path: Path, codes: list[str]) -> dict[str, pd.DataFrame]:
    if not parquet_path.exists() or not codes:
        return {}
    dataset = ds.dataset(str(parquet_path), format="parquet")
    required = {"trade_date", "stock_code", "close"}
    missing = required - set(dataset.schema.names)
    if missing:
        raise ValueError(f"本地行情缺少字段：{', '.join(sorted(missing))}")
    table = dataset.to_table(
        columns=["trade_date", "stock_code", "close"],
        filter=ds.field("stock_code").isin(codes),
    )
    if table.num_rows == 0:
        return {}
    frame = table.to_pandas()
    frame["trade_date"] = _parse_dates(frame["trade_date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "stock_code", "close"])
    return {
        str(code): group.sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)
        for code, group in frame.groupby("stock_code")
    }


def _forward_return(bars: pd.DataFrame | None, signal_date: str | None, horizon: int) -> dict | None:
    if bars is None or bars.empty or not signal_date:
        return None
    try:
        signal = pd.Timestamp(signal_date)
    except (TypeError, ValueError):
        return None

    dates = bars["trade_date"]
    eligible = bars.index[dates <= signal]
    if len(eligible) == 0:
        return None
    entry_idx = int(eligible[-1])
    exit_idx = entry_idx + int(horizon)
    if exit_idx >= len(bars):
        return None

    entry = bars.iloc[entry_idx]
    exit_row = bars.iloc[exit_idx]
    entry_price = float(entry["close"])
    exit_price = float(exit_row["close"])
    if entry_price <= 0:
        return None
    return {
        "entry_date": pd.Timestamp(entry["trade_date"]).date().isoformat(),
        "entry_price": entry_price,
        "exit_date": pd.Timestamp(exit_row["trade_date"]).date().isoformat(),
        "exit_price": exit_price,
        "return_pct": (exit_price / entry_price - 1) * 100,
    }


def build_forward_validation(settings: dict, horizons: list[int] | None = None) -> dict:
    output_dir = Path(settings["paths"]["output_dir"])
    history_path = output_dir / "build_history.json"
    parquet_path = Path(settings["paths"]["parquet_path"])
    history = _load_history(history_path)
    configured = settings.get("validation", {})
    horizons = [int(value) for value in (horizons or configured.get("horizons", [5, 20, 60])) if int(value) > 0]
    benchmarks = [str(code) for code in configured.get("benchmark_codes", [])]

    candidate_codes = {
        str(candidate.get("code"))
        for build in history
        for candidate in build.get("candidates", [])
        if candidate.get("code")
    }
    all_codes = sorted(candidate_codes | set(benchmarks))
    bars = _load_bars(parquet_path, all_codes)

    records = []
    for build in history:
        for candidate in build.get("candidates", []) or []:
            code = str(candidate.get("code") or "")
            signal_date = candidate.get("quote_date") or build.get("generated_at")
            for horizon in horizons:
                outcome = _forward_return(bars.get(code), signal_date, horizon)
                if outcome is None:
                    continue
                benchmark_returns = {}
                excess_returns = {}
                for benchmark in benchmarks:
                    bench = _forward_return(bars.get(benchmark), signal_date, horizon)
                    if bench is None:
                        continue
                    benchmark_returns[benchmark] = round(bench["return_pct"], 4)
                    excess_returns[benchmark] = round(outcome["return_pct"] - bench["return_pct"], 4)
                records.append({
                    "build_id": build.get("build_id"),
                    "built_at": build.get("built_at"),
                    "code": code,
                    "company": candidate.get("company"),
                    "status": candidate.get("status"),
                    "signal_date": signal_date,
                    "horizon_trading_days": horizon,
                    **{key: (round(value, 4) if isinstance(value, float) else value) for key, value in outcome.items()},
                    "benchmark_returns_pct": benchmark_returns,
                    "excess_returns_pct": excess_returns,
                })

    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in records:
        grouped[(str(row.get("status") or "UNKNOWN"), int(row["horizon_trading_days"]))].append(row)

    summary = []
    for (status, horizon), rows in sorted(grouped.items()):
        returns = [float(row["return_pct"]) for row in rows]
        item = {
            "status": status,
            "horizon_trading_days": horizon,
            "matured_count": len(rows),
            "mean_return_pct": round(mean(returns), 4),
            "median_return_pct": round(median(returns), 4),
            "positive_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 2),
        }
        for benchmark in benchmarks:
            values = [
                float(row["excess_returns_pct"][benchmark])
                for row in rows
                if benchmark in row.get("excess_returns_pct", {})
            ]
            if values:
                item[f"mean_excess_vs_{benchmark}_pct"] = round(mean(values), 4)
                item[f"excess_positive_rate_vs_{benchmark}_pct"] = round(
                    sum(value > 0 for value in values) / len(values) * 100, 2
                )
        summary.append(item)

    return {
        "measurement": "signal-day close to Nth subsequent trading-day close; research validation, not executable backtest",
        "history_build_count": len(history),
        "horizons": horizons,
        "benchmark_codes": benchmarks,
        "matured_record_count": len(records),
        "summary": summary,
        "records": records,
    }


def render_validation_markdown(result: dict) -> str:
    lines = [
        "# 观察池前瞻验证",
        "",
        "口径：信号日收盘价到随后第 N 个交易日收盘价，仅用于研究筛选效果验证，不代表可成交回测收益。",
        f"历史构建：{result['history_build_count']} 次；已成熟观测：{result['matured_record_count']} 条；周期：{result['horizons']}。",
        "",
        "## 汇总",
        "",
    ]
    if not result["summary"]:
        lines.append("尚无达到前瞻周期的历史样本。继续按日构建后再运行验证即可。")
    else:
        lines.append("| 状态 | 周期 | 样本 | 平均收益 | 中位收益 | 正收益率 |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for item in result["summary"]:
            lines.append(
                f"| {item['status']} | T+{item['horizon_trading_days']} | {item['matured_count']} | "
                f"{item['mean_return_pct']:.2f}% | {item['median_return_pct']:.2f}% | {item['positive_rate_pct']:.2f}% |"
            )
    return "\n".join(lines) + "\n"


def write_forward_validation(result: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "forward_validation.json"
    md_path = output_dir / "forward_validation.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_validation_markdown(result), encoding="utf-8")
    return md_path, json_path
