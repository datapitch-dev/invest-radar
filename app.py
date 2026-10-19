"""Dashboard: streamlit run app.py   (tryb demo: streamlit run app.py -- --demo)"""
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from radar.pipeline import run_scan
from radar.runtime import is_demo, load_config, load_env, make_analyzer, make_insiders, make_providers, make_store
from radar.signals import ACTION_PL, HORIZON_PL

st.set_page_config(page_title="Invest Radar", page_icon="📡", layout="wide")
load_env()
DEMO = is_demo()
cfg = load_config(demo=DEMO)
store = make_store(DEMO)

COLORS = {"BUY": "#1a9850", "ACCUMULATE": "#91cf60", "HOLD": "#9e9e9e", "REDUCE": "#fc8d59", "AVOID": "#d73027"}

st.title("📡 Invest Radar")
st.caption("Osobiste narzędzie do selekcji pomysłów inwestycyjnych. To NIE jest porada inwestycyjna - "
           "sygnały generuje model na podstawie niepełnych, darmowych danych. Decyzje podejmujesz sam.")
if DEMO:
    st.info("**Tryb demo:** fikcyjne instrumenty i syntetyczne notowania, sentyment ze słownika zamiast AI. "
            "Nic tu nie opisuje prawdziwych spółek.")


def scan(use_ai: bool, verify: bool, progress=None) -> None:
    analyzer = make_analyzer(use_ai, demo=DEMO)
    if use_ai and not DEMO and analyzer.__class__.__name__ == "NoAI":
        st.warning("Brak ANTHROPIC_API_KEY - liczę bez AI.")
    prices, news = make_providers(cfg, demo=DEMO)
    run_scan(cfg, prices, news, analyzer, store, progress=progress, verify=verify and use_ai,
             insiders=make_insiders(cfg, news, store, demo=DEMO))


# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Ustawienia")
    horizon = st.radio("Horyzont", ["long", "medium"], format_func=lambda h: HORIZON_PL[h])
    markets = sorted({w.get("market", "") for w in cfg["watchlist"]})
    sel_markets = st.multiselect("Rynki", markets, default=markets)
    min_conf = st.slider("Min. pewność", 0, 100, 0, step=5, format="%d%%") / 100
    st.divider()
    use_ai = st.toggle("Użyj AI (Claude)", value=not DEMO, disabled=DEMO,
                       help="Wymaga ANTHROPIC_API_KEY w pliku .env. Bez AI: tylko technika + fundamenty.")
    verify = st.toggle("Weryfikacja AI sygnałów", value=not DEMO, disabled=DEMO or not use_ai)
    if st.button("🔄 Odśwież dane i sygnały", type="primary", width="stretch"):
        bar = st.progress(0.0, text="Start...")
        scan(use_ai, verify, progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
        bar.empty()
        st.rerun()

if DEMO and store.latest_run() is None:
    scan(use_ai=False, verify=False)        # demo liczy się w sekundę i offline - od razu jest co pokazać
run = store.latest_run()
if not run:
    st.info("Brak danych. Kliknij **Odśwież dane i sygnały** w panelu bocznym (pierwszy skan trwa kilka minut).")
    st.stop()

st.caption(f"Ostatni skan: {datetime.fromtimestamp(run['ts']):%Y-%m-%d %H:%M}")

# ---------------- makro ----------------
macro = run["macro"] or {}
ai_errors = macro.get("ai_errors") or []
if ai_errors:
    st.error(f"**AI nie zadziałało w {len(ai_errors)} wywołaniach** - sygnały policzono bez części danych AI. "
             f"Sprawdź ANTHROPIC_API_KEY / ANTHROPIC_MODEL w .env.\n\nPierwszy błąd: `{ai_errors[0]}`")
with st.expander("🌍 Kontekst makro i geopolityka", expanded=True):
    c1, c2 = st.columns([2, 1])
    with c1:
        st.write(macro.get("summary") or "Brak analizy makro (AI wyłączone lub brak newsów).")
        rs = macro.get("risk_sentiment")
        if rs is not None:
            st.metric("Nastrój rynku (−1 ucieczka od ryzyka … +1 apetyt na ryzyko)", f"{rs:+.2f}")
        for h in macro.get("headlines", [])[:8]:
            st.markdown(f"- [{h['title']}]({h['link']})")
    with c2:
        tilts = macro.get("sector_tilts") or {}
        if tilts:
            tdf = pd.DataFrame({"Sektor": list(tilts), "Wpływ": list(tilts.values())}).sort_values("Wpływ")
            fig = go.Figure(go.Bar(x=tdf["Wpływ"], y=tdf["Sektor"], orientation="h",
                                   marker_color=["#d73027" if v < 0 else "#1a9850" for v in tdf["Wpływ"]]))
            fig.update_layout(height=280, margin=dict(l=0, r=0, t=20, b=0), xaxis_range=[-1, 1],
                              title="Tilt sektorów z newsów")
            st.plotly_chart(fig, width="stretch")

# ---------------- tabela sygnałów ----------------
sigs = [s for s in run["signals"] if s["horizon"] == horizon and s["market"] in sel_markets
        and s["confidence"] >= min_conf]
sigs.sort(key=lambda s: -s["score"])
if not sigs:
    st.warning("Brak sygnałów dla wybranych filtrów.")
    st.stop()

st.subheader(f"Sygnały - horyzont {HORIZON_PL[horizon]}")
table = pd.DataFrame([{
    "Symbol": s["symbol"], "Nazwa": s["name"], "Rynek": s["market"], "Sektor": s["sector"],
    "Sygnał": ACTION_PL[s["action"]], "Score": round(s["score"], 2), "Pewność": round(s["confidence"] * 100),
    "Technika": s["tech_score"], "Fundamenty": s["fund_score"], "Newsy/makro": s["news_score"],
    "Insiderzy": (s.get("insider") or {}).get("score") if (s.get("insider") or {}).get("n_events") else None,
    "Cena": s["price"], "Uwagi": len(s["risks"]) + len(s["errors"]),
} for s in sigs])


def color_signal(v):
    inv = {ACTION_PL[k]: c for k, c in COLORS.items()}
    return f"background-color: {inv.get(v, '#fff')}; color: #111; font-weight: 600"


st.dataframe(
    table.style.map(color_signal, subset=["Sygnał"]).format(
        {"Score": "{:+.2f}", "Technika": "{:+.2f}", "Fundamenty": "{:+.2f}", "Newsy/makro": "{:+.2f}",
         "Insiderzy": "{:+.2f}", "Cena": "{:.2f}", "Pewność": "{:.0f}%"}, na_rep="-"),
    width="stretch", hide_index=True)

# ---------------- szczegóły ----------------
st.subheader("Szczegóły instrumentu")
pick = st.selectbox("Wybierz", [f"{s['symbol']} - {s['name']}" for s in sigs])
s = next(x for x in sigs if f"{x['symbol']} - {x['name']}" == pick)

left, right = st.columns([3, 2])
with left:
    px = store.get_df(f"px:{s['symbol']}")
    if px is not None and not px.empty:
        c = px["Close"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=px.index, y=c, name="Cena", line=dict(width=2)))
        fig.add_trace(go.Scatter(x=px.index, y=c.rolling(50).mean(), name="SMA50", line=dict(dash="dot")))
        if len(c) >= 200:
            fig.add_trace(go.Scatter(x=px.index, y=c.rolling(200).mean(), name="SMA200", line=dict(dash="dash")))
        fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Brak zapisanych notowań.")
