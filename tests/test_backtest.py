"""Backtest techniki i ocena historii sygnałów na danych syntetycznych."""
import time
import unittest

import numpy as np
import pandas as pd

from radar.backtest import backtest_technical, evaluate_history
from radar.signals import Signal
from radar.storage import Store


def universe(drifts, n=1500, seed=0, vol=0.015):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    return {f"S{i}": pd.Series(100 * np.exp(np.cumsum(rng.normal(d, vol, n))), index=idx)
            for i, d in enumerate(drifts)}


class TechnicalBacktest(unittest.TestCase):
    def test_score_sees_only_the_past_and_entry_is_next_session(self):
        seen = []

        def spy(df):                                  # zapisuje ostatnią datę, którą widział score
            seen.append(df.index[-1])
            return 0.0

        res = backtest_technical(universe([0.0] * 6, n=700), horizon=21, score_fn=spy)
        dates = res.observations["date"]
        self.assertEqual(len(seen), len(dates))
        self.assertTrue(all(s <= d for s, d in zip(seen, dates, strict=True)))

    def test_persistent_trends_give_positive_ic(self):
        drifts = np.linspace(-0.0015, 0.0015, 12)      # trend trwały w czasie -> momentum działa
        res = backtest_technical(universe(drifts), horizon=63)
        self.assertGreater(res.ic_mean, 0.2)
        self.assertGreater(res.ic_t, 3)
        table = res.by_action()
        self.assertGreater(table.loc["BUY", "sredni_zwrot_nadwyzkowy"], table.loc["AVOID", "sredni_zwrot_nadwyzkowy"])

    def test_random_walk_gives_no_signal(self):
        res = backtest_technical(universe([0.0] * 12, seed=7), horizon=63)
        self.assertLess(abs(res.ic_t), 3)

    def test_excess_returns_are_relative_to_watchlist_average(self):
        res = backtest_technical(universe(np.linspace(-0.001, 0.001, 6)), horizon=63)
        sums = res.observations.groupby("date")["excess"].sum()
        self.assertTrue(np.allclose(sums, 0))

    def test_too_little_data_is_an_error(self):
        with self.assertRaises(ValueError):
            backtest_technical(universe([0.0] * 3, n=700), horizon=21)


class History(unittest.TestCase):
    def test_weekly_dedup_horizon_filter_and_excess(self):
        day = 86400
        t0 = pd.Timestamp("2026-01-05").timestamp()                  # poniedziałek
        rows = [{"ts": t0 + k * day, "symbol": s, "horizon": "long", "action": a, "price": 100.0}
                for k in range(5) for s, a in (("UP", "BUY"), ("DN", "AVOID"))]
        rows.append({"ts": time.time(), "symbol": "UP", "horizon": "long", "action": "BUY", "price": 100.0})
        idx = pd.bdate_range("2026-01-01", "2026-06-30")
        closes = {"UP": pd.Series(np.linspace(100, 130, len(idx)), index=idx),
                  "DN": pd.Series(np.linspace(100, 90, len(idx)), index=idx)}
        res = evaluate_history(rows, closes, days=91)
        self.assertEqual(len(res.observations), 2)                   # 5 dni -> 1 tydzień; świeży sygnał pominięty
        t = res.by_action()
        self.assertGreater(t.loc[("long", "BUY"), "sredni_zwrot_nadwyzkowy"], 0)
        self.assertLess(t.loc[("long", "AVOID"), "sredni_zwrot_nadwyzkowy"], 0)

    def test_store_exposes_saved_signals(self):
        store = Store(":memory:")
        sig = Signal(symbol="X", name="X", kind="stock", market="USA", sector="T", horizon="long",
                     action="BUY", score=0.5, confidence=0.6, price=12.5)
        store.save_run([sig], {})
        (row,) = store.signal_rows()
        self.assertEqual((row["symbol"], row["action"], row["price"]), ("X", "BUY", 12.5))


if __name__ == "__main__":
    unittest.main()
