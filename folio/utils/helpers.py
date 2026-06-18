"""Common utility helpers."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from flask import has_request_context, session

_LOCALES_CACHE: dict[str, dict] = {}
_SUPPORTED_LANGS = {"en", "de"}
_DEFAULT_LANG = "en"


def _locales_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "locales"


def _load_locale(lang: str) -> dict:
    normalized_lang = lang if lang in _SUPPORTED_LANGS else _DEFAULT_LANG
    if normalized_lang in _LOCALES_CACHE:
        return _LOCALES_CACHE[normalized_lang]

    locale_path = _locales_dir() / f"{normalized_lang}.json"
    try:
        with locale_path.open("r", encoding="utf-8") as locale_file:
            payload = json.load(locale_file)
    except (OSError, json.JSONDecodeError):
        payload = {}

    _LOCALES_CACHE[normalized_lang] = payload
    return payload


def get_current_language(default: str = _DEFAULT_LANG) -> str:
    if not has_request_context():
        return default
    lang = session.get("lang", default)
    if lang not in _SUPPORTED_LANGS:
        return default
    return lang


def translate(key: str, lang: str | None = None) -> str:
    """Translate a dot-notation key for the active or requested language."""
    selected_lang = lang or get_current_language()
    translations = _load_locale(selected_lang)
    value: object = translations

    for part in key.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return key

    return value if isinstance(value, str) else key


def get_locale_payload(lang: str) -> dict:
    return _load_locale(lang)


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
