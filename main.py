"""
╔════════════════════════════════════════════════════════════════════╗
║      TRADETERMINAL — SMC CONFLUENCE ENGINE v16.0                 ║
║      1% Risk | Auto-Scanner 24/7 | Daily Alive Ping              ║
║      Twelve Data XAUUSD Spot — India Friendly                    ║
╚════════════════════════════════════════════════════════════════════╝

FEATURES:
- 1% risk per trade with strategy-based SL
- Auto-scanner runs every 5 minutes (no manual click needed)
- Daily "I'm alive" ping at 9:00 AM IST (3:30 AM UTC)
- Telegram alerts for setups + daily status
- 24/7 operation on Render
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import warnings
import requests
import os
import threading
import time as time_module

warnings.filterwarnings("ignore")

# ============================================================
# APP
# ============================================================

app = FastAPI(title="TradeTerminal SMC Confluence v16.0", version="16.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ============================================================
# MODELS
# ============================================================

class SMCRules(BaseModel):
    htf_direction: bool = True
    breakout_required: bool = True
    ma_crossover: bool = True
    rsi_filter: bool = True
    macd_filter: bool = True
    fvg_entry: bool = True
    london_only: bool = True
    max_trades: bool = True
    min_rr: bool = True

class SMCBacktestRequest(BaseModel):
    pair: str = "XAUUSD"
    date_from: str
    date_to: str
    capital: float = 5000
    session: str = "London"
    rules: SMCRules = SMCRules()

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = "8699113273:AAGO7uiY3RDDxY-LEnBQEgGf4UX1FPtZ8vY"
TELEGRAM_CHAT_ID = "6504029480"
TWELVE_DATA_API_KEY = "16c788122dde4374b557d83f0317777e"

RISK_PER_TRADE = 0.01
MAX_RISK_CAP = 0.05
POINT_VALUE = 100
R_RATIO = 1.5

# ============================================================
# HELPERS
# ============================================================

def utc_now(): return datetime.now(timezone.utc)
def to_utc(s): return pd.to_datetime(s, utc=True, errors="coerce")

def resolve_timeframes(date_from, date_to):
    d1 = datetime.strptime(date_from, "%Y-%m-%d")
    d2 = datetime.strptime(date_to, "%Y-%m-%d")
    days_ago = (datetime.utcnow() - d2).days
    if days_ago <= 59: return "4h", "15m", "5m"
    elif days_ago <= 730: return "1d", "1h", "15m"
    return "1wk", "1d", "1h"

def get_session(dt):
    if pd.isna(dt): return "Off"
    dt = pd.Timestamp(dt)
    if dt.tzinfo is None: dt = dt.tz_localize("UTC")
    else: dt = dt.tz_convert("UTC")
    h = dt.hour
    if 7 <= h < 16: return "London"
    if 12 <= h < 21: return "New York"
    if 0 <= h < 7: return "Asian"
    return "Off"

def add_indicators(df):
    if df is None or df.empty: return df
    df = df.copy()
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    prev = df["close"].shift(1)
    df["tr"] = pd.concat([df["high"]-df["low"], (df["high"]-prev).abs(), (df["low"]-prev).abs()], axis=1).max(axis=1)
    df["atr_14"] = df["tr"].rolling(14).mean()
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean().replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df

# ============================================================
# DATA FETCH
# ============================================================

def fetch_twelve(symbol, interval, start, end):
    td_sym = {"XAUUSD": "XAU/USD", "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD"}.get(symbol.upper(), symbol)
    td_int = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1day", "1wk": "1week"}.get(interval, "15min")
    
    sdt = datetime.strptime(start, "%Y-%m-%d")
    edt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
    all_candles = []
    
    try:
        while sdt < edt:
            chunk_end = min(sdt + timedelta(days=30), edt)
            params = {
                "symbol": td_sym, "interval": td_int,
                "start_date": sdt.strftime("%Y-%m-%d 00:00:00"),
                "end_date": chunk_end.strftime("%Y-%m-%d 23:59:59"),
                "outputsize": 5000, "apikey": TWELVE_DATA_API_KEY, "timezone": "UTC"
            }
            
            r = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
            if r.status_code == 429:
                time_module.sleep(60); continue
            if r.status_code != 200: break
            data = r.json()
            if "values" not in data: break
            
            for c in data["values"]:
                all_candles.append({"date":c["datetime"],"open":float(c["open"]),"high":float(c["high"]),
                                    "low":float(c["low"]),"close":float(c["close"]),"volume":0})
            sdt = chunk_end + timedelta(days=1)
        
        if not all_candles: return None
        df = pd.DataFrame(all_candles)
        df["date"] = to_utc(df["date"])
        df = df.sort_values("date").drop_duplicates("date").dropna(subset=["date"]).reset_index(drop=True)
        df["session"] = df["date"].apply(get_session)
        df = add_indicators(df)
        return df
    except Exception as e:
        print(f"⚠️ Twelve Data: {e}")
        return None

def fetch_data(pair, tf, start, end):
    df = fetch_twelve(pair, tf, start, end)
    if df is not None and len(df) > 10: return df
    
    import yfinance as yf
    print("📡 Yahoo fallback...")
    tfm = {"5m":"5m","15m":"15m","30m":"30m","1h":"60m","4h":"60m","1d":"1d"}
    try:
        t = tfm.get(tf, "15m")
        raw = yf.download("GLD", start=start, end=end, interval=t, progress=False, auto_adjust=False)
        if raw is None or len(raw) < 5: return None
        if isinstance(raw.columns, pd.MultiIndex): raw.columns = raw.columns.get_level_values(0)
        raw = raw.reset_index().rename(columns={raw.columns[0]:"date"})
        raw.columns = [str(c).lower() for c in raw.columns]
        raw["date"] = to_utc(raw["date"])
        if tf == "4h":
            raw = raw.set_index("date").resample("4H").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna().reset_index()
        raw["session"] = raw["date"].apply(get_session)
        return add_indicators(raw)
    except: return None

# ============================================================
# CONFLUENCE SETUP
# ============================================================

def get_htf_bias(df_h, t, fb=None):
    if df_h is not None and not df_h.empty:
        p = df_h[df_h["date"] <= t]
        if not p.empty and pd.notna(p.iloc[-1].get("ema_50")):
            return "BULLISH" if p.iloc[-1]["close"] > p.iloc[-1]["ema_50"] else "BEARISH"
    if fb is not None and pd.notna(fb.get("ema_50", np.nan)):
        return "BULLISH" if fb["close"] > fb["ema_50"] else "BEARISH"
    return "BULLISH"

def check_long(df, i):
    r = df.iloc[i]
    if i < 20: return None
    if r["close"] <= df["high"].iloc[max(0,i-20):i].max(): return None
    if pd.isna(r["ema_20"]) or pd.isna(r["ema_50"]) or r["ema_20"] <= r["ema_50"]: return None
    if pd.isna(r["rsi_14"]) or r["rsi_14"] < 50 or r["rsi_14"] > 80: return None
    if pd.isna(r["macd_hist"]) or r["macd_hist"] <= 0: return None
    return ["4H↑", "BO", "MA↑", f"RSI{r['rsi_14']:.0f}", "MACD↑"]

def check_short(df, i):
    r = df.iloc[i]
    if i < 20: return None
    if r["close"] >= df["low"].iloc[max(0,i-20):i].min(): return None
    if pd.isna(r["ema_20"]) or pd.isna(r["ema_50"]) or r["ema_20"] >= r["ema_50"]: return None
    if pd.isna(r["rsi_14"]) or r["rsi_14"] > 50 or r["rsi_14"] < 20: return None
    if pd.isna(r["macd_hist"]) or r["macd_hist"] >= 0: return None
    return ["4H↓", "BO", "MA↓", f"RSI{r['rsi_14']:.0f}", "MACD↓"]

def find_fvg(df, i, d):
    row = df.iloc[i]
    for j in range(max(0,i-15), i-1):
        if j+2 >= len(df): continue
        c1, c3 = df.iloc[j], df.iloc[j+2]
        if d == "BUY" and c3["low"] > c1["high"] + 0.5:
            if row["low"] <= c3["low"] and row["close"] >= c1["high"]:
                return round((c1["high"]+c3["low"])/2, 2), j
        if d == "SELL" and c1["low"] > c3["high"] + 0.5:
            if row["high"] >= c3["high"] and row["close"] <= c1["low"]:
                return round((c3["high"]+c1["low"])/2, 2), j
    return None, None

def build_setup(df, i, rules, bias):
    if i < 30: return None
    row = df.iloc[i]
    sess = row["session"]
    if rules.get("london_only", True) and sess not in ["London", "New York"]: return None
    atr = row["atr_14"] if pd.notna(row["atr_14"]) else row["close"] * 0.005
    if atr < row["close"] * 0.001: return None
    
    if bias == "BULLISH":
        rm = check_long(df, i); d = "BUY"
    else:
        rm = check_short(df, i); d = "SELL"
    if rm is None: return None
    
    entry, oi = find_fvg(df, i, d)
    if rules.get("fvg_entry", True) and entry is None: return None
    if entry is None: entry = round(float(row["close"]), 2)
    rm.append("FVG")
    rm.insert(1, sess[:3].upper())
    
    if d == "BUY":
        sweep_level = df["low"].iloc[max(0,i-15):i].min()
        sl = round(sweep_level - atr * 0.3, 2)
        if sl >= entry: sl = round(entry - atr * 1.5, 2)
        risk = entry - sl
        if risk <= 0 or risk > 200: return None
        tp = round(entry + risk * R_RATIO, 2)
    else:
        sweep_level = df["high"].iloc[max(0,i-15):i].max()
        sl = round(sweep_level + atr * 0.3, 2)
        if sl <= entry: sl = round(entry + atr * 1.5, 2)
        risk = sl - entry
        if risk <= 0 or risk > 200: return None
        tp = round(entry - risk * R_RATIO, 2)
    
    if d == "BUY" and tp <= entry: return None
    if d == "SELL" and tp >= entry: return None
    
    rr = R_RATIO
    if rules.get("min_rr", True) and rr < 1.2: return None
    rm.append(f"RR 1:{rr}")
    
    return {"setup_time":row["date"],"setup_session":sess,"direction":d,"entry":entry,"sl":sl,"tp":tp,
            "rr_ratio":rr,"met_rules":rm,"risk_points":round(risk,2)}

# ============================================================
# EXECUTION
# ============================================================

def calculate_position_size(capital, entry, sl):
    sl_distance = abs(entry - sl)
    if sl_distance <= 0: return 0.01
    risk_amount = capital * RISK_PER_TRADE
    lots = risk_amount / (sl_distance * POINT_VALUE)
    lots = max(0.01, round(lots, 2))
    actual_risk = lots * sl_distance * POINT_VALUE
    if actual_risk > capital * MAX_RISK_CAP:
        lots = (capital * MAX_RISK_CAP) / (sl_distance * POINT_VALUE)
        lots = max(0.01, round(lots, 2))
    return lots

def simulate_trade(df_entry, setup, capital, max_bars=500):
    st = pd.Timestamp(setup["setup_time"])
    d = setup["direction"]
    post = df_entry[df_entry["date"] > st]
    if post.empty: return None
    
    entry = float(post.iloc[0]["open"])
    sl = float(setup["sl"])
    tp = float(setup["tp"])
    fill_time = post.iloc[0]["date"]
    risk_points = abs(entry - sl)
    if risk_points <= 0: return None
    
    lots = calculate_position_size(capital, entry, sl)
    risk_usd = round(lots * risk_points * POINT_VALUE, 2)
    
    post_fill = post.head(max_bars)
    result = "TIMEOUT"
    exit_price = entry
    
    for _, c in post_fill.iterrows():
        if d == "BUY":
            if c["low"] <= sl: result = "LOSS"; exit_price = sl; break
            if c["high"] >= tp: result = "WIN"; exit_price = tp; break
        else:
            if c["high"] >= sl: result = "LOSS"; exit_price = sl; break
            if c["low"] <= tp: result = "WIN"; exit_price = tp; break
    
    if result == "TIMEOUT": exit_price = float(post_fill.iloc[-1]["close"])
    
    pnl = lots * (exit_price - entry) * POINT_VALUE if d == "BUY" else lots * (entry - exit_price) * POINT_VALUE
    
    return {"date":str(fill_time),"setup_time":str(st),"direction":d,"entry":round(entry,2),"sl":round(sl,2),
            "tp":round(tp,2),"exit":round(exit_price,2),"lots":lots,"risk_usd":risk_usd,"pnl":round(pnl,2),
            "result":result,"rules_met":" | ".join(setup["met_rules"]),"rr_ratio":setup["rr_ratio"],
            "session":setup["setup_session"]}

# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df_s, df_e, df_h, rules, capital):
    trades, initial = [], capital
    df_s = df_s.sort_values("date").reset_index(drop=True)
    df_e = df_e.sort_values("date").reset_index(drop=True)
    stc, ls, lf, taken, cl = 0, None, None, set(), 0
    
    for i in range(30, len(df_s)):
        row = df_s.iloc[i]; st = row["date"]
        if capital <= 0: break
        cs = row["session"]
        if cs != ls: stc = 0; ls = cs
        if rules.get("max_trades", True) and stc >= 2: continue
        if cl >= 3: cl = 0; continue
        if lf and (st - lf).total_seconds() / 60 < 180: continue
        
        bias = get_htf_bias(df_h, st, fb=row)
        setup = build_setup(df_s, i, rules, bias)
        if not setup: continue
        
        key = f"{st.date()}_{setup['setup_session']}_{setup['direction']}"
        if key in taken: continue
        
        trade = simulate_trade(df_e, setup, capital)
        if not trade: continue
        
        trade["id"] = len(trades) + 1
        capital += trade["pnl"]
        trades.append(trade)
        stc += 1; lf = pd.Timestamp(trade["date"]); taken.add(key)
        cl = cl + 1 if trade["result"] == "LOSS" else 0
    
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    tos = [t for t in trades if t["result"] == "TIMEOUT"]
    total = len(trades)
    wr = round(len(wins) / total * 100, 1) if total > 0 else 0
    gp = sum(t["pnl"] for t in wins) if wins else 0
    gl = abs(sum(t["pnl"] for t in losses)) if losses else 0
    pf = round(gp / gl, 2) if gl > 0 else (999 if gp > 0 else 0)
    net = capital - initial
    
    print(f"📊 {total} trades | {len(wins)}W/{len(losses)}L | WR:{wr}% | P&L:${net:,.2f}")
    
    eq = [{"time":0,"value":round(initial,2)}]
    r = initial
    for t in trades: r += t["pnl"]; eq.append({"time":len(eq),"value":round(r,2)})
    
    return {"trades":trades,"performance":{"total_trades":total,"wins":len(wins),"losses":len(losses),
            "timeouts":len(tos),"win_rate":wr,"profit_factor":pf,"total_pnl":round(net,2),
            "gross_profit":round(gp,2),"gross_loss":round(gl,2),
            "best_trade":round(max(t["pnl"] for t in trades),2) if trades else 0,
            "worst_trade":round(min(t["pnl"] for t in trades),2) if trades else 0,
            "avg_win":round(gp/len(wins),2) if wins else 0,"avg_loss":round(gl/len(losses),2) if losses else 0,
            "final_capital":round(capital,2),"total_return":round(net/initial*100,2)},"equity_curve":eq}

# ============================================================
# TELEGRAM
# ============================================================

def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"},timeout=5)
    except Exception as e:
        print(f"TG error: {e}")

# ============================================================
# 24/7 AUTO SCANNER
# ============================================================

def run_scanner():
    """Scan for SMC setups and send Telegram alerts"""
    try:
        end = utc_now()
        start = end - timedelta(days=5)
        
        df_s = fetch_data("XAUUSD", "15m", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        
        if df_s is None or len(df_s) < 30:
            print("⚠️ Scanner: Insufficient data")
            return False
        
        df_h = fetch_data("XAUUSD", "4h", (end - timedelta(days=30)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        
        for i in range(len(df_s) - 1, max(29, len(df_s) - 6), -1):
            bias = get_htf_bias(df_h, df_s.iloc[i]["date"], fb=df_s.iloc[i])
            setup = build_setup(df_s, i, {}, bias)
            
            if setup:
                lots = calculate_position_size(5000, setup["entry"], setup["sl"])
                emoji = "🟢 LONG" if setup["direction"] == "BUY" else "🔴 SHORT"
                msg = f"""🤖 <b>SMC CONFLUENCE — {emoji}</b>
