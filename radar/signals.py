"""Łączenie składowych (technika, fundamenty, newsy/AI) w sygnał z uzasadnieniem."""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

DEFAULT_WEIGHTS = {
    # horyzont: (technika, fundamenty, newsy+makro)
    "long": {"tech": 0.25, "fund": 0.45, "news": 0.30},
    "medium": {"tech": 0.40, "fund": 0.20, "news": 0.40},
}

ACTIONS = ["BUY", "ACCUMULATE", "HOLD", "REDUCE", "AVOID"]
ACTION_PL = {"BUY": "KUP", "ACCUMULATE": "DOKUPUJ", "HOLD": "TRZYMAJ",
             "REDUCE": "OGRANICZ", "AVOID": "UNIKAJ"}
HORIZON_PL = {"long": "długi (lata)", "medium": "średni (tyg.–mies.)"}


@dataclass
class Signal:
    symbol: str
    name: str
    kind: str
    market: str
    sector: str
    horizon: str
    action: str
    score: float
    confidence: float
    tech_score: float | None = None
    fund_score: float | None = None
    news_score: float | None = None
    price: float | None = None
    rationale: str = ""
    risks: list[str] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    insider: dict = field(default_factory=dict)
    news: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    as_of: str = ""
    base_action: str = ""      # akcja wynikająca z samego score (przed obniżeniami) - punkt odniesienia histerezy

    def to_dict(self) -> dict:
        return asdict(self)


_INF = float("inf")
ACTION_RANGES = {"BUY": (0.35, _INF), "ACCUMULATE": (0.15, 0.35), "HOLD": (-0.15, 0.15),
                 "REDUCE": (-0.35, -0.15), "AVOID": (-_INF, -0.35)}


def action_from_score(score: float, prev: str | None = None, band: float = 0.0) -> str:
    """Histereza: poprzednia akcja zostaje, dopóki score nie wyjdzie poza jej przedział poszerzony o `band`
    - score oscylujący wokół progu nie przełącza sygnału (i nie generuje alertów) co skan."""
    if prev in ACTION_RANGES and band > 0:
        lo, hi = ACTION_RANGES[prev]
        if lo - band <= score <= hi + band:
            return prev
    if score >= 0.35:
        return "BUY"
    if score >= 0.15:
        return "ACCUMULATE"
    if score > -0.15:
        return "HOLD"
    if score > -0.35:
        return "REDUCE"
    return "AVOID"


def combine(tech, fund, news, horizon: str, weights: dict | None = None,
           fund_applicable: bool = True) -> tuple[float, float]:
    """Zwraca (score, confidence). Brakujące składowe są pomijane, a pewność maleje.

    confidence = siła sygnału * zgodność składowych * pokrycie wagami.

    `fund_applicable=False` (ETF-y i inne instrumenty bez fundamentów z definicji) wyłącza wagę
    fundamentów z mianownika pokrycia - brak fundamentów tam, gdzie strukturalnie nie mogą istnieć,
    nie powinien obniżać pewności tak samo jak brak newsów czy cen, czyli dane, które powinny być.
    """
    w = (weights or DEFAULT_WEIGHTS)[horizon]
    comps = {"tech": tech, "fund": fund, "news": news}
    present = {k: v for k, v in comps.items() if v is not None}
    if not present:
        return 0.0, 0.0
    wsum = sum(w[k] for k in present)
    score = sum(w[k] * v for k, v in present.items()) / wsum
    applicable_total = sum(v for k, v in w.items() if k != "fund" or fund_applicable)
    coverage = wsum / applicable_total

    if len(present) > 1:
        spread = statistics.pstdev(present.values())      # 0 = pełna zgodność
        agreement = max(0.0, 1.0 - spread)
    else:
        agreement = 0.6                                    # jedno źródło = niska wiarygodność
    strength = min(1.0, abs(score) * 1.8)
    confidence = strength * (0.4 + 0.6 * agreement) * coverage
    return float(score), float(min(1.0, confidence))


def apply_verification(action: str, confidence: float, verification: dict | None) -> tuple[str, float]:
    """Drugi przebieg AI może tylko zmniejszyć przekonanie, nigdy go zwiększyć."""
    if not verification:
        return action, confidence
    verdict = verification.get("verdict")
    if verdict == "reject":
        return ("HOLD" if action in ("BUY", "ACCUMULATE", "REDUCE", "AVOID") else action), confidence * 0.4
    if verdict == "caution":
        return action, confidence * 0.7
    return action, confidence


