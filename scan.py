"""CLI: python scan.py [--no-ai] [--no-verify] [--no-insiders] [--notify]

Robi pełny skan, drukuje tabelę sygnałów i (z --notify) wysyła alert o ZMIANACH względem poprzedniego skanu.
Kod wyjścia: 0 = OK, 1 = nie udało się wysłać alertu, 2 = błędy wywołań AI (alerty wtedy pomijane).
Do automatu użyj crona / Harmonogramu zadań (patrz README) - dashboard pokaże ostatni wynik.
"""
import argparse
import sys

from radar.alerts import diff_signals, format_message, notify
from radar.pipeline import run_scan
from radar.runtime import (load_config, load_env, make_analyzer, make_insiders, make_providers,
                           make_store)
from radar.signals import ACTION_PL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true", help="tylko technika + fundamenty")
    ap.add_argument("--no-verify", action="store_true", help="pomiń drugi przebieg weryfikacji AI")
    ap.add_argument("--no-insiders", action="store_true", help="pomiń transakcje insiderów")
    ap.add_argument("--notify", action="store_true", help="wyślij alert o zmianach sygnałów (Telegram/e-mail)")
    args = ap.parse_args()

    load_env()
    cfg = load_config()
    store = make_store()
    prev = store.latest_run(without_ai_errors=True)  # PRZED skanem, który zapisze nowy; skany z błędami AI pomijamy
    prices, news = make_providers(cfg)
    insiders = None if args.no_insiders else make_insiders(cfg, news, store)
    res = run_scan(cfg, prices, news, make_analyzer(use_ai=not args.no_ai), store,
                   progress=lambda f, m: print(f"[{f:4.0%}] {m}"), verify=not args.no_verify,
                   insiders=insiders)

    print("\nMAKRO:", res["macro"].get("summary", ""))
    for hz in cfg.get("settings", {}).get("horizons", ["long", "medium"]):
        print(f"\n=== Horyzont: {hz} ===")
        for s in sorted((s for s in res["signals"] if s.horizon == hz), key=lambda s: -s.score):
            ins = f" insider {s.insider['score']:+.1f}" if s.insider.get("n_events") else ""
            print(f"{s.symbol:9} {s.name[:24]:24} {ACTION_PL[s.action]:9} score {s.score:+.2f}  "
                  f"pewność {s.confidence:.0%}{ins}  {'; '.join(s.errors)[:60]}")

    ai_errors = res["macro"].get("ai_errors") or []
    if ai_errors:
        print(f"\nBŁĘDY AI ({len(ai_errors)} wywołań) - sygnały policzone bez części danych AI:\n  "
              + "\n  ".join(ai_errors[:3]), file=sys.stderr)
        if args.notify:
            # porównanie z poprzednim (pełnym) skanem dałoby fałszywe "zmiany" sygnałów
            print("Alerty: pominięte z powodu błędów AI.", file=sys.stderr)
        return 2

    if args.notify:
        al = cfg.get("alerts", {})
        changes = diff_signals(prev["signals"] if prev else None, res["signals"],
                               min_confidence=al.get("min_confidence", 0.40),
                               alert_on_first_run=al.get("alert_on_first_run", False))
        if not changes:
            print("\nAlerty: brak istotnych zmian sygnałów.")
        else:
            try:
                sent = notify(format_message(changes, res["macro"].get("summary", "")))
                print(f"\nAlerty: {len(changes)} zmian, wysłano: {', '.join(sent) or 'żaden kanał nie jest skonfigurowany (.env)'}")
            except Exception as e:
                print(f"\nAlerty: NIE wysłano - {e}", file=sys.stderr)
                return 1
    print("\nTo narzędzie pomocnicze, nie porada inwestycyjna.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
