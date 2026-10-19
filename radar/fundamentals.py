"""Prosty scoring fundamentalny z pól `info` z Yahoo Finance.

Progi są celowo ogólne (bez porównania do branży) - to jest filtr pomocniczy,
nie wycena spółki. Dla ETF-ów zwykle brak danych -> score=None.
"""
from __future__ import annotations

import math


def _num(info: dict, key: str):
    v = info.get(key)
    return float(v) if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)) else None


def fundamental_score(info: dict | None) -> dict:
    """Zwraca {'score': float|None, 'coverage': 0..1, 'details': {metryka: wynik}}."""
    if not info:
        return {"score": None, "coverage": 0.0, "details": {}}

    is_financial = (info.get("sector") or "") in ("Financial Services", "Financials")
    d: dict[str, float] = {}

    pe = _num(info, "forwardPE") or _num(info, "trailingPE")
    if pe is not None:
        d["P/E"] = (-0.5 if pe <= 0 else 0.6 if pe < 12 else 0.2 if pe < 20
                    else -0.2 if pe < 30 else -0.6)
    roe = _num(info, "returnOnEquity")
    if roe is not None:
        d["ROE"] = 0.6 if roe > 0.15 else 0.2 if roe > 0.08 else -0.1 if roe > 0 else -0.7
    pm = _num(info, "profitMargins")
    if pm is not None:
        d["Marża netto"] = 0.5 if pm > 0.15 else 0.1 if pm > 0.05 else -0.1 if pm > 0 else -0.6
    rg = _num(info, "revenueGrowth")
    if rg is not None:
        d["Wzrost przychodów"] = math.tanh(rg / 0.15)
    eg = _num(info, "earningsGrowth")
    if eg is not None:
        d["Wzrost zysku"] = math.tanh(eg / 0.25)
    de = _num(info, "debtToEquity")
    if de is not None and not is_financial:       # dla banków/ubezpieczycieli D/E nie ma sensu
        d["Zadłużenie (D/E)"] = 0.4 if de < 50 else 0.0 if de < 120 else -0.3 if de < 250 else -0.6

    total = 6
    if not d:
        return {"score": None, "coverage": 0.0, "details": {}}
    return {"score": sum(d.values()) / len(d), "coverage": len(d) / total, "details": d}
