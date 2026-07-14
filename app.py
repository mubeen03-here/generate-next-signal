import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from sqlalchemy import create_engine, text
import requests
from datetime import datetime, timedelta
import pytz

# Page Configuration
st.set_page_config(page_title="Institutional Trading Terminal", layout="wide")

# ==========================================
# 1. DATABASE CONNECTION & INITIALIZATION
# ==========================================
@st.cache_resource
def get_db_engine():
    """Neon Postgres Database connection engine with SSL security."""
    db_url = st.secrets["DATABASE_URL"]
    if "?sslmode=" not in db_url:
        db_url += "?sslmode=require"
    return create_engine(db_url)

engine = get_db_engine()

def init_db():
    """Initializes schema v2 with strict unique constraints."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS signals_v2 (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                signal_type VARCHAR(10) NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                tp DOUBLE PRECISION NOT NULL,
                sl DOUBLE PRECISION NOT NULL,
                status VARCHAR(20) DEFAULT 'PENDING',
                result VARCHAR(20) DEFAULT 'OPEN',
                UNIQUE(symbol, timestamp, signal_type, entry_price)
            );
        """))

init_db()

# ==========================================
# 2. ADMIN DATABASE OPERATIONS
# ==========================================
def cancel_all_pending_signals():
    """Cancels all active pending signals to free up the terminal."""
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE signals_v2 
            SET status = 'CLOSED', result = 'CANCELLED' 
            WHERE status = 'PENDING'
        """))

def reset_entire_database():
    """Clears all logged signals from the database."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE signals_v2 RESTART IDENTITY;"))

# ==========================================
# 3. AUTO-TIMEOUT & SIGNAL UPDATE ENGINE
# ==========================================
def run_auto_timeout_and_updates(symbol, current_price):
    """Cancels >24h stale signals and checks TP/SL hits for active ones."""
    utc_now = datetime.now(pytz.utc)
    
    with engine.begin() as conn:
        # 1. 24-Hour Auto Timeout Logic
        conn.execute(text("""
            UPDATE signals_v2 
            SET status = 'CLOSED', result = 'CANCELLED' 
            WHERE status = 'PENDING' 
            AND timestamp < NOW() - INTERVAL '24 hours'
        """))
        
        # 2. Check pending signals status based on latest price
        pending_signals = conn.execute(text(
            "SELECT id, signal_type, entry_price, tp, sl FROM signals_v2 WHERE symbol = :symbol AND status = 'PENDING'"
        ), {"symbol": symbol}).fetchall()
        
        for sig in pending_signals:
            sig_id, sig_type, entry, tp, sl = sig
            
            if sig_type == "BUY":
                if current_price >= tp:
                    conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'WIN' WHERE id = :id"), {"id": sig_id})
                elif current_price <= sl:
                    conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'LOSS' WHERE id = :id"), {"id": sig_id})
            
            elif sig_type == "SELL":
                if current_price <= tp:
                    conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'WIN' WHERE id = :id"), {"id": sig_id})
                elif current_price >= sl:
                    conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'LOSS' WHERE id = :id"), {"id": sig_id})

# ==========================================
# 4. FOREX FACTORY NEWS FILTER
# ==========================================
def is_macro_news_blocked():
    """Checks if there is any high impact USD news today to block trading."""
    try:
        url = "https://nfs.forexfactory1.com/ff_calendar_thisweek.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            events = response.json()
            today_str = datetime.now(pytz.timezone('America/New_York')).strftime('%Y-%m-%d')
            for ev in events:
                ev_date = ev.get("date", "")
                if today_str in ev_date:
                    if ev.get("country") == "USD" and ev.get("impact") == "High":
                        return True
    except Exception:
        pass
    return False