with right:
    st.markdown(f"### {ACTION_PL[s['action']]}  ·  score {s['score']:+.2f}  ·  pewność {s['confidence']:.0%}")
    comp = {"Technika": s["tech_score"], "Fundamenty": s["fund_score"], "Newsy/makro": s["news_score"]}
    comp = {k: v for k, v in comp.items() if v is not None}
    if comp:
        fig = go.Figure(go.Bar(x=list(comp.values()), y=list(comp), orientation="h",
                               marker_color=["#d73027" if v < 0 else "#1a9850" for v in comp.values()]))
        fig.update_layout(height=170, margin=dict(l=0, r=0, t=0, b=0), xaxis_range=[-1, 1])
        st.plotly_chart(fig, width="stretch")
    st.write(s["rationale"])
    v = s.get("verification") or {}
    if v:
        icon = {"confirm": "✅", "caution": "⚠️", "reject": "⛔"}.get(v.get("verdict"), "")
        st.markdown(f"**Weryfikacja AI:** {icon} {v.get('verdict')} - {v.get('summary', '')}")
    for r in s["risks"]:
        st.warning(r)
    for e in s["errors"]:
        st.caption(f"⚙️ {e}")

ins = s.get("insider") or {}
if ins.get("n_events"):
    note = " _(przybliżone - z nagłówków newsów, nie z oficjalnego rejestru)_" if ins.get("approximate") else ""
    st.markdown(f"**Insiderzy**{note}: {ins['buyers']} kupujących, {ins['sellers']} sprzedających "
                f"(score {ins['score']:+.2f})")
    for e in ins.get("events", []):
        who = f"{e['insider']} ({e['role']})" if e.get("role") else e["insider"]
        val = f" · ${e['value']:,.0f}" if e.get("value") else ""
        label = e.get("headline") or f"{'zakup' if e['code'] == 'P' else 'sprzedaż'} {e['shares']:,.0f} akcji"
        link = e.get("filing_url")
        st.markdown(f"- {e.get('date', '')} · {who}: " + (f"[{label}]({link})" if link else label) + val)

st.markdown("**Najnowsze newsy**")
for n in s["news"]:
    when = datetime.fromtimestamp(n["published"]).strftime("%d.%m %H:%M")
    st.markdown(f"- {when} · [{n['title']}]({n['link']}) _{n.get('source', '')}_")

hist = store.history(s["symbol"], horizon)
if len(hist) > 1:
    hdf = pd.DataFrame(hist, columns=["ts", "action", "score", "conf"])
    hdf["Data"] = pd.to_datetime(hdf["ts"], unit="s")
    st.markdown("**Historia score z kolejnych skanów**")
    st.line_chart(hdf.set_index("Data")["score"])
