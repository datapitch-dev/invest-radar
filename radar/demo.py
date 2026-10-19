"""Tryb demo: syntetyczne notowania, fundamenty i nagłówki - bez internetu i bez klucza API.

Służy do pokazania projektu od razu po instalacji (`python scan.py --demo`, `RADAR_DEMO=1 streamlit run app.py`).
Wszystkie liczby są wygenerowane deterministycznie z nazwy symbolu - to NIE są prawdziwe dane rynkowe.
Sentyment newsów liczy prosty słownik zamiast Claude'a, więc demo nie kosztuje nic i działa offline.
"""
from __future__ import annotations

import time
import zlib

import numpy as np
import pandas as pd

from .data import NewsItem

DEMO_END = "2026-10-16"

_POSITIVE = ("powyżej oczekiwań", "rekordowe", "podnosi prognozę", "nowy kontrakt", "rośnie")
_NEGATIVE = ("poniżej oczekiwań", "obniża prognozę", "spadek", "kara", "traci")
_TEMPLATES = (
    ("{q}: wyniki kwartalne powyżej oczekiwań analityków", 0),
    ("{q} podnosi prognozę na cały rok", 0),
    ("{q} podpisuje nowy kontrakt wart setki milionów", 0),
    ("{q}: przychody poniżej oczekiwań, zarząd obniża prognozę", 1),
    ("{q} traci po decyzji regulatora, możliwa kara", 1),
    ("{q}: walne zgromadzenie zatwierdziło sprawozdanie", 2),
    ("{q} zaprasza na dzień inwestora", 2),
)


def _rng(key: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(key.encode()))


class DemoPrices:
    """Ten sam interfejs co YahooProvider: .prices(symbol) i .info(symbol)."""

    def __init__(self, sessions: int = 600, end: str = DEMO_END):
        self.sessions, self.end = sessions, end

    def prices(self, symbol: str, period: str = "2y") -> pd.DataFrame:
        rng = _rng(symbol)
        drift = rng.uniform(-0.0010, 0.0014)                  # roczny trend od ok. -22% do +42%
        vol = rng.uniform(0.010, 0.024)
        rets = rng.normal(drift, vol, self.sessions)
        close = rng.uniform(20, 400) * np.exp(np.cumsum(rets))
        spread = np.abs(rng.normal(0, vol / 2, self.sessions))
        idx = pd.bdate_range(end=self.end, periods=self.sessions)
        return pd.DataFrame({"Open": close * (1 + rng.normal(0, vol / 4, self.sessions)),
                             "High": close * (1 + spread), "Low": close * (1 - spread), "Close": close,
                             "Volume": rng.integers(10_000, 2_000_000, self.sessions)}, index=idx)

    def info(self, symbol: str) -> dict:
        rng = _rng("info:" + symbol)
        return {"forwardPE": float(rng.uniform(-5, 40)), "returnOnEquity": float(rng.uniform(-0.1, 0.35)),
                "profitMargins": float(rng.uniform(-0.05, 0.3)), "revenueGrowth": float(rng.uniform(-0.2, 0.4)),
                "earningsGrowth": float(rng.uniform(-0.4, 0.6)), "debtToEquity": float(rng.uniform(10, 300))}


class DemoNews:
    """Ten sam interfejs co GoogleNewsProvider: .search(query, max_items)."""

    def search(self, query: str, max_items: int = 15, lang: str | None = None,
               country: str | None = None) -> list[NewsItem]:
        rng = _rng("news:" + query)
        picks = rng.choice(len(_TEMPLATES), size=3, replace=False)
        now = time.time()
        return [NewsItem(_TEMPLATES[i][0].format(q=query), "https://example.com/demo", "Demo",
                         now - (k + 1) * 3 * 3600) for k, i in enumerate(sorted(picks))][:max_items]


class DemoAnalyzer:
    """Zamiast Claude'a: sentyment z prostego słownika. Bez weryfikacji (tylko AI mogłoby ją zrobić)."""

    model = None            # brak modelu = pipeline nie cache'uje (i tak jest deterministyczne)

    def analyze_news(self, meta: dict, headlines: list[str]) -> dict:
        if not headlines:
            return {"sentiment": None, "confidence": 0.0, "summary": "Brak świeżych newsów.", "events": []}
        text = " ".join(headlines).lower()
        pos, neg = sum(w in text for w in _POSITIVE), sum(w in text for w in _NEGATIVE)
        sentiment = (pos - neg) / max(1, pos + neg)
        return {"sentiment": sentiment, "confidence": 0.5, "events": headlines[:2],
                "summary": "Demo: sentyment policzony słownikiem, nie przez AI."}

    def analyze_macro(self, headlines: list[str], sectors: list[str]) -> dict:
        tilts = {s: round(float(_rng("tilt:" + s).uniform(-0.5, 0.5)), 2) for s in sectors}
        return {"risk_sentiment": 0.1, "sector_tilts": tilts,
                "summary": "Tryb demo: dane syntetyczne, bez połączenia z internetem i bez AI."}

    def verify(self, meta, horizon, payload):
        return {}
