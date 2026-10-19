"""Orkiestracja: dane -> wskaźniki -> AI -> sygnały -> zapis do bazy."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from collections.abc import Callable

from .fundamentals import fundamental_score
from .indicators import technical_score
from .signals import DEFAULT_WEIGHTS, Signal, build_signal
from .storage import Store

AI_CACHE_TTL_HOURS = 24 * 7     # klucz zawiera hash nagłówków, więc nowe newsy i tak dają nowe zapytanie


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _cached_ai(store: Store, analyzer, kind: str, parts, fn):
    """Te same nagłówki -> ten sam wynik AI. Bez cache każde odświeżenie pytałoby model ponownie,
    a niedeterministyczna odpowiedź potrafi przełączyć sygnał (i wysłać alert) bez nowej informacji."""
    model = getattr(analyzer, "model", None)
    if model is None:                                  # NoAI - nic do cache'owania
        return fn()
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    key = f"ai:{kind}:{model}:{digest}"
    hit = store.get_json(key, AI_CACHE_TTL_HOURS)
    if hit is not None:
        return hit
    res = fn()
    store.set_json(key, res)
    return res


def _rounded(x, digits: int = 4):
    """Zaokrągla liczby w payloadzie dla AI. Ceny po odczycie z cache (JSON) różnią się na dalekich miejscach
    po przecinku; bez zaokrąglenia ten sam sygnał dawałby inny klucz cache i nowe zapytanie do modelu."""
    if isinstance(x, float):
        return round(x, digits) if x == x else None
    if isinstance(x, dict):
        return {k: _rounded(v, digits) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_rounded(v, digits) for v in x]
    return x


def news_component(analysis: dict | None, sector_tilt: float | None, macro_weight: float = 0.25):
    """Łączy ocenę newsów spółki (ważoną pewnością AI) z tilt'em sektorowym z makro."""
    s = analysis.get("sentiment") if analysis else None
    if s is not None:
        s = s * (0.5 + 0.5 * analysis.get("confidence", 0.0))   # niepewna ocena -> bliżej zera
    if s is None and sector_tilt is None:
        return None
    if s is None:
        return _clip(sector_tilt * 0.5)
    if sector_tilt is None:
        return _clip(s)
    return _clip((1 - macro_weight) * s + macro_weight * sector_tilt)


