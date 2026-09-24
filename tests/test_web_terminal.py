from __future__ import annotations

from pathlib import Path

from web_app import app, forward_validation


ROOT = Path(__file__).resolve().parents[1]


def test_terminal_assets_exist_and_index_uses_split_assets():
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = ROOT / "web" / "styles.css"
    app_js = ROOT / "web" / "app.js"

    assert styles.is_file()
    assert app_js.is_file()
    assert 'href="/assets/styles.css"' in index
    assert 'src="/assets/app.js"' in index
    for element_id in (
        "tab-watchlist",
        "tab-reports",
        "tab-history",
        "tab-validation",
        "candidate-rows",
        "detail-panel",
        "validation-rows",
    ):
        assert f'id="{element_id}"' in index


def test_validation_endpoint_has_safe_empty_shape_without_generated_file(monkeypatch, tmp_path):
    import web_app

    settings = {
        "paths": {"output_dir": tmp_path},
        "validation": {"horizons": [5, 20, 60], "benchmark_codes": ["000300.SH"]},
    }
    monkeypatch.setattr(web_app, "load_settings", lambda: settings)

    result = forward_validation()

    assert result["matured_record_count"] == 0
    assert result["horizons"] == [5, 20, 60]
    assert result["benchmark_codes"] == ["000300.SH"]
    assert result["summary"] == []


def test_static_assets_are_mounted():
    paths = [getattr(route, "path", None) for route in app.routes]
    assert "/assets" in paths