def risk_flags(risk: dict) -> list[str]:
    flags = []
    vol, dd = risk.get("annual_vol"), risk.get("max_drawdown_1y")
    if vol is not None and vol == vol and vol > 0.45:
        flags.append(f"Wysoka zmienność ({vol:.0%} rocznie)")
    if dd is not None and dd == dd and dd < -0.30:
        flags.append(f"Głęboka korekta w ostatnim roku ({dd:.0%} od szczytu)")
    return flags


def apply_insider(score: float, conf: float, insider: dict | None,
                  weight: float = 0.15) -> tuple[float, float]:
    """Insiderzy to nakładka na wynik, nie pełna składowa: większość spółek nie ma świeżych transakcji,
    a zero w roli składowej rozwadniałoby cały score. Dane przybliżone (GPW z newsów) ważą o połowę mniej.
    Gdy insiderzy przeczą kierunkowi sygnału, spada pewność."""
    if not insider or not insider.get("score"):
        return score, conf
    w = weight * (0.5 if insider.get("approximate") else 1.0)
    adj = w * insider["score"]
    if score * adj < 0:
        conf *= 0.85
    return max(-1.0, min(1.0, score + adj)), conf


def build_signal(meta: dict, horizon: str, tech: dict, fund: dict, news_score,
                 news_analysis: dict | None, verification: dict | None, news_items: list[dict],
                 weights: dict | None = None, min_confidence: float = 0.30,
                 as_of: str = "", insider: dict | None = None, insider_weight: float = 0.15,
                 prev: dict | None = None, hysteresis: float = 0.0) -> Signal:
    """prev: sygnał z poprzedniego skanu ({'action', 'base_action'}) - do histerezy progów score i pewności."""
    prev = prev or {}
    t, f = tech.get("score"), fund.get("score")
    fund_applicable = meta.get("kind", "stock") == "stock"       # ETF-y i inne nigdy nie mają fundamentów
    score, conf = combine(t, f, news_score, horizon, weights, fund_applicable=fund_applicable)
    score, conf = apply_insider(score, conf, insider, insider_weight)
    base_action = action_from_score(score, prev.get("base_action") or prev.get("action"), hysteresis)
    action, conf = apply_verification(base_action, conf, verification)

    flags = risk_flags(tech.get("risk", {}))
    if sum(x is not None for x in (t, f, news_score)) < 2 and action != "HOLD":
        flags.append("Za mało źródeł danych (min. 2 składowe) - sygnał obniżony do TRZYMAJ")
        action = "HOLD"
    # utrzymany sygnał ma próg pewności niższy o `hysteresis`, żeby nie migał wokół min_confidence
    if prev.get("action") == action:
        min_confidence -= hysteresis
    if conf < min_confidence and action != "HOLD":
        flags.append("Za niskie przekonanie - sygnał obniżony do TRZYMAJ")
        action = "HOLD"
    if verification:
        flags += [f"Weryfikacja AI: {o}" for o in verification.get("objections", [])[:3]]

    parts = []
    if t is not None:
        parts.append(f"technika {t:+.2f}")
    if f is not None:
        parts.append(f"fundamenty {f:+.2f}")
    if news_score is not None:
        parts.append(f"newsy/makro {news_score:+.2f}")
    rationale = "Składowe: " + ", ".join(parts) + "." if parts else "Brak danych do oceny."
    if insider and insider.get("n_events"):
        approx = " (przybliżone, z newsów)" if insider.get("approximate") else ""
        rationale += (f" Insiderzy{approx}: {insider['buyers']} kupujących, "
                      f"{insider['sellers']} sprzedających w ostatnich miesiącach.")
    if news_analysis and news_analysis.get("summary"):
        rationale += " " + news_analysis["summary"]

    return Signal(
        symbol=meta["symbol"], name=meta.get("name", meta["symbol"]), kind=meta.get("kind", "stock"),
        market=meta.get("market", ""), sector=meta.get("sector", ""), horizon=horizon,
        action=action, score=score, confidence=conf, tech_score=t, fund_score=f, news_score=news_score,
        price=tech.get("details", {}).get("last"), rationale=rationale, risks=flags,
        verification=verification or {}, insider=insider or {}, news=news_items, as_of=as_of,
        base_action=base_action,
    )
