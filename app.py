import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from sqlalchemy import create_engine, text
import requests
from datetime import datetime
import pytz

# ==========================================
# 0. PAGE CONFIG & PREMIUM CSS DESIGN
# ==========================================
st.set_page_config(page_title="Institutional Trading Terminal", layout="wide", initial_sidebar_state="expanded")

# Injecting Custom Institutional CSS
st.markdown("""
    <style>
    /* Dark Premium Theme Corrections */
    .stApp { background-color: #0E1117; }
    .css-1d391kg { background-color: #161A25; }
    
    /* Sleek Metric Cards */
    div[data-testid="metric-container"] {
        background-color: #1E232F;
        border: 1px solid #31333F;
        padding: 15px 20px;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    
    /* Main Title Styling */
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #F0F2F6;
        margin-bottom: 0px;
        padding-bottom: 10px;
        border-bottom: 2px solid #FF4B4B;
    }
    
    /* Subheader Styling */
    .sub-title {
        color: #808495;
        font-size: 1.1rem;
        font-weight: 400;
        margin-bottom: 30px;
    }
    
    /* Hide Streamlit Default Menu */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. DATABASE CONNECTION & INITIALIZATION
# ==========================================
@st.cache_resource
def get_db_engine():
    db_url = st.secrets["DATABASE_URL"]
    if "?sslmode=" not in db_url:
        db_url += "?sslmode=require"
    return create_engine(db_url, pool_size=5, max_overflow=10, pool_recycle=300, pool_pre_ping=True)

engine = get_db_engine()

def init_db():
    recreate = False
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT id FROM signals_v2 LIMIT 1;"))
        except Exception:
            recreate = True
    with engine.begin() as conn:
        if recreate:
            conn.execute(text("DROP TABLE IF EXISTS signals_v2 CASCADE;"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS signals_v2 (
                id SERIAL PRIMARY KEY, symbol VARCHAR(20) NOT NULL, timestamp TIMESTAMP NOT NULL,
                signal_type VARCHAR(10) NOT NULL, entry_price DOUBLE PRECISION NOT NULL,
                tp DOUBLE PRECISION NOT NULL, sl DOUBLE PRECISION NOT NULL,
                status VARCHAR(20) DEFAULT 'PENDING', result VARCHAR(20) DEFAULT 'OPEN',
                UNIQUE(symbol, timestamp, signal_type, entry_price)
            );
        """))

init_db()

# ==========================================
# 2. ADMIN DATABASE OPERATIONS
# ==========================================
def cancel_all_pending_signals():
    with engine.begin() as conn:
        conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'CANCELLED' WHERE status = 'PENDING'"))

def reset_entire_database():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE signals_v2 RESTART IDENTITY;"))

def run_auto_timeout_and_updates(symbol, current_price):
    with engine.begin() as conn:
        conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'CANCELLED' WHERE status = 'PENDING' AND timestamp < NOW() - INTERVAL '24 hours'"))
        pending = conn.execute(text("SELECT id, signal_type, entry_price, tp, sl FROM signals_v2 WHERE symbol = :symbol AND status = 'PENDING'"), {"symbol": symbol}).fetchall()
        for sig in pending:
            sig_id, sig_type, entry, tp, sl = sig
            if sig_type == "BUY":
                if current_price >= tp: conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'WIN' WHERE id = :id"), {"id": sig_id})
                elif current_price <= sl: conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'LOSS' WHERE id = :id"), {"id": sig_id})
            elif sig_type == "SELL":
                if current_price <= tp: conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'WIN' WHERE id = :id"), {"id": sig_id})
                elif current_price >= sl: conn.execute(text("UPDATE signals_v2 SET status = 'CLOSED', result = 'LOSS' WHERE id = :id"), {"id": sig_id})

# ==========================================
# 3. DATA & LOGIC ENGINE
# ==========================================
def is_macro_news_blocked():
    try:
        url = "https://nfs.forexfactory1.com/ff_calendar_thisweek.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            events = response.json()
            today_str = datetime.now(pytz.timezone('America/New_York')).strftime('%Y-%m-%d')
            for ev in events:
                if today_str in ev.get("date", "") and ev.get("country") == "USD" and ev.get("impact") == "High":
                    return True
    except Exception:
        pass
    return False

@st.cache_data(ttl=120)
def fetch_ticker_data(symbol, timeframe):
    return yf.download(symbol, period="5d", interval=timeframe, progress=False)

