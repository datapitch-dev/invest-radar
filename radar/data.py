"""Dostawcy danych: ceny/fundamenty (Yahoo Finance przez yfinance) i newsy (RSS Google News).

Importy yfinance/feedparser są leniwe, żeby reszta kodu dała się testować bez nich.
Dane z darmowych źródeł bywają opóźnione lub niepełne - traktuj je jako pomoc, nie wyrocznię.
"""
from __future__ import annotations

import calendar
import time
import urllib.parse
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: float  # unix ts

    def to_dict(self) -> dict:
        return asdict(self)


class YahooProvider:
    def prices(self, symbol: str, period: str = "2y") -> pd.DataFrame:
        import yfinance as yf
        df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
        if df is None or df.empty:
            raise ValueError(f"Brak notowań dla {symbol}")
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df[["Open", "High", "Low", "Close", "Volume"]]

    def info(self, symbol: str) -> dict:
        import yfinance as yf
        try:
            return dict(yf.Ticker(symbol).info or {})
        except Exception:
            return {}


class GoogleNewsProvider:
    BASE = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{lang}"

    def __init__(self, lang: str = "pl", country: str = "PL", max_age_days: int = 7):
        self.lang, self.country, self.max_age_days = lang, country, max_age_days

    def search(self, query: str, max_items: int = 15, lang: str | None = None,
               country: str | None = None) -> list[NewsItem]:
        import feedparser
        lang, country = lang or self.lang, country or self.country
        url = self.BASE.format(q=urllib.parse.quote(f"{query} when:{self.max_age_days}d"),
                               hl=lang, gl=country, lang=lang)
        feed = feedparser.parse(url)
        cutoff = time.time() - self.max_age_days * 86400
        items, seen = [], set()
        for e in feed.entries:
            ts = calendar.timegm(e.published_parsed) if getattr(e, "published_parsed", None) else time.time()
            title = (e.get("title") or "").strip()
            key = title.lower()[:80]
            if not title or key in seen or ts < cutoff:
                continue
            seen.add(key)
            src = (e.get("source") or {}).get("title", "") if hasattr(e.get("source", {}), "get") else ""
            items.append(NewsItem(title, e.get("link", ""), src, ts))
        items.sort(key=lambda i: i.published, reverse=True)
        return items[:max_items]
