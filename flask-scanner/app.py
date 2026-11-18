from __future__ import annotations

import sqlite3
import time
import csv
import io
import urllib.request
import json
import os
from datetime import timedelta
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB_PATH = ROOT / "portfolio.db"
app = Flask(__name__, static_folder="static")
PRICE_CACHE = {}
DETAIL_CACHE = {}
SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_GID = os.environ.get("SHEET_GID", "0")
SHEET_CACHE = {"at": 0.0, "count": 0, "error": None}


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS holdings (symbol TEXT PRIMARY KEY, shares REAL NOT NULL DEFAULT 0, cost REAL NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'app')")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(holdings)").fetchall()}
        if "source" not in columns:
            db.execute("ALTER TABLE holdings ADD COLUMN source TEXT NOT NULL DEFAULT 'app'")
        db.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, kind TEXT NOT NULL,
            signal_date TEXT NOT NULL, macd REAL NOT NULL, signal REAL NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(symbol, kind, signal_date))""")
        db.execute("CREATE TABLE IF NOT EXISTS market_bars (symbol TEXT NOT NULL, day TEXT NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, PRIMARY KEY(symbol,day))")
        db.execute("CREATE TABLE IF NOT EXISTS universe_cache (group_name TEXT PRIMARY KEY, symbols TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS market_scan_results (group_name TEXT NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(group_name,as_of))")


def sync_sheet_holdings(force=False):
    """Import rows from the user's link-shared Google Sheet without deleting app data."""
    now = time.monotonic()
    if not force and now - SHEET_CACHE["at"] < 300:
        return dict(SHEET_CACHE)
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MACD-Portfolio-PWA/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            text = response.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not reader.fieldnames:
            raise ValueError("Google Sheet 沒有可讀取的標題列。")
        header_map = {str(name or "").strip().casefold(): name for name in reader.fieldnames}
        def find_header(*names):
            return next((header_map[n.casefold()] for n in names if n.casefold() in header_map), None)
        symbol_col = find_header("代號", "股票代號", "ticker", "symbol")
        shares_col = find_header("股數", "數量", "shares")
        cost_col = find_header("平均成本", "每股成本", "持有成本", "cost", "avg cost")
        if not symbol_col:
            raise ValueError("Google Sheet 找不到「代號」欄。")
        imported = 0
        with connect() as db:
            for row in rows:
                raw_symbol = str(row.get(symbol_col, "")).strip()
                if not raw_symbol:
                    continue
                symbol = valid_symbol(raw_symbol)
                raw_shares = str(row.get(shares_col, "")).strip() if shares_col else ""
                raw_cost = str(row.get(cost_col, "")).strip() if cost_col else ""
                shares = float(raw_shares.replace(",", "")) if raw_shares else None
                cost = float(raw_cost.replace(",", "").replace("$", "")) if raw_cost else None
                existing = db.execute("SELECT shares,cost,source FROM holdings WHERE symbol=?", (symbol,)).fetchone()
                if existing is None:
                    db.execute("INSERT INTO holdings(symbol,shares,cost,source) VALUES(?,?,?,?)", (symbol, shares if shares is not None else 5, cost or 0, "sheet"))
                    imported += 1
                elif existing["source"] == "sheet" and (existing["shares"] <= 0 or (shares is not None or cost is not None)):
                    db.execute("UPDATE holdings SET shares=?,cost=? WHERE symbol=?", (shares if shares is not None else 5 if existing["shares"] <= 0 else existing["shares"], cost if cost is not None else existing["cost"], symbol))
        SHEET_CACHE.update({"at": now, "count": len([r for r in rows if str(r.get(symbol_col, "")).strip()]), "error": None})
    except Exception as exc:
        SHEET_CACHE.update({"at": now, "count": 0, "error": str(exc)})
    return {**SHEET_CACHE, "new": imported if SHEET_CACHE["error"] is None else 0}


def valid_symbol(value):
    symbol = str(value or "").strip().upper()
    if not symbol or len(symbol) > 20 or not all(c.isalnum() or c in ".-^=" for c in symbol):
        raise ValueError("請輸入有效的美股代號，例如 AAPL 或 BRK-B。")
    return symbol