# ==========================================
# 5. CORE INSTITUTIONAL LOGIC
# ==========================================
def generate_signals_engine(symbol, timeframe="15m"):
    """Core algorithmic engine utilizing VWAP, Liquidity Sweeps and relaxed Volume spikes."""
    df = yf.download(symbol, period="5d", interval=timeframe)
    if df.empty:
        return None
    
    # Clean Column Names
    df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
    
    # Calculate Daily VWAP
    cum_vol = df['Volume'].cumsum()
    cum_vol_price = (df['Close'] * df['Volume']).cumsum()
    df['VWAP'] = cum_vol_price / cum_vol
    
    # Volatility and Average Volume
    df['ATR'] = df['Close'].diff().abs().rolling(14).mean()
    df['Vol_MA'] = df['Volume'].rolling(20).mean()
    
    # Lookback for Swings
    lookback = 30
    df['Recent_High'] = df['High'].rolling(lookback).max()
    df['Recent_Low'] = df['Low'].rolling(lookback).min()
    
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]
    
    current_price = last_row['Close']
    current_volume = last_row['Volume']
    avg_volume = last_row['Vol_MA']
    vwap = last_row['VWAP']
    atr = last_row['ATR']
    
    # Relaxed Volume Spike check for better Scalping (1.2x)
    volume_spike = current_volume > (avg_volume * 1.2)
    
    # Liquidity Sweep Detections
    liquidity_sweep_bullish = (prev_row['Low'] <= last_row['Recent_Low']) and (current_price > last_row['Recent_Low'])
    liquidity_sweep_bearish = (prev_row['High'] >= last_row['Recent_High']) and (current_price < last_row['Recent_High'])
    
    # Macro Filter Check
    news_block = is_macro_news_blocked()
    
    signal = "WAIT"
    entry = current_price
    tp = 0.0
    sl = 0.0
    
    if not news_block:
        if current_price > vwap and liquidity_sweep_bullish and volume_spike:
            signal = "BUY"
            sl = current_price - (1.5 * atr)
            tp = current_price + (3.0 * atr)
        elif current_price < vwap and liquidity_sweep_bearish and volume_spike:
            signal = "SELL"
            sl = current_price + (1.5 * atr)
            tp = current_price - (3.0 * atr)
            
    return {
        "signal": signal,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "vwap": vwap,
        "current_price": current_price,
        "news_blocked": news_block
    }

# ==========================================
# 6. STREAMLIT UI & DASHBOARD
# ==========================================
st.title("🛡️ Institutional Grade Algorithmic Terminal")

# Sidebar - Asset Selection
st.sidebar.header("Asset Settings")
symbol = st.sidebar.selectbox("Select Asset", ["XAUUSD=F", "BTC-USD", "ETH-USD"], index=0)
timeframe = st.sidebar.selectbox("Select Timeframe", ["5m", "15m", "1h"], index=1)

# Sidebar - Admin Dashboard Panel
st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Admin Control Panel")
admin_password = st.sidebar.text_input("Enter Admin Password", type="password")

if admin_password == "mubeen123":
    st.sidebar.success("Access Granted!")
    col_admin1, col_admin2 = st.sidebar.columns(2)
    with col_admin1:
        if st.button("🔴 Cancel All Pending"):
            cancel_all_pending_signals()
            st.sidebar.success("All pending updated to CANCELLED!")
            st.rerun()
    with col_admin2:
        if st.button("⚠️ Reset Entire DB"):
            reset_entire_database()
            st.sidebar.warning("Database tables cleared!")
            st.rerun()

# Run Engine and Auto-Timeout Updates
engine_output = generate_signals_engine(symbol, timeframe)

if engine_output:
    current_price = engine_output["current_price"]
    run_auto_timeout_and_updates(symbol, current_price)
    
    # Display Status Cards
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Current Market Price", f"{current_price:.2f}")
    with col2:
        st.metric("Daily VWAP Level", f"{engine_output['vwap']:.2f}")
    with col3:
        status_text = "🚨 MACRO NEWS BLOCK" if engine_output["news_blocked"] else "⚡ RUNNING"
        st.metric("News Filter Status", status_text)
        
    # Process New Signals
    signal_type = engine_output["signal"]
    if signal_type in ["BUY", "SELL"]:
        # Save to Neon DB with unique constraints bypass
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO signals_v2 (symbol, timestamp, signal_type, entry_price, tp, sl, status, result)
                VALUES (:symbol, NOW(), :signal_type, :entry_price, :tp, :sl, 'PENDING', 'OPEN')
                ON CONFLICT (symbol, timestamp, signal_type, entry_price) DO NOTHING
            """), {
                "symbol": symbol,
                "signal_type": signal_type,
                "entry_price": engine_output["entry"],
                "tp": engine_output["tp"],
                "sl": engine_output["sl"]
            })
            
    # Display Current Signals Status from DB
    st.subheader("📊 Live Tracking Dashboard")
    with engine.connect() as conn:
        active_signals = conn.execute(text(
            "SELECT timestamp, signal_type, entry_price, tp, sl, status, result FROM signals_v2 ORDER BY id DESC LIMIT 10"
        )).fetchall()
        
    if active_signals:
        df_signals = pd.DataFrame(active_signals, columns=["Time", "Type", "Entry Price", "Take Profit", "Stop Loss", "Status", "Result"])
        st.dataframe(df_signals, use_container_width=True)
    else:
        st.info("No active signals recorded yet. Waiting for structural sweeps...")
else:
    st.error("Failed to retrieve market data. Check connection settings.")
    