<b>XAUUSD</b>
━━━━━━━━━━━━━━━━━━━━━
📍 <b>Entry:</b> ${setup['entry']}
🛑 <b>SL:</b> ${setup['sl']} ({setup['risk_points']} pts)
🎯 <b>TP:</b> ${setup['tp']}
📊 <b>R:R 1:{setup['rr_ratio']}</b>
📐 <b>Lots:</b> {lots}
💰 <b>Risk:</b> ${round(lots*setup['risk_points']*POINT_VALUE,2)} (1%)
<b>{' | '.join(setup['met_rules'])}</b>
━━━━━━━━━━━━━━━━━━━━━
⏰ {setup['setup_time']}
🧠 v16.0 Auto"""
                send_tg(msg)
                print(f"🚨 SIGNAL SENT: {setup['direction']} @ ${setup['entry']}")
                return True
        
        print(f"✅ Scan OK — No setup at {end.strftime('%H:%M UTC')}")
        return False
        
    except Exception as e:
        print(f"⚠️ Scanner error: {e}")
        return False

def auto_scanner_loop():
    """Background loop: scan every 5 minutes"""
    print("🔍 Auto-Scanner: Starting (every 5 min)...")
    time_module.sleep(10)  # Initial delay for server startup
    
    while True:
        run_scanner()
        time_module.sleep(300)  # 5 minutes

def daily_alive_ping():
    """Send 'I'm alive' message every day at 9:00 AM IST (3:30 AM UTC)"""
    print("💓 Daily Ping: Scheduled for 3:30 UTC (9:00 AM IST)")
    
    while True:
        now = utc_now()
        
        # Check if it's 3:30 UTC
        if now.hour == 3 and now.minute == 30:
            msg = f"""💓 <b>SMC Engine — Daily Status</b>
━━━━━━━━━━━━━━━━━━━━━
✅ <b>Status:</b> ONLINE
🕐 <b>Time:</b> {now.strftime('%Y-%m-%d %H:%M UTC')} (9:00 AM IST)
🔍 <b>Scanner:</b> Running (every 5 min)
📡 <b>Data:</b> Twelve Data XAUUSD
💰 <b>Risk:</b> 1% per trade
━━━━━━━━━━━━━━━━━━━━━
🧠 v16.0 — All Systems Go!"""
            send_tg(msg)
            print("💓 Daily ping sent!")
            time_module.sleep(120)  # Sleep 2 min to avoid duplicate
        
        time_module.sleep(30)  # Check every 30 seconds

