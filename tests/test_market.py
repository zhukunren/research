from __future__ import annotations

import pandas as pd

from stock_picker.market import _parse_dates


def test_parse_dates_handles_numeric_yyyymmdd():
    values = pd.Series([20260923, 20260924])
    result = _parse_dates(values)

    assert result.dt.strftime("%Y-%m-%d").tolist() == ["2026-09-23", "2026-09-24"]
