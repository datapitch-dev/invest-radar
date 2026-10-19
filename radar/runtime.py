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


def load_config(path: str | Path | None = None, demo: bool = False) -> dict:
    default = ROOT / ("config.demo.yaml" if demo else "config.yaml")
    with open(path or default, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_demo() -> bool:
    """Tryb demo włącza zmienna RADAR_DEMO=1 albo flaga --demo (CLI i `streamlit run app.py -- --demo`)."""
    import sys
    return os.getenv("RADAR_DEMO") == "1" or "--demo" in sys.argv


def make_store(demo: bool = False) -> Store:
    # demo ma osobną bazę, żeby syntetyczne sygnały nie mieszały się z prawdziwą historią
    return Store(ROOT / "data" / ("demo.db" if demo else "radar.db"))


def make_analyzer(use_ai: bool = True, demo: bool = False):
    if demo:
        from .demo import DemoAnalyzer
        return DemoAnalyzer()
    key = os.getenv("ANTHROPIC_API_KEY")
    if use_ai and key:
        return ClaudeAnalyzer(api_key=key)
    return NoAI()


def make_providers(cfg: dict, demo: bool = False):
    if demo:
        from .demo import DemoNews, DemoPrices
        return DemoPrices(), DemoNews()
    max_age = cfg.get("settings", {}).get("news_max_age_days", 7)
    return YahooProvider(), GoogleNewsProvider(max_age_days=max_age)


def make_insiders(cfg: dict, news, store, demo: bool = False):
    """Zwraca InsiderService albo None, gdy wyłączone w config.yaml (settings.insiders: false) lub w demo."""
    settings = cfg.get("settings", {})
    if demo or not settings.get("insiders", True):
        return None
    from .insiders import InsiderService, PolishInsiderNews, SecEdgarInsiders
    sec = None
    if os.getenv("SEC_USER_AGENT"):
        sec = SecEdgarInsiders(store=store)
    return InsiderService(sec=sec, pl=PolishInsiderNews(news), days=settings.get("insider_days", 90))