# ============================================================
# API
# ============================================================

@app.get("/api/health")
async def health():
    return {"status":"ok","version":"16.0","engine":"SMC Confluence 24/7","scanner":"auto"}

@app.post("/api/smc-backtest")
async def smc_backtest(req: SMCBacktestRequest):
    htf_tf, setup_tf, entry_tf = resolve_timeframes(req.date_from, req.date_to)
    df_h = fetch_data(req.pair, htf_tf, req.date_from, req.date_to)
    df_s = fetch_data(req.pair, setup_tf, req.date_from, req.date_to)
    df_e = fetch_data(req.pair, entry_tf, req.date_from, req.date_to)
    if df_s is None or len(df_s)<30:
        df_s = fetch_data(req.pair,"1d",req.date_from,req.date_to)
        if df_s is None or len(df_s)<10: raise HTTPException(404,"No data")
        req.rules.london_only=False; req.rules.max_trades=False
    if df_e is None or len(df_e)<20: df_e = df_s.copy()
    return run_backtest(df_s, df_e, df_h, req.rules.dict(), req.capital)

@app.post("/api/smc-scan")
async def smc_scan(req: SMCBacktestRequest):
    result = run_scanner()
    return {"signal":result,"last_check":str(utc_now())}

# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    
    # Start 24/7 auto-scanner (background thread)
    scanner_thread = threading.Thread(target=auto_scanner_loop, daemon=True)
    scanner_thread.start()
    print("🔍 24/7 Auto-Scanner: ACTIVE")
    
    # Start daily alive ping (background thread)
    ping_thread = threading.Thread(target=daily_alive_ping, daemon=True)
    ping_thread.start()
    print("💓 Daily Alive Ping: ACTIVE (9:00 AM IST)")
    
    print(f"🚀 SMC Engine v16.0 starting on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
