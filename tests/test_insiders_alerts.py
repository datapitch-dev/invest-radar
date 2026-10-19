"""Testy insiderów i alertów na danych syntetycznych."""
import time
import unittest

from radar.alerts import diff_signals, format_message, notify
from radar.ai import NoAI
from radar.insiders import (InsiderService, PolishInsiderNews, classify_pl_headline, parse_form4,
                            summarize)
from radar.pipeline import run_scan
from radar.signals import Signal, apply_insider, build_signal
from radar.storage import Store
from tests.test_core import FakeNews, FakeNewsItem, FakePrices

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-20</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>50.5</value></transactionPricePerShare></transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-21</value></transactionDate>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>5000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare></transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-22</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>200</value></transactionShares>
        <transactionPricePerShare><value>51</value></transactionPricePerShare></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def ev(code, who="X", value=1000.0):
    return {"date": "2026-09-20", "insider": who, "role": "", "code": code, "shares": 10, "price": 100,
            "value": value}


class Form4(unittest.TestCase):
    def test_parses_only_open_market_buys_and_sells(self):
        rows = parse_form4(FORM4)
        self.assertEqual([r["code"] for r in rows], ["P", "S"])      # kod A (przyznanie) pominięty
        self.assertEqual(rows[0]["insider"], "DOE JANE")
        self.assertEqual(rows[0]["role"], "CFO")
        self.assertAlmostEqual(rows[0]["value"], 50500.0)


class Summaries(unittest.TestCase):
    def test_cluster_buying_beats_single_and_sales_weigh_less(self):
        cluster = summarize([ev("P", "A"), ev("P", "B"), ev("P", "C")])["score"]
        single = summarize([ev("P", "A")])["score"]
        sells = summarize([ev("S", "A"), ev("S", "B"), ev("S", "C")])["score"]
        self.assertGreater(cluster, single)
        self.assertAlmostEqual(cluster, 1.0)
        self.assertLess(sells, 0)
        self.assertGreater(sells, -0.5)

    def test_same_person_counted_once(self):
        self.assertEqual(summarize([ev("P", "A"), ev("P", "A")])["buyers"], 1)

    def test_empty_is_zero(self):
        self.assertEqual(summarize([])["score"], 0.0)