def generate_signals_engine(selected_symbol, timeframe="15m"):
    try:
        actual_symbol = selected_symbol
        raw_df = fetch_ticker_data(actual_symbol, timeframe)
        
        if raw_df.empty or len(raw_df) < 35:
            actual_symbol = "BTC-USD"
            raw_df = fetch_ticker_data(actual_symbol, timeframe)
            
        if raw_df.empty or len(raw_df) < 35: return None
        
        df = raw_df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        
        cum_vol = df['Volume'].cumsum()
        cum_vol_price = (df['Close'] * df['Volume']).cumsum()
        df['VWAP'] = (cum_vol_price / cum_vol).fillna(df['Close'])
        
        df['ATR'] = df['Close'].diff().abs().rolling(14).mean()
        df['Vol_MA'] = df['Volume'].rolling(20).mean()
        
        lookback = 30
        df['Recent_High'] = df['High'].rolling(lookback).max()
        df['Recent_Low'] = df['Low'].rolling(lookback).min()
        
        last_row, prev_row = df.iloc[-1], df.iloc[-2]
        candle_time = df.index[-1]
        if hasattr(candle_time, 'tzinfo') and candle_time.tzinfo is not None:
            candle_time = candle_time.tz_convert('UTC').tz_localize(None)
        
        current_price, current_volume = float(last_row['Close']), float(last_row['Volume'])
        avg_volume, vwap, atr = float(last_row['Vol_MA']), float(last_row['VWAP']), float(last_row['ATR'])
        
        vol_spike = current_volume > (avg_volume * 1.2)
        liq_sweep_bull = (prev_row['Low'] <= last_row['Recent_Low']) and (current_price > last_row['Recent_Low'])
        liq_sweep_bear = (prev_row['High'] >= last_row['Recent_High']) and (current_price < last_row['Recent_High'])
        
        news_block = is_macro_news_blocked()
        signal, tp, sl = "WAIT", 0.0, 0.0
        
        if not news_block:
            if current_price > vwap and liq_sweep_bull and vol_spike:
                signal, sl, tp = "BUY", current_price - (1.5 * atr), current_price + (3.0 * atr)
            elif current_price < vwap and liq_sweep_bear and vol_spike:
                signal, sl, tp = "SELL", current_price + (1.5 * atr), current_price - (3.0 * atr)
                
        return {
            "actual_symbol": actual_symbol, "signal": signal, "entry": current_price,
            "tp": tp, "sl": sl, "vwap": vwap, "news_blocked": news_block, "timestamp": candle_time
        }
    except Exception:
        return None

# ==========================================
# 4. FRONT-END UI / UX
# ==========================================
st.markdown('<div class="main-title">🛡️ Institutional Order Flow Terminal</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Advanced Liquidity Sweep & VWAP Algorithm (FundedNext Optimized)</div>', unsafe_allow_html=True)

# Control Panel
col_ctrl1, col_ctrl2 = st.columns(2)
with col_ctrl1:
    symbol_input = st.selectbox("🎯 Target Asset", ["GC=F", "BTC-USD", "ETH-USD"], index=0)
with col_ctrl2:
    timeframe = st.selectbox("⏱️ Resolution (Timeframe)", ["5m", "15m", "1h"], index=1)

st.markdown("---")

# Admin Sidebar
st.sidebar.markdown("### ⚙️ System Admin Panel")
admin_password = st.sidebar.text_input("Enter Admin Key", type="password")
if admin_password == "mubeen123":
    st.sidebar.success("✅ Root Access Granted")
    if st.sidebar.button("🔴 Force Cancel Pending Orders"):
        cancel_all_pending_signals()
        st.rerun()
    if st.sidebar.button("⚠️ Hard Reset Database"):
        reset_entire_database()
        st.rerun()

# Run Engine
engine_output = generate_signals_engine(symbol_input, timeframe)

if engine_output:
    used_symbol = engine_output["actual_symbol"]
    current_price = engine_output["entry"]
    run_auto_timeout_and_updates(used_symbol, current_price)
    
    # Premium Metric Cards
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        st.metric(f"Live Price ({used_symbol})", f"${current_price:,.2f}")
    with col_m2:
        st.metric("Institutional VWAP", f"${engine_output['vwap']:,.2f}")
    with col_m3:
        status_text = "🚨 MACRO BLOCK" if engine_output["news_blocked"] else "⚡ ACTIVE TRACKING"
        st.metric("Market Regime Filter", status_text)
        
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Save Signal Logic
    signal_type = engine_output["signal"]
    if signal_type in ["BUY", "SELL"]:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO signals_v2 (symbol, timestamp, signal_type, entry_price, tp, sl, status, result)
                VALUES (:sym, :ts, :sig, :ent, :tp, :sl, 'PENDING', 'OPEN')
                ON CONFLICT DO NOTHING
            """), {"sym": used_symbol, "ts": engine_output["timestamp"], "sig": signal_type, "ent": current_price, "tp": engine_output["tp"], "sl": engine_output["sl"]})
            
    # Live Tracking Dashboard
    st.markdown("### 📊 Order Block & Live Execution Tracker")
    try:
        with engine.connect() as conn:
            active_signals = conn.execute(text("SELECT timestamp, symbol, signal_type, entry_price, tp, sl, status, result FROM signals_v2 ORDER BY id DESC LIMIT 15")).fetchall()
            
        if active_signals:
            df_signals = pd.DataFrame(active_signals, columns=["Timestamp (UTC)", "Asset", "Direction", "Entry Price", "Take Profit", "Stop Loss", "Status", "Outcome"])
            
            # Styling the DataFrame
            def color_direction(val):
                color = '#00F0FF' if val == 'BUY' else '#FF4B4B' if val == 'SELL' else 'white'
                return f'color: {color}; font-weight: bold;'
                
            styled_df = df_signals.style.applymap(color_direction, subset=['Direction'])
            st.dataframe(styled_df, use_container_width=True, hide_index=True)
        else:
            st.info("⏳ Scanning market for high-probability liquidity sweeps. No active positions.")
    except Exception as e:
        st.error(f"Database sync error: {e}")
else:
    st.error("⚠️ Data connection blocked or rate-limited. Terminal will automatically retry.")
