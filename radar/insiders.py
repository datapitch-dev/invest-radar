"""Transakcje insiderów jako dodatkowy sygnał.

- USA: formularze SEC Form 4 (EDGAR, darmowe). Liczymy tylko zakupy/sprzedaże na wolnym rynku
  (kody P i S), pomijamy np. przyznane opcje czy akcje przyznane w wynagrodzeniu.
- GPW: PRZYBLIŻONE - klasyfikacja nagłówków newsów o transakcjach osób zarządzających (art. 19 MAR).
  To nie jest oficjalny feed; waga takiego sygnału jest zmniejszona o połowę. Docelowo warto podpiąć
  źródło ESPI/EBI po sprawdzeniu, co jest dostępne.

Insiderzy kupują z jednego powodu (liczą na wzrost), a sprzedają z wielu (podatki, dywersyfikacja,
plan 10b5-1), więc zakupy ważą w score wyraźnie więcej niż sprzedaże.
Dane są opóźnione (Form 4: do 2 dni roboczych) - to kontekst, nie przewaga czasowa.
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

SEC_UA_ENV = "SEC_USER_AGENT"   # SEC wymaga identyfikacji, np. "Jan Kowalski jan@example.com"


# ---------------------------------------------------------------- SEC Form 4
def parse_form4(xml_text: str) -> list[dict]:
    """Wyciąga transakcje z XML formularza 4. Zwraca tylko kody P (zakup) i S (sprzedaż)."""
    root = ET.fromstring(xml_text)

    def txt(node, path):
        el = node.find(path)
        return el.text.strip() if el is not None and el.text else None

    owner = txt(root, "reportingOwner/reportingOwnerId/rptOwnerName") or "?"
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    role = ""
    if rel is not None:
        title = txt(rel, "officerTitle")
        if title:
            role = title
        elif txt(rel, "isDirector") in ("1", "true"):
            role = "Director"
        elif txt(rel, "isTenPercentOwner") in ("1", "true"):
            role = "10% owner"

    out = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = txt(t, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        try:
            shares = float(txt(t, "transactionAmounts/transactionShares/value") or 0)
            price = float(txt(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        out.append({"date": txt(t, "transactionDate/value"), "insider": owner, "role": role,
                    "code": code, "shares": shares, "price": price, "value": shares * price})
    return out


class SecEdgarInsiders:
    """Wymaga biblioteki requests i zmiennej SEC_USER_AGENT."""

    def __init__(self, store=None, user_agent: str | None = None, max_filings: int = 12, pause: float = 0.15):
        self.ua = user_agent or os.getenv(SEC_UA_ENV)
        if not self.ua:
            raise RuntimeError(f"Ustaw {SEC_UA_ENV} w .env (np. 'Imię Nazwisko email@domena.pl') - wymaga tego SEC.")
        self.store, self.max_filings, self.pause = store, max_filings, pause

    def _get(self, url: str):
        import requests
        time.sleep(self.pause)                                  # limit SEC: max ~10 zapytań/s
        r = requests.get(url, headers={"User-Agent": self.ua}, timeout=20)
        r.raise_for_status()
        return r

    def _cik(self, symbol: str) -> int | None:
        data = self.store.get_json("sec:tickers", ttl_hours=24 * 7) if self.store else None
        if data is None:
            raw = self._get("https://www.sec.gov/files/company_tickers.json").json()
            data = {v["ticker"].upper(): v["cik_str"] for v in raw.values()}
            if self.store:
                self.store.set_json("sec:tickers", data)
        return data.get(symbol.upper().replace("-", "."))

    def fetch(self, symbol: str, days: int = 90) -> list[dict]:
        cik = self._cik(symbol)
        if cik is None:
            return []
        sub = self._get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()
        rec = sub["filings"]["recent"]
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        events, used = [], 0
        for form, date, acc, doc in zip(rec["form"], rec["filingDate"], rec["accessionNumber"],
                                        rec["primaryDocument"], strict=True):
            if form != "4" or date < cutoff:
                continue
            if used >= self.max_filings:
                break
            used += 1
            raw_doc = doc.split("/")[-1]                        # surowy XML zamiast widoku XSLT
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{raw_doc}"
            try:
                for e in parse_form4(self._get(url).text):
                    e["filing_url"] = url
                    events.append(e)
            except Exception:
                continue
        return events


# ------------------------------------------------------- GPW (przybliżone, newsy)
# Tylko osoby zarządzające i nadzorujące (art. 19 MAR). Celowo bez gołego "zarząd" (łapało "Zarząd skupuje akcje
# własne") i bez "akcjonariusz" (fundusz zwiększający udział nie jest insiderem).
_ROLE = re.compile(r"prezes|członek zarządu|członka zarządu|członkini zarządu|rady nadzorczej|insider|"
                   r"art\.? ?19|\bMAR\b|osob[aył] zarządzając|wiceprezes", re.I)
_BUYBACK = re.compile(r"akcj\w* własn|skup\w* akcji własn|buyback", re.I)   # skup przez spółkę, nie przez osobę
_BUY = re.compile(r"kupi[łl]|kupuje|kupił[ai]|zakup akcji|nabył|nabycie akcji|dokupi|zwiększa udział|skupuje", re.I)
_SELL = re.compile(r"sprzeda[łl]|sprzedaje|sprzedaż akcji|zbył|zbycie akcji|redukuje udział", re.I)


def classify_pl_headline(title: str) -> str | None:
    """'P' (zakup), 'S' (sprzedaż) albo None. Wymaga kontekstu insiderskiego w nagłówku."""
    if not _ROLE.search(title) or _BUYBACK.search(title):
        return None
    buy, sell = bool(_BUY.search(title)), bool(_SELL.search(title))
    if buy == sell:                    # oba albo żaden = niejednoznaczne
        return None
    return "P" if buy else "S"


class PolishInsiderNews:
    def __init__(self, news):
        self.news = news

    def fetch(self, name: str, days: int = 90) -> list[dict]:
        events, seen = [], set()
        for q in (f"{name} transakcja osoby zarządzające zakup akcji",
                  f"{name} insider sprzedaż akcji prezes członek zarządu"):
            for it in self.news.search(q, max_items=15):
                code = classify_pl_headline(it.title)
                key = it.title.lower()[:80]
                if code and key not in seen and it.published >= time.time() - days * 86400:
                    seen.add(key)
                    events.append({"date": datetime.fromtimestamp(it.published).strftime("%Y-%m-%d"),
                                   "insider": "?", "role": "", "code": code, "shares": 0.0, "price": 0.0,
                                   "value": 0.0, "headline": it.title, "filing_url": it.link})
        return events


# ------------------------------------------------------------- podsumowanie
def summarize(events: list[dict], approximate: bool = False) -> dict:
    """Score w zakresie -0.5..+1: klaster zakupów różnych osób to najmocniejszy sygnał."""
    buys = [e for e in events if e["code"] == "P"]
    sells = [e for e in events if e["code"] == "S"]

    def n_people(es):
        named = {e["insider"] for e in es if e["insider"] != "?"}
        return len(named) if named else len(es)       # newsy bez nazwisk: liczymy sztuki

    nb, ns = n_people(buys), n_people(sells)
    score = max(-0.5, min(1.0, 0.4 * min(nb, 3) - 0.1 * min(ns, 5)))
    return {"score": float(score), "buyers": nb, "sellers": ns,
            "buy_value": sum(e["value"] for e in buys), "sell_value": sum(e["value"] for e in sells),
            "n_events": len(events), "approximate": approximate,
            "events": sorted(events, key=lambda e: e.get("date") or "", reverse=True)[:8]}


class InsiderService:
    """Wybiera źródło po rynku instrumentu: USA -> SEC, GPW -> newsy (przybliżone), reszta -> brak."""

    def __init__(self, sec=None, pl=None, days: int = 90):
        self.sec, self.pl, self.days = sec, pl, days

    def analyze(self, meta: dict, store=None, ttl_hours: float = 12) -> dict | None:
        if meta.get("kind", "stock") != "stock":
            return None
        market, sym = meta.get("market"), meta["symbol"]
        if market == "USA" and self.sec:
            fetch, approx = (lambda: self.sec.fetch(sym, self.days)), False
        elif market == "GPW" and self.pl:
            fetch, approx = (lambda: self.pl.fetch(meta.get("name", sym), self.days)), True
        else:
            return None                                  # brak źródła dla tego rynku (wybór przed cache)
        key = f"ins:{sym}"
        if store is not None:
            cached = store.get_json(key, ttl_hours)
            if cached is not None:
                return cached
        res = summarize(fetch(), approximate=approx)
        if store is not None:
            store.set_json(key, res)
        return res
