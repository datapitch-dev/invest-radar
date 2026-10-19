"""Sprawdzian, czy sygnały niosą informację: backtest techniki i ocena zapisanej historii sygnałów.

1. `backtest_technical` - test wstecz samej składowej technicznej. Tylko ona da się uczciwie przetestować na
   historii: fundamenty pochodzą z bieżącego zrzutu Yahoo (`info`), a RSS Google News nie ma archiwum, więc użycie
   ich na przeszłych datach oznaczałoby, że model „wie” rzeczy z przyszłości. Technika liczy się z samych cen.

   Metoda przekrojowa (jak przy ocenie czynników): co `step` sesji bierzemy datę t; dla każdego instrumentu score
   z cen do t włącznie (to samo okno 2 lat co na żywo), wejście po zamknięciu NASTĘPNEJ sesji (sygnał z zamknięcia
   t nie kupi po tym samym zamknięciu), wyjście `horizon` sesji dalej. Zwrot nadwyżkowy = zwrot minus średnia
   wszystkich instrumentów w tym okresie - benchmark z samej watchlisty, niezależny od waluty i wyboru indeksu.
   IC = korelacja rang (Spearman) między score a zwrotem w przekroju danej daty, uśredniona po datach. Przy
   step >= horizon okresy się nie nakładają, więc błąd standardowy średniej jest uczciwy.

2. `evaluate_history` - test do przodu: sygnały zapisane w bazie przez kolejne skany (wszystkie składowe,
   łącznie z AI) porównane z tym, co cena zrobiła potem.

Ograniczenia obu: bez kosztów transakcyjnych i podatku, zwroty w walutach lokalnych, watchlista wybrana dziś
(survivorship bias). To sprawdzian, czy sygnał w ogóle coś mówi, a nie symulacja portfela.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from .indicators import technical_score
from .signals import ACTIONS, action_from_score

LIVE_WINDOW = 504          # ~2 lata sesji - tyle historii pobiera skan na żywo (YahooProvider, period="2y")


def _tech(df: pd.DataFrame) -> float | None:
    return technical_score(df)["score"]


@dataclass
class EvaluationResult:
    observations: pd.DataFrame     # kolumny: date, symbol, score/action, ret, excess (+ horizon w historii)
    ic_by_date: pd.Series | None = None

    @property
    def ic_mean(self) -> float:
        return float(self.ic_by_date.mean()) if self.ic_by_date is not None and len(self.ic_by_date) else math.nan

    @property
    def ic_se(self) -> float:
        n = 0 if self.ic_by_date is None else len(self.ic_by_date)
        return float(self.ic_by_date.std(ddof=1) / math.sqrt(n)) if n > 1 else math.nan

    @property
    def ic_t(self) -> float:
        return self.ic_mean / self.ic_se if self.ic_se and self.ic_se == self.ic_se else math.nan

    def by_action(self) -> pd.DataFrame:
        """Liczba obserwacji, średni zwrot ponad średnią watchlisty i odsetek dodatnich, per akcja."""
        keys = ["horizon", "action"] if "horizon" in self.observations else ["action"]
        g = self.observations.groupby(keys)["excess"]
        out = pd.DataFrame({"n": g.size(), "sredni_zwrot_nadwyzkowy": g.mean(),
                            "odsetek_dodatnich": g.apply(lambda x: float((x > 0).mean()))})
        order = {a: i for i, a in enumerate(ACTIONS)}
        return out.sort_index(key=lambda idx: idx.map(order) if idx.name == "action" else idx)


def _rank_corr(g: pd.DataFrame) -> float:
    if g["score"].nunique() < 2 or g["ret"].nunique() < 2:
        return math.nan                     # same remisy - korelacja niezdefiniowana
    return float(g["score"].rank().corr(g["ret"].rank()))


def backtest_technical(closes: dict[str, pd.Series], horizon: int = 63, step: int | None = None,
                       min_history: int = 200, min_instruments: int = 5,
                       score_fn: Callable[[pd.DataFrame], float | None] = _tech) -> EvaluationResult:
    """closes: {symbol: seria cen zamknięcia z indeksem dat}. horizon/step w sesjach (63 ≈ 3 miesiące)."""
    step = step or horizon
    closes = {s: c.dropna().sort_index() for s, c in closes.items() if len(c.dropna())}
    if not closes:
        raise ValueError("Brak notowań do backtestu")
    calendar = pd.bdate_range(min(c.index[0] for c in closes.values()), max(c.index[-1] for c in closes.values()))

    rows = []
    for i in range(0, len(calendar) - horizon - 1, step):
        t, t_entry, t_exit = calendar[i], calendar[i + 1], calendar[i + 1 + horizon]
        for sym, c in closes.items():
            hist = c.loc[:t]
            if len(hist) < min_history or (t - hist.index[-1]).days > 7 or c.index[-1] < t_exit:
                continue                    # za krótka historia, instrument nienotowany albo brak ceny wyjścia
            entry = c.loc[t_entry:]
            if entry.empty or entry.index[0] >= t_exit:
                continue
            score = score_fn(pd.DataFrame({"Close": hist.iloc[-LIVE_WINDOW:]}))
            if score is None:
                continue
            rows.append({"date": t, "symbol": sym, "score": float(score),
                         "ret": float(c.asof(t_exit) / entry.iloc[0] - 1)})

    obs = pd.DataFrame(rows, columns=["date", "symbol", "score", "ret"])
    obs = obs[obs.groupby("date")["symbol"].transform("size") >= min_instruments].copy()
    if obs.empty:
        raise ValueError(f"Za mało danych: potrzeba min. {min_instruments} instrumentów z {min_history} sesjami")
    obs["excess"] = obs["ret"] - obs.groupby("date")["ret"].transform("mean")
    obs["action"] = obs["score"].map(action_from_score)
    ic = obs.groupby("date").apply(_rank_corr, include_groups=False)
    return EvaluationResult(obs.reset_index(drop=True), ic.dropna())


def evaluate_history(rows: list[dict], closes: dict[str, pd.Series], days: int = 91) -> EvaluationResult:
    """rows: zapisane sygnały ({'ts', 'symbol', 'horizon', 'action', 'price'}), closes: aktualne notowania.

    Bierze jeden sygnał na instrument, horyzont i tydzień (pierwszy skan w tygodniu) - codzienne skany powtarzają
    tę samą obserwację i sztucznie zawyżałyby jej wagę. Ocenia tylko sygnały starsze niż `days` dni."""
    df = pd.DataFrame(rows, columns=["ts", "symbol", "horizon", "action", "price"]).dropna(subset=["price"])
    if df.empty:
        return EvaluationResult(df.assign(date=[], ret=[], excess=[]))
    df["date"] = pd.to_datetime(df["ts"], unit="s")
    df["week"] = df["date"].dt.to_period("W")
    df = df.sort_values("ts").groupby(["symbol", "horizon", "week"], as_index=False).first()

    exits = []
    for r in df.itertuples():
        c = closes.get(r.symbol)
        exit_at = r.date + pd.Timedelta(days=days)
        ok = c is not None and len(c) and c.index[-1] >= exit_at and r.price > 0
        exits.append(float(c.asof(exit_at) / r.price - 1) if ok else math.nan)
    df["ret"] = exits
    df = df.dropna(subset=["ret"])
    df["excess"] = df["ret"] - df.groupby(["week", "horizon"])["ret"].transform("mean")
    return EvaluationResult(df[["date", "symbol", "horizon", "action", "ret", "excess"]].reset_index(drop=True))
