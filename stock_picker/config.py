from __future__ import annotations

import os
import configparser
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> dict:
    with (ROOT_DIR / "settings.yml").open("r", encoding="utf-8") as stream:
        settings = yaml.safe_load(stream) or {}
    app_config = configparser.ConfigParser(interpolation=None)
    app_config.read(ROOT_DIR / "config.ini", encoding="utf-8")
    app = app_config["app"] if app_config.has_section("app") else {}
    provider_name = os.getenv("LLM_PROVIDER", app.get("model_provider", "OpenAI"))
    provider_section = f"model_providers.{provider_name}"
    provider = app_config[provider_section] if app_config.has_section(provider_section) else {}
    secrets = app_config["secrets"] if app_config.has_section("secrets") else {}
    security = app_config["security"] if app_config.has_section("security") else {}
    tushare_config = app_config["tushare"] if app_config.has_section("tushare") else {}
    api_key_env = os.getenv("LLM_API_KEY_ENV", secrets.get("openai_api_key_env", "LLM_API_KEY"))
    config_api_key = os.getenv(api_key_env, "") if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env) else api_key_env
    disable_response_storage = _env_bool(
        "LLM_DISABLE_RESPONSE_STORAGE",
        app.getboolean("disable_response_storage", fallback=True) if hasattr(app, "getboolean") else True,
    )
    base_url = (os.getenv("LLM_BASE_URL", "").strip() or provider.get("base_url", "https://api.openai.com/v1").strip())
    allow_insecure_http = _env_bool(
        "LLM_ALLOW_INSECURE_HTTP",
        security.getboolean("allow_insecure_http", fallback=False) if hasattr(security, "getboolean") else False,
    )
    for key in ("report_dir", "parquet_path", "output_dir", "cache_dir"):
        settings.setdefault("paths", {})[key] = ROOT_DIR / settings["paths"][key]
    settings["env"] = {
        "tushare_token": (os.getenv("TUSHARE_TOKEN", "") or tushare_config.get("token", "")).strip(),
        "llm_provider": provider_name,
        "llm_base_url": base_url,
        "llm_api_key": (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or config_api_key).strip(),
        "llm_model": os.getenv("LLM_MODEL", app.get("model", "gpt-5.5")).strip(),
        "llm_reasoning_effort": os.getenv("LLM_REASONING_EFFORT", app.get("reasoning_effort", "max")).strip().lower(),
        "llm_request_timeout_seconds": max(
            1,
            int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", app.get("request_timeout_seconds", "300"))),
        ),
        "llm_review_model": os.getenv("LLM_REVIEW_MODEL", app.get("review_model", "gpt-5.5")).strip(),
        "llm_wire_api": os.getenv("LLM_WIRE_API", provider.get("wire_api", "responses")).strip().lower(),
        "llm_store_responses": _env_bool("LLM_STORE_RESPONSES", not disable_response_storage),
        "llm_allow_insecure_http": allow_insecure_http,
        "llm_requires_auth": provider.getboolean("requires_openai_auth", fallback=True) if hasattr(provider, "getboolean") else True,
        "llm_base_scheme": urlsplit(base_url).scheme.lower(),
    }
    return settings