class PolishHeadlines(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(classify_pl_headline("Prezes spółki kupił akcje za 2 mln zł"), "P")
        self.assertEqual(classify_pl_headline("Członek zarządu sprzedał akcje CD Projekt"), "S")
        self.assertIsNone(classify_pl_headline("Spółka kupiła konkurenta"))              # brak kontekstu insiderskiego
        self.assertIsNone(classify_pl_headline("Prezes kupił i sprzedał akcje w jednym dniu"))  # niejednoznaczne

    def test_fetch_uses_news_and_dedupes(self):
        class N:
            def search(self, q, max_items=15):
                it = FakeNewsItem("Prezes kupił akcje spółki")
                return [type("I", (), {"title": it.t, "link": "http://x", "published": time.time()})()]
        ev_ = PolishInsiderNews(N()).fetch("Spółka")
        self.assertEqual(len(ev_), 1)                      # to samo z dwóch zapytań -> jedno
        self.assertEqual(ev_[0]["code"], "P")


class Overlay(unittest.TestCase):
    def test_overlay_directions_and_approximation(self):
        ins = {"score": 1.0, "approximate": False}
        s, c = apply_insider(0.2, 0.6, ins)
        self.assertAlmostEqual(s, 0.35)
        self.assertEqual(c, 0.6)
        s2, _ = apply_insider(0.2, 0.6, {"score": 1.0, "approximate": True})
        self.assertAlmostEqual(s2, 0.275)                  # połowa wagi
        s3, c3 = apply_insider(0.5, 0.6, {"score": -0.5, "approximate": False})
        self.assertLess(s3, 0.5)
        self.assertLess(c3, 0.6)                           # przeczą kierunkowi -> mniejsza pewność

    def test_no_insider_data_changes_nothing(self):
        self.assertEqual(apply_insider(0.3, 0.5, None), (0.3, 0.5))
        self.assertEqual(apply_insider(0.3, 0.5, {"score": 0.0}), (0.3, 0.5))

    def test_build_signal_mentions_insiders(self):
        meta = {"symbol": "X", "name": "X"}
        tech, fund = {"score": 0.3, "details": {"last": 10}, "risk": {}}, {"score": 0.3}
        ins = summarize([ev("P", "A"), ev("P", "B")])
        sig = build_signal(meta, "long", tech, fund, 0.3, None, None, [], insider=ins)
        self.assertIn("Insiderzy", sig.rationale)
        self.assertGreater(sig.score, build_signal(meta, "long", tech, fund, 0.3, None, None, []).score)


class Service(unittest.TestCase):
    def test_routes_by_market_and_caches(self):
        class Sec:
            calls = 0
            def fetch(self, sym, days):
                Sec.calls += 1
                return [ev("P", "A")]
        svc = InsiderService(sec=Sec(), pl=None)
        store = Store(":memory:")
        meta = {"symbol": "AAPL", "name": "Apple", "kind": "stock", "market": "USA"}
        r1, r2 = svc.analyze(meta, store), svc.analyze(meta, store)
        self.assertEqual(r1["buyers"], 1)
        self.assertEqual(Sec.calls, 1)                     # drugie z cache
        self.assertIsNone(svc.analyze({**meta, "kind": "etf"}, store))
        self.assertIsNone(svc.analyze({**meta, "market": "Europa"}, store))

    def test_pipeline_survives_insider_errors_and_uses_overlay(self):
        class Boom:
            def analyze(self, meta, store): raise RuntimeError("sec down")
        cfg = {"settings": {}, "macro_queries": [],
               "watchlist": [{"symbol": "UP", "name": "Up", "kind": "stock", "market": "USA",
                              "sector": "T", "query": "u"}]}
        res = run_scan(cfg, FakePrices(), FakeNews(), NoAI(), Store(":memory:"), insiders=Boom())
        self.assertTrue(any("insiderzy" in e for e in res["signals"][0].errors))

        class Bull:
            def analyze(self, meta, store): return summarize([ev("P", "A"), ev("P", "B"), ev("P", "C")])
        base = run_scan(cfg, FakePrices(), FakeNews(), NoAI(), Store(":memory:"))["signals"][0]
        boosted = run_scan(cfg, FakePrices(), FakeNews(), NoAI(), Store(":memory:"), insiders=Bull())["signals"][0]
        self.assertGreater(boosted.score, base.score)
        self.assertEqual(boosted.insider["buyers"], 3)


def sig(sym, hz, action, conf=0.6, score=0.4):
    return Signal(symbol=sym, name=sym, kind="stock", market="USA", sector="T", horizon=hz,
                  action=action, score=score, confidence=conf)


class Alerts(unittest.TestCase):
    def test_first_run_no_alerts_unless_enabled(self):
        new = [sig("A", "long", "BUY")]
        self.assertEqual(diff_signals(None, new), [])
        self.assertEqual(len(diff_signals(None, new, alert_on_first_run=True)), 1)

    def test_change_rules(self):
        prev = [{"symbol": "A", "horizon": "long", "action": "HOLD"},
                {"symbol": "B", "horizon": "long", "action": "BUY"},
                {"symbol": "C", "horizon": "long", "action": "HOLD"},
                {"symbol": "D", "horizon": "long", "action": "BUY"}]
        new = [sig("A", "long", "BUY"),                      # nowy BUY, pewny -> alert
               sig("B", "long", "HOLD", conf=0.1),           # wygasł sygnał -> alert
               sig("C", "long", "BUY", conf=0.2),            # za niska pewność -> brak
               sig("D", "long", "BUY")]                      # bez zmiany -> brak
        out = {c["symbol"]: c for c in diff_signals(prev, new)}
        self.assertEqual(set(out), {"A", "B"})
        self.assertEqual(out["B"]["new"], "HOLD")

    def test_same_action_alerts_when_confidence_crosses_threshold(self):
        prev = [{"symbol": "A", "horizon": "long", "action": "BUY", "confidence": 0.35},
                {"symbol": "B", "horizon": "long", "action": "BUY", "confidence": 0.50}]
        out = diff_signals(prev, [sig("A", "long", "BUY", conf=0.7), sig("B", "long", "BUY", conf=0.8)])
        self.assertEqual([c["symbol"] for c in out], ["A"])            # B już wcześniej był ponad progiem
        self.assertIn("pewność wzrosła z 35%", format_message(out))
        prev_hold = [{"symbol": "A", "horizon": "long", "action": "HOLD", "confidence": 0.1}]
        self.assertEqual(diff_signals(prev_hold, [sig("A", "long", "HOLD", conf=0.2)]), [])

    def test_new_watchlist_item_alerts_when_actionable(self):
        prev = [{"symbol": "A", "horizon": "long", "action": "HOLD"}]
        out = diff_signals(prev, [sig("Z", "long", "AVOID", conf=0.7)])
        self.assertEqual(out[0]["symbol"], "Z")

    def test_message_format_and_truncation(self):
        ch = [{"symbol": f"S{i}", "name": "N" * 40, "horizon": "long", "old": "HOLD", "new": "BUY",
               "score": 0.5, "confidence": 0.7, "risks": ["r" * 80, "r" * 80]} for i in range(100)]
        msg = format_message(ch, "makro")
        self.assertLessEqual(len(msg), 3500)
        self.assertIn("KUP", msg)
        self.assertIn("nie jest porada", format_message(ch[:1]))

    def test_notify_channels_and_failures(self):
        calls = []
        tg = lambda t, tok, cid: calls.append(("tg", tok, cid))
        mail = lambda t, h, p, u, pw, to: calls.append(("mail", h, p, to))
        env = {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1", "SMTP_HOST": "h", "SMTP_USER": "u",
               "SMTP_PASSWORD": "p", "EMAIL_TO": "me@x"}
        self.assertEqual(notify("hi", env, tg, mail), ["telegram", "email"])
        self.assertEqual(calls[1], ("mail", "h", 465, "me@x"))
        self.assertEqual(notify("hi", {}, tg, mail), [])        # nic nie skonfigurowane

        def bad(*a): raise OSError("net")
        self.assertEqual(notify("hi", env, bad, mail), ["email"])   # błąd jednego kanału nie blokuje drugiego
        with self.assertRaises(RuntimeError):
            notify("hi", {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1"}, bad, mail)


if __name__ == "__main__":
    unittest.main()
