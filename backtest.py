"""CLI: czy sygnały cokolwiek przewidują?

  python backtest.py technika [--years 10] [--horizon 63] [--demo]
      Test wstecz składowej technicznej na historii notowań watchlisty (jedyna składowa bez danych z przyszłości).
  python backtest.py historia [--days 91] [--demo]
      Ocena sygnałów zapisanych przez kolejne skany: co zrobiła cena `days` dni po sygnale.

Szczegóły metody i ograniczeń: radar/backtest.py.
"""
import argparse
import sys

import pandas as pd

from radar.backtest import EvaluationResult, backtest_technical, evaluate_history
from radar.runtime import ROOT, is_demo, load_config, load_env, make_providers, make_store
from radar.signals import ACTION_PL


def show(res: EvaluationResult, title: str) -> None:
    obs = res.observations
    print(f"\n=== {title} ===")
    print(f"Obserwacje: {len(obs)}, instrumenty: {obs['symbol'].nunique()}, "
          f"okres: {obs['date'].min():%Y-%m-%d} - {obs['date'].max():%Y-%m-%d}")
    if res.ic_by_date is not None:
        print(f"IC (korelacja rang score vs zwrot): średnio {res.ic_mean:+.3f}, błąd std. {res.ic_se:.3f}, "
              f"t = {res.ic_t:+.2f} z {len(res.ic_by_date)} dat")
        print("  |t| < 2 oznacza, że nie da się odróżnić wyniku od przypadku.")
    table = res.by_action().rename(index=ACTION_PL)
    with pd.option_context("display.float_format", "{:+.2%}".format):
        print(table.to_string())
    print("Zwrot nadwyżkowy = zwrot instrumentu minus średnia watchlisty w tym samym okresie. "
          "Bez kosztów i podatku.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("technika", help="backtest składowej technicznej")
    t.add_argument("--years", type=int, default=10)
    t.add_argument("--horizon", type=int, default=63, help="sesje do wyjścia (63 ≈ 3 mies.)")
    t.add_argument("--demo", action="store_true", help="syntetyczne notowania, bez internetu")
    h = sub.add_parser("historia", help="ocena zapisanych sygnałów")
    h.add_argument("--days", type=int, default=91, help="dni kalendarzowe od sygnału do oceny")
    h.add_argument("--demo", action="store_true", help="baza trybu demo (data/demo.db)")
    args = ap.parse_args()

    load_env()
    demo = getattr(args, "demo", False) or is_demo()
    cfg = load_config(demo=demo)
    prices, _ = make_providers(cfg, demo)
    if demo and args.mode == "technika":
        prices.sessions = 252 * args.years      # dłuższa syntetyczna historia do testu wstecz

    if args.mode == "technika":
        closes, failed = {}, []
        for w in cfg["watchlist"]:
            try:
                closes[w["symbol"]] = prices.prices(w["symbol"], period=f"{args.years}y")["Close"]
            except Exception as e:  # brak jednego instrumentu nie przerywa testu
                failed.append(f"{w['symbol']}: {e}")
        if failed:
            print("Pominięte:", "; ".join(failed), file=sys.stderr)
        res = backtest_technical(closes, horizon=args.horizon)
        out = ROOT / "data" / ("backtest_demo.csv" if demo else "backtest_technika.csv")
        out.parent.mkdir(exist_ok=True)
        res.observations.to_csv(out, index=False)
        show(res, f"Technika, horyzont {args.horizon} sesji" + (" (DEMO: dane syntetyczne)" if demo else ""))
        print(f"Obserwacje zapisane w {out}")
    else:
        store = make_store(demo)
        rows = store.signal_rows()
        closes = {}
        for sym in sorted({r["symbol"] for r in rows}):
            try:
                closes[sym] = prices.prices(sym)["Close"]
            except Exception as e:
                print(f"Pominięte {sym}: {e}", file=sys.stderr)
        res = evaluate_history(rows, closes, days=args.days)
        if res.observations.empty:
            print(f"Brak sygnałów starszych niż {args.days} dni - skanuj regularnie i wróć później.")
            return 0
        show(res, f"Historia sygnałów, ocena po {args.days} dniach")
    return 0


if __name__ == "__main__":
    sys.exit(main())
