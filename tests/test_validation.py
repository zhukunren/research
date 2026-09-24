from __future__ import annotations

import json

import pandas as pd

from stock_picker.validation import build_forward_validation


def test_forward_validation_uses_only_matured_future_closes(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    history = [
        {
            "build_id": "b1",
            "built_at": "2026-09-01T18:00:00+08:00",
            "generated_at": "2026-09-01",
            "candidates": [
                {
                    "code": "000001.SZ",
                    "company": "测试公司",
                    "status": "CORE",
                    "quote_date": "2026-09-01",
                }
            ],
        }
    ]
    (output / "build_history.json").write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")

    dates = pd.bdate_range("2026-09-01", periods=10)
    rows = []
    for i, trade_date in enumerate(dates):
        rows.append({"trade_date": trade_date.strftime("%Y%m%d"), "stock_code": "000001.SZ", "close": 10 + i})
        rows.append({"trade_date": trade_date.strftime("%Y%m%d"), "stock_code": "000300.SH", "close": 100 + i})
    parquet = tmp_path / "bars.parquet"
    pd.DataFrame(rows).to_parquet(parquet, index=False)

    settings = {
        "paths": {"output_dir": output, "parquet_path": parquet},
        "validation": {"horizons": [5, 20], "benchmark_codes": ["000300.SH"]},
    }
    result = build_forward_validation(settings)

    assert result["matured_record_count"] == 1
    row = result["records"][0]
    assert row["horizon_trading_days"] == 5
    assert row["entry_date"] == "2026-09-01"
    assert row["exit_date"] == dates[5].date().isoformat()
    assert row["return_pct"] == 50.0
    assert "000300.SH" in row["excess_returns_pct"]
