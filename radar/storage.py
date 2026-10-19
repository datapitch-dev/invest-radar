"""SQLite: cache danych (ceny, fundamenty, newsy) i historia sygnałów."""
from __future__ import annotations

import io
import json
import sqlite3
import time
from pathlib import Path

import pandas as pd


class Store:
    def __init__(self, path: str | Path = "data/radar.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, ts REAL, payload TEXT);
            CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, macro TEXT);
            CREATE TABLE IF NOT EXISTS signals (run_id INTEGER, symbol TEXT, horizon TEXT,
                action TEXT, score REAL, confidence REAL, payload TEXT);
        """)

    # --- cache -----------------------------------------------------------
    def get_json(self, key: str, ttl_hours: float | None = None):
        row = self.db.execute("SELECT ts, payload FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if ttl_hours is not None and time.time() - row[0] > ttl_hours * 3600:
            return None
        return json.loads(row[1])

    def set_json(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)",
                        (key, time.time(), json.dumps(value, default=str)))
        self.db.commit()

    def get_df(self, key: str, ttl_hours: float | None = None) -> pd.DataFrame | None:
        s = self.get_json(key, ttl_hours)
        if s is None:
            return None
        return pd.read_json(io.StringIO(s), orient="split")

    def set_df(self, key: str, df: pd.DataFrame) -> None:
        self.set_json(key, df.to_json(orient="split", date_format="iso"))

    # --- historia sygnałów --------------------------------------------------
    def save_run(self, signals: list, macro: dict | None) -> int:
        cur = self.db.execute("INSERT INTO runs (ts, macro) VALUES (?,?)",
                              (time.time(), json.dumps(macro or {}, default=str)))
        run_id = cur.lastrowid
        self.db.executemany(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?)",
            [(run_id, s.symbol, s.horizon, s.action, s.score, s.confidence,
              json.dumps(s.to_dict(), default=str)) for s in signals])
        self.db.commit()
        return run_id

    def latest_run(self, without_ai_errors: bool = False):
        """Ostatni skan; z without_ai_errors=True pomija skany z błędami AI (punkt odniesienia dla alertów)."""
        runs = self.db.execute("SELECT id, ts, macro FROM runs ORDER BY id DESC LIMIT 50").fetchall()
        for run_id, ts, macro_json in runs:
            macro = json.loads(macro_json)
            if without_ai_errors and macro.get("ai_errors"):
                continue
            sigs = [json.loads(r[0]) for r in self.db.execute(
                "SELECT payload FROM signals WHERE run_id=?", (run_id,))]
            return {"id": run_id, "ts": ts, "macro": macro, "signals": sigs}
        return None

    def history(self, symbol: str, horizon: str, limit: int = 60):
        return self.db.execute(
            "SELECT r.ts, s.action, s.score, s.confidence FROM signals s JOIN runs r ON r.id=s.run_id "
            "WHERE s.symbol=? AND s.horizon=? ORDER BY r.id DESC LIMIT ?",
            (symbol, horizon, limit)).fetchall()

    def signal_rows(self) -> list[dict]:
        """Wszystkie zapisane sygnały z czasem skanu i ceną - wejście do oceny historii (radar.backtest)."""
        out = []
        for ts, payload in self.db.execute(
                "SELECT r.ts, s.payload FROM signals s JOIN runs r ON r.id=s.run_id ORDER BY r.id"):
            s = json.loads(payload)
            out.append({"ts": ts, "symbol": s["symbol"], "horizon": s["horizon"], "action": s["action"],
                        "price": s.get("price")})
        return out