def holding_dict(row):
    return {"symbol": row["symbol"], "shares": row["shares"], "cost": row["cost"]}


@app.get("/")
def index():
    return send_from_directory(ROOT / "static", "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(ROOT / "static", "manifest.webmanifest")


@app.get("/icon.svg")
def icon():
    return send_from_directory(ROOT / "static", "icon.svg")


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(ROOT / "static", "sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/api/holdings")
def list_holdings():
    sync_sheet_holdings()
    with connect() as db:
        rows = db.execute("SELECT * FROM holdings ORDER BY symbol").fetchall()
    return jsonify([holding_dict(row) for row in rows])


@app.post("/api/holdings")
def save_holding():
    try:
        data = request.get_json(force=True)
        symbol = valid_symbol(data.get("symbol"))
        shares, cost = float(data.get("shares")), float(data.get("cost"))
        if shares <= 0 or cost < 0:
            raise ValueError("股數須大於 0，持有成本不可小於 0。")
        with connect() as db:
            db.execute("INSERT INTO holdings(symbol, shares, cost) VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET shares=excluded.shares, cost=excluded.cost", (symbol, shares, cost))
        return jsonify({"symbol": symbol, "shares": shares, "cost": cost})
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/holdings/<symbol>")
def delete_holding(symbol):
    try:
        symbol = valid_symbol(symbol)
        with connect() as db:
            db.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
        return jsonify({"ok": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


def fetch_macd(symbol):
    cached = PRICE_CACHE.get(symbol)
    if cached and time.monotonic() - cached[0] < 240:
        return cached[1]
    frame = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
    if frame.empty or "Close" not in frame:
        raise ValueError("找不到價格資料，請確認代號。")
    close = frame["Close"].dropna()
    # Treat the current NY daily candle as unfinished until the regular session has closed.
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    last_day = close.index[-1].date()
    if last_day == ny_now.date() and (ny_now.hour, ny_now.minute) < (16, 15):
        close = close.iloc[:-1]
    if len(close) < 35:
        raise ValueError("可用的日線資料不足。")
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    values = pd.DataFrame({"close": close, "macd": macd, "signal": signal}).dropna()
    PRICE_CACHE[symbol] = (time.monotonic(), values)
    return values


MARKET_GROUPS = {
    "sp500": ("S&P 500", None, 500),
    "healthcare": ("Healthcare", "Healthcare", 50),
    "financial": ("Financials", "Financial Services", 10),
    "telecom": ("Telecom operators", "Telecom Services", 10),
    "consumer": ("Consumer products", "Consumer Defensive", 50),
    "energy": ("Energy", "Energy", 20),
}


def market_symbols(group):
    """Refresh screened universes weekly; prices and indicators remain yfinance based."""
    if group not in MARKET_GROUPS:
        raise ValueError("未知的股票分類。")
    with connect() as db:
        cached = db.execute("SELECT symbols,updated_at FROM universe_cache WHERE group_name=?", (group,)).fetchone()
    if cached and datetime.now(timezone.utc) - datetime.fromisoformat(cached["updated_at"]) < timedelta(days=7):
        return json.loads(cached["symbols"])
    label, sector, limit = MARKET_GROUPS[group]
    if group == "sp500":
        # Wikipedia's current constituents are used only as the index membership list.
        import re
        req = urllib.request.Request("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers={"User-Agent": "Mozilla/5.0 MACD-PWA"})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        table = re.search(r'<table[^>]*id="constituents"[^>]*>(.*?)</table>', html, re.S)
        if not table:
            raise ValueError("暫時無法讀取 S&P 500 成分股名單。")
        import html as html_lib
        symbols = []
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table.group(1), re.S):
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
            if cells:
                ticker = html_lib.unescape(re.sub(r'<[^>]+>', '', cells[0])).strip()
                if re.fullmatch(r'[A-Z]{1,5}(?:\.[A-Z])?', ticker):
                    symbols.append(ticker)
        symbols = list(dict.fromkeys(symbols))
        symbols = [s.replace(".", "-") for s in symbols][:limit]
    else:
        from yfinance import EquityQuery
        if group == "telecom":
            iq = EquityQuery("and", [EquityQuery("eq", ["region", "us"]), EquityQuery("eq", ["industry", "Telecom Services"])])
            ir = yf.screen(iq, size=250, sortField="intradaymarketcap", sortAsc=False)
            symbols = [x.get("symbol", "") for x in ir.get("quotes", []) if x.get("symbol")][:limit]
        elif group == "consumer":
            collected = {}
            for consumer_sector in ("Consumer Defensive", "Consumer Cyclical"):
                q = EquityQuery("and", [EquityQuery("eq", ["region", "us"]), EquityQuery("eq", ["sector", consumer_sector])])
                response = yf.screen(q, size=250, sortField="intradaymarketcap", sortAsc=False)
                for item in response.get("quotes", []):
                    if item.get("symbol"):
                        collected[item["symbol"]] = item
            ranked = sorted(collected.values(), key=lambda item: item.get("marketCap") or 0, reverse=True)
            symbols = [item["symbol"] for item in ranked[:limit]]
        else:
            q = EquityQuery("and", [EquityQuery("eq", ["region", "us"]), EquityQuery("eq", ["sector", sector])])
            response = yf.screen(q, size=250, sortField="intradaymarketcap", sortAsc=False)
            quotes = response.get("quotes", []) if isinstance(response, dict) else []
            symbols = [x.get("symbol", "") for x in quotes if x.get("symbol")][:limit]
    if not symbols:
        raise ValueError(f"{label}名單目前無法取得。")
    with connect() as db:
        db.execute("INSERT INTO universe_cache(group_name,symbols,updated_at) VALUES(?,?,?) ON CONFLICT(group_name) DO UPDATE SET symbols=excluded.symbols,updated_at=excluded.updated_at", (group, json.dumps(symbols), datetime.now(timezone.utc).isoformat()))
    return symbols


@app.get("/api/market-scan")
def market_scan():
    group = request.args.get("group", "healthcare")
    try:
        symbols = market_symbols(group)
        ny_now = datetime.now(ZoneInfo("America/New_York"))
        if (ny_now.hour, ny_now.minute) >= (16, 15):
            with connect() as db:
                saved = db.execute("SELECT payload FROM market_scan_results WHERE group_name=? AND as_of=?", (group, ny_now.date().isoformat())).fetchone()
            if saved:
                return jsonify(json.loads(saved["payload"]))
        # Download in small batches with bounded yfinance concurrency to reduce rate-limit pressure.
        frames = []
        for start in range(0, len(symbols), 40):
            batch = symbols[start:start + 40]
            frame = yf.download(batch, period="1y", interval="1d", auto_adjust=True, group_by="ticker", threads=2, progress=False, timeout=25, multi_level_index=True)
            if frame is not None and not frame.empty:
                frames.append(frame)
        if not frames:
            raise ValueError("yfinance 暫時沒有回傳行情資料。")
        data = pd.concat(frames, axis=1)
        rows = []
        for symbol in symbols:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    cols = data[symbol] if symbol in data.columns.get_level_values(0) else None
                else:
                    cols = data if len(symbols) == 1 else None
                if cols is None or "Close" not in cols or "Volume" not in cols:
                    continue
                close, volume = cols["Close"].dropna(), cols["Volume"].fillna(0)
                ny_now = datetime.now(ZoneInfo("America/New_York"))
                if len(close) and close.index[-1].date() == ny_now.date() and (ny_now.hour, ny_now.minute) < (16, 15):
                    close = close.iloc[:-1]
                if len(close) < 35:
                    continue
                macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
                signal = macd.ewm(span=9, adjust=False).mean()
                last_day = close.index[-1]
                pos = close.index.get_loc(last_day)
                recent = close.tail(120)
                m, s = macd.loc[recent.index], signal.loc[recent.index]
                v = volume.reindex(recent.index).fillna(0)
                events = {"golden": "", "positive": "", "negative": "", "death": ""}
                for i in range(1, len(recent)):
                    day = recent.index[i].date().isoformat()
                    if m.iloc[i-1] <= s.iloc[i-1] and m.iloc[i] > s.iloc[i]: events["golden"] = day
                    if m.iloc[i-1] <= 0 < m.iloc[i]: events["positive"] = day
                    if m.iloc[i-1] >= 0 > m.iloc[i]: events["negative"] = day
                    if m.iloc[i-1] >= s.iloc[i-1] and m.iloc[i] < s.iloc[i]: events["death"] = day
                avg_vol = float(v.iloc[-21:-1].mean()) if len(v) > 20 else 0
                current_vol = float(v.iloc[-1])
                vol_pct = (current_vol / avg_vol - 1) * 100 if avg_vol else 0
                avg_dollar_volume = float((recent.tail(20) * v.tail(20)).mean())
                price = float(close.iloc[-1])
                prev_close = float(close.iloc[-2])
                rows.append({"symbol": symbol, "close": price, "change_pct": (price / prev_close - 1) * 100 if prev_close else 0,
                    "golden": events["golden"], "positive": events["positive"], "negative": events["negative"], "death": events["death"],
                    "volume": current_vol, "vol_pct": vol_pct, "avg_dollar_volume": avg_dollar_volume, "spike": vol_pct >= 50,
                    "macd": float(macd.iloc[-1]), "signal": float(signal.iloc[-1]), "date": last_day.date().isoformat()})
                # Persist downloaded daily bars so the Pi can reuse its local history cache.
                with connect() as db:
                    db.executemany("INSERT OR REPLACE INTO market_bars(symbol,day,close,volume) VALUES(?,?,?,?)", [(symbol, ix.date().isoformat(), float(close.loc[ix]), float(volume.get(ix, 0) or 0)) for ix in close.tail(120).index])
            except Exception:
                continue
        if not rows:
            raise ValueError("目前沒有可用的完整日線行情，請稍後重試。")
        payload = {"group": group, "group_label": MARKET_GROUPS[group][0], "as_of": max((r["date"] for r in rows), default=""), "rows": rows, "count": len(rows), "volume_threshold": 1.5}
        if payload["as_of"]:
            with connect() as db:
                db.execute("INSERT OR REPLACE INTO market_scan_results(group_name,as_of,payload) VALUES(?,?,?)", (group, payload["as_of"], json.dumps(payload)))
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/scan")
def scan():
    sync_sheet_holdings()
    with connect() as db:
        holdings = db.execute("SELECT * FROM holdings ORDER BY symbol").fetchall()
    problems, new_alerts = [], []
    for holding in holdings:
        symbol = holding["symbol"]
        try:
            values = fetch_macd(symbol)
            previous, current = values.iloc[-2], values.iloc[-1]
            signal_date = values.index[-1].date().isoformat()
            price = float(current["close"])
            events = []
            if previous.macd <= 0 < current.macd:
                events.append(("macd_positive", "MACD 線轉正"))
            if previous.macd >= 0 > current.macd:
                events.append(("macd_negative", "MACD 線轉負"))
            if previous.macd <= previous.signal and current.macd > current.signal:
                events.append(("golden_cross", "MACD 黃金交叉"))
            if previous.macd >= previous.signal and current.macd < current.signal:
                events.append(("death_cross", "MACD 死亡交叉"))
            for kind, label in events:
                with connect() as db:
                    cursor = db.execute("INSERT OR IGNORE INTO alerts(symbol,kind,signal_date,macd,signal,created_at) VALUES(?,?,?,?,?,?)", (symbol, kind, signal_date, float(current.macd), float(current.signal), datetime.now(timezone.utc).isoformat()))
                    if cursor.rowcount:
                        new_alerts.append({"symbol": symbol, "kind": kind, "label": label, "date": signal_date, "macd": round(float(current.macd), 4), "signal": round(float(current.signal), 4)})
        except Exception as exc:
            problems.append({"symbol": symbol, "error": str(exc)})
    return jsonify({"alerts": new_alerts, "errors": problems})


@app.get("/api/alerts")
def list_alerts():
    with connect() as db:
        rows = db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 100").fetchall()
    labels = {"macd_positive": "MACD 線轉正", "macd_negative": "MACD 線轉負", "golden_cross": "MACD 黃金交叉", "death_cross": "MACD 死亡交叉"}
    return jsonify([{"id": row["id"], "symbol": row["symbol"], "kind": row["kind"], "label": labels[row["kind"]], "date": row["signal_date"], "macd": row["macd"], "signal": row["signal"]} for row in rows])


@app.get("/api/quotes")
def quotes():
    sheet = sync_sheet_holdings()
    with connect() as db:
        holdings = db.execute("SELECT * FROM holdings ORDER BY symbol").fetchall()
    results, errors = [], []
    for holding in holdings:
        try:
            values = fetch_macd(holding["symbol"])
            latest = values.iloc[-1]
            shares, cost = float(holding["shares"]), float(holding["cost"])
            price = float(latest.close)
            if holding["source"] == "sheet" and cost <= 0:
                cost = price
                with connect() as db:
                    db.execute("UPDATE holdings SET cost=? WHERE symbol=? AND source='sheet' AND cost<=0", (cost, holding["symbol"]))
            results.append({"symbol": holding["symbol"], "shares": shares, "cost": cost, "price": price, "value": price * shares if shares else None, "pnl": (price - cost) * shares if shares and cost else None, "pnl_pct": ((price / cost) - 1) * 100 if cost else None, "macd": float(latest.macd), "signal": float(latest.signal), "histogram": float(latest.macd - latest.signal), "date": values.index[-1].date().isoformat()})
        except Exception as exc:
            errors.append({"symbol": holding["symbol"], "error": str(exc)})
    return jsonify({"quotes": results, "errors": errors, "sheet": sheet, "holding_count": len(holdings)})


@app.post("/api/sync-sheet")
def sync_sheet_route():
    return jsonify(sync_sheet_holdings(force=True))


@app.get("/api/history/<symbol>")
def macd_history(symbol):
    try:
        symbol = valid_symbol(symbol)
        values = fetch_macd(symbol).tail(120)
        return jsonify({"symbol": symbol, "points": [{
            "date": idx.date().isoformat(), "close": float(row.close),
            "macd": float(row.macd), "signal": float(row.signal),
            "histogram": float(row.macd - row.signal)
        } for idx, row in values.iterrows()]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/stock-detail/<symbol>")
def stock_detail(symbol):
    try:
        symbol = valid_symbol(symbol)
        cached = DETAIL_CACHE.get(symbol)
        if cached and time.monotonic() - cached[0] < 240:
            return jsonify(cached[1])
        frame = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
        if frame.empty or not {"Open", "High", "Low", "Close", "Volume"}.issubset(frame.columns):
            raise ValueError("找不到完整的日線 OHLCV 資料。")
        frame = frame.dropna(subset=["Close"])
        ny_now = datetime.now(ZoneInfo("America/New_York"))
        if frame.index[-1].date() == ny_now.date() and (ny_now.hour, ny_now.minute) < (16, 15):
            frame = frame.iloc[:-1]
        close = frame["Close"]
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        signal = macd.ewm(span=9, adjust=False).mean()
        frame = frame.tail(120).copy()
        frame["MACD"] = macd.reindex(frame.index)
        frame["Signal"] = signal.reindex(frame.index)
        frame["Histogram"] = frame["MACD"] - frame["Signal"]
        points = [{"date": idx.date().isoformat(), "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]), "volume": float(row["Volume"]),
            "macd": float(row["MACD"]), "signal": float(row["Signal"]), "histogram": float(row["Histogram"])}
            for idx, row in frame.iterrows()]
        events = []
        for i in range(1, len(points)):
            a, b = points[i - 1], points[i]
            if a["macd"] <= a["signal"] and b["macd"] > b["signal"]:
                events.append({"kind": "golden", "label": "黃金交叉", "date": b["date"]})
            if a["macd"] >= a["signal"] and b["macd"] < b["signal"]:
                events.append({"kind": "death", "label": "死亡交叉", "date": b["date"]})
            if a["macd"] <= 0 < b["macd"]:
                events.append({"kind": "positive", "label": "MACD 負轉正", "date": b["date"]})
            if a["macd"] >= 0 > b["macd"]:
                events.append({"kind": "negative", "label": "MACD 正轉負", "date": b["date"]})
        result = {"symbol": symbol, "date": points[-1]["date"] if points else "", "points": points, "events": list(reversed(events))}
        DETAIL_CACHE[symbol] = (time.monotonic(), result)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5001, debug=False)
