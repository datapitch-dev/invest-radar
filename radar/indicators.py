"""Wskaźniki techniczne i scoring techniczny (-1 = bardzo słabo, +1 = bardzo mocno)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(~((loss == 0) & gain.notna()), 100.0)


def max_drawdown(close: pd.Series) -> float:
    """Największy spadek od szczytu (wartość ujemna, np. -0.35)."""
    peak = close.cummax()
    return float((close / peak - 1).min())


def annual_vol(close: pd.Series) -> float:
    rets = close.pct_change().dropna()
    if len(rets) < 20:
        return float("nan")
    return float(rets.std() * math.sqrt(252))


def technical_score(df: pd.DataFrame) -> dict:
    """Zwraca {'score': float|None, 'details': {...}, 'risk': {...}}.

    Wymaga kolumny 'Close' i min. 60 sesji; bez tego score=None.
    """
    if df is None or "Close" not in df or len(df.dropna(subset=["Close"])) < 60:
        return {"score": None, "details": {}, "risk": {}}

    close = df["Close"].dropna().astype(float)
    last = float(close.iloc[-1])
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else float("nan")
    r = rsi(close).iloc[-1]

    def ret(n: int) -> float:
        return float(close.iloc[-1] / close.iloc[-n - 1] - 1) if len(close) > n else float("nan")

    r3, r6 = ret(63), ret(126)

    parts: list[tuple[float, float]] = []  # (wynik, waga)
    details: dict = {"last": last, "sma50": float(sma50), "rsi": float(r) if pd.notna(r) else None,
                     "ret_3m": r3, "ret_6m": r6}

    parts.append((_clip((last / sma50 - 1) / 0.08), 1.0))
    if pd.notna(sma200):
        details["sma200"] = float(sma200)
        parts.append((_clip((last / sma200 - 1) / 0.15), 1.0))
        parts.append((1.0 if sma50 > sma200 else -1.0, 0.7))
    if pd.notna(r3):
        parts.append((math.tanh(r3 / 0.15), 1.2))
    if pd.notna(r6):
        parts.append((math.tanh(r6 / 0.25), 1.0))
    if pd.notna(r):
        if r > 75:
            parts.append((-0.6, 0.8))      # wykupienie
        elif r > 65:
            parts.append((-0.2, 0.5))
        elif r < 25:
            parts.append((0.4, 0.8))       # wyprzedanie (kontrariańsko, mała waga)
        elif r < 35:
            parts.append((0.15, 0.5))

    wsum = sum(w for _, w in parts)
    score = _clip(sum(s * w for s, w in parts) / wsum)

    vol = annual_vol(close)
    dd = max_drawdown(close.iloc[-252:])
    return {"score": float(score), "details": details,
            "risk": {"annual_vol": vol, "max_drawdown_1y": dd}}
