"""Tryb demo: działa offline, bez klucza API, i daje powtarzalne wyniki."""
import unittest

from radar.demo import DemoAnalyzer, DemoNews, DemoPrices
from radar.pipeline import run_scan
from radar.runtime import load_config, make_analyzer, make_insiders, make_providers
from radar.storage import Store


class Demo(unittest.TestCase):
    def test_demo_scan_is_offline_and_deterministic(self):
        cfg = load_config(demo=True)
        runs = []
        for _ in range(2):
            res = run_scan(cfg, DemoPrices(), DemoNews(), DemoAnalyzer(), Store(":memory:"))
            runs.append([(s.symbol, s.horizon, s.action, round(s.score, 6)) for s in res["signals"]])
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(len(runs[0]), 2 * len(cfg["watchlist"]))
        self.assertTrue(any(a != "HOLD" for _, _, a, _ in runs[0]))     # demo ma coś pokazać

    def test_runtime_wires_demo_components(self):
        cfg = load_config(demo=True)
        prices, news = make_providers(cfg, demo=True)
        self.assertIsInstance(prices, DemoPrices)
        self.assertIsInstance(make_analyzer(True, demo=True), DemoAnalyzer)
        self.assertIsNone(make_insiders(cfg, news, Store(":memory:"), demo=True))

    def test_dictionary_sentiment(self):
        a = DemoAnalyzer()
        self.assertGreater(a.analyze_news({}, ["X: wyniki powyżej oczekiwań"])["sentiment"], 0)
        self.assertLess(a.analyze_news({}, ["X obniża prognozę"])["sentiment"], 0)
        self.assertIsNone(a.analyze_news({}, [])["sentiment"])


if __name__ == "__main__":
    unittest.main()
