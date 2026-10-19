"""Wspólne dla dashboardu i CLI: ładowanie configu i składanie dostawców."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .ai import ClaudeAnalyzer, NoAI
from .data import GoogleNewsProvider, YahooProvider
from .storage import Store

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_store() -> Store:
    return Store(ROOT / "data" / "radar.db")


def make_analyzer(use_ai: bool = True):
    key = os.getenv("ANTHROPIC_API_KEY")
    if use_ai and key:
        return ClaudeAnalyzer(api_key=key)
    return NoAI()


def make_providers(cfg: dict):
    max_age = cfg.get("settings", {}).get("news_max_age_days", 7)
    return YahooProvider(), GoogleNewsProvider(max_age_days=max_age)


def make_insiders(cfg: dict, news, store):
    """Zwraca InsiderService albo None, gdy wyłączone w config.yaml (settings.insiders: false)."""
    settings = cfg.get("settings", {})
    if not settings.get("insiders", True):
        return None
    from .insiders import InsiderService, PolishInsiderNews, SecEdgarInsiders
    sec = None
    if os.getenv("SEC_USER_AGENT"):
        sec = SecEdgarInsiders(store=store)
    return InsiderService(sec=sec, pl=PolishInsiderNews(news), days=settings.get("insider_days", 90))