def run_scan(cfg: dict, prices, news, analyzer, store: Store,
             progress: Callable[[float, str], None] | None = None, verify: bool = True,
             insiders=None) -> dict:
    """prices: obiekt z .prices(sym)/.info(sym); news: obiekt z .search(query, ...);
    insiders: opcjonalny InsiderService (.analyze(meta, store)).
    Błąd pojedynczego instrumentu nie przerywa skanu - ląduje w Signal.errors."""
    settings = cfg.get("settings", {})
    insider_weight = settings.get("insider_weight", 0.15)
    ttl = settings.get("cache_ttl_hours", 6)
    weights = cfg.get("weights")
    min_conf = settings.get("min_confidence", 0.30)
    hysteresis = settings.get("hysteresis", 0.05)
    horizons = settings.get("horizons", ["long", "medium"])
    watch = cfg["watchlist"]
    sectors = sorted({w.get("sector") for w in watch if w.get("sector")})
    say = progress or (lambda f, m: None)
    prev_run = store.latest_run(without_ai_errors=True)   # ten sam punkt odniesienia co alerty w scan.py
    prev = {(s["symbol"], s["horizon"]): s for s in prev_run["signals"]} if prev_run else {}
    ai_errors: list[str] = []      # błędy wywołań AI - dashboard i CLI pokazują je wyraźnie

    # --- makro / geopolityka ----------------------------------------------
    say(0.02, "Czytam wiadomości makro i geopolityczne...")
    macro_items: list[dict] = []
    for q in cfg.get("macro_queries", []):
        try:
            macro_items += [i.to_dict() for i in news.search(q, max_items=10)]
        except Exception:      # pojedyncze zapytanie makro może paść - reszta wystarczy
            continue
    seen, uniq = set(), []
    for i in sorted(macro_items, key=lambda x: x["published"], reverse=True):
        if i["title"].lower()[:80] not in seen:
            seen.add(i["title"].lower()[:80])
            uniq.append(i)
    titles = [i["title"] for i in uniq]
    try:
        macro = _cached_ai(store, analyzer, "macro", [titles, sectors],
                           lambda: analyzer.analyze_macro(titles, sectors))
    except Exception as e:
        macro = {"risk_sentiment": 0.0, "sector_tilts": {}, "summary": f"Błąd analizy makro: {e}"}
        ai_errors.append(f"makro: {e}")
    macro["headlines"] = uniq[:15]

    # --- instrumenty ------------------------------------------------------
    out: list[Signal] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for n, meta in enumerate(watch):
        sym = meta["symbol"]
        say(0.05 + 0.9 * n / len(watch), f"Analizuję {meta.get('name', sym)} ({n + 1}/{len(watch)})")
        errors: list[str] = []

        df = store.get_df(f"px:{sym}", ttl)
        if df is None:
            try:
                df = prices.prices(sym)
                store.set_df(f"px:{sym}", df)
            except Exception as e:
                errors.append(f"ceny: {e}")
        tech = technical_score(df) if df is not None else {"score": None, "details": {}, "risk": {}}

        info = store.get_json(f"info:{sym}", ttl * 4)
        if info is None and meta.get("kind", "stock") == "stock":
            try:
                info = prices.info(sym)
                store.set_json(f"info:{sym}", info)
            except Exception as e:
                errors.append(f"fundamenty: {e}")
        fund = fundamental_score(info if meta.get("kind", "stock") == "stock" else None)

        items = store.get_json(f"news:{sym}", ttl)
        if items is None:
            try:
                items = [i.to_dict() for i in news.search(meta.get("query") or meta.get("name") or sym, max_items=15)]
                store.set_json(f"news:{sym}", items)
            except Exception as e:
                items = []
                errors.append(f"newsy: {e}")
        headlines = [i["title"] for i in items]
        try:
            analysis = _cached_ai(store, analyzer, "news",
                                  [sym, meta.get("name"), meta.get("sector"), meta.get("market"), headlines],
                                  lambda: analyzer.analyze_news(meta, headlines))
        except Exception as e:
            analysis = None
            errors.append(f"AI newsy: {e}")
            ai_errors.append(f"{sym} newsy: {e}")
        tilt = macro.get("sector_tilts", {}).get(meta.get("sector"))
        n_score = news_component(analysis, tilt)

        insider = None
        if insiders is not None:
            try:
                insider = insiders.analyze(meta, store)
            except Exception as e:
                errors.append(f"insiderzy: {e}")

        for hz in horizons:
            opts = dict(weights=weights, min_confidence=min_conf, as_of=now, insider=insider,
                        insider_weight=insider_weight, prev=prev.get((sym, hz)), hysteresis=hysteresis)
            sig = build_signal(meta, hz, tech, fund, n_score, analysis, None, items[:8], **opts)
            # weryfikujemy gotowy sygnał (recenzent musi go widzieć) i tylko taki, który coś każe zrobić
            if verify and sig.action != "HOLD":
                payload = {"sygnal": {"akcja": sig.action, "score": round(sig.score, 3),
                                      "pewnosc": round(sig.confidence, 3),
                                      "wagi": (weights or DEFAULT_WEIGHTS)[hz], "flagi_ryzyka": sig.risks},
                           "technika": tech.get("score"), "szczegoly_techniki": tech.get("details"),
                           "ryzyko": tech.get("risk"), "fundamenty": fund, "newsy": analysis,
                           "tilt_sektora": tilt, "nastroje_rynku": macro.get("risk_sentiment"),
                           "insiderzy": {k: insider[k] for k in ("score", "buyers", "sellers", "approximate")}
                           if insider else None}
                payload = _rounded(payload)
                try:
                    # cache jak przy newsach: ten sam sygnał i te same dane -> ten sam werdykt, więc
                    # niedeterministyczna odpowiedź nie przełącza KUP/TRZYMAJ (i nie wysyła alertu) co skan
                    verification = _cached_ai(store, analyzer, "verify", [sym, hz, payload],
                                              lambda: analyzer.verify(meta, hz, payload)) or None
                except Exception as e:
                    verification = None
                    errors.append(f"weryfikacja AI: {e}")
                    ai_errors.append(f"{sym} weryfikacja: {e}")
                if verification:
                    sig = build_signal(meta, hz, tech, fund, n_score, analysis, verification, items[:8], **opts)
            sig.errors = list(errors)
            out.append(sig)

    macro["ai_errors"] = ai_errors
    say(1.0, "Gotowe")
    run_id = store.save_run(out, macro)
    return {"run_id": run_id, "signals": out, "macro": macro, "ts": time.time()}
