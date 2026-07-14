import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from sqlalchemy import create_engine, text
import requests
from datetime import datetime
import pytz

# Page Configuration
st.set_page_config(page_title="Institutional Trading Terminal", layout="wide")

# ==========================================
# 1. DATABASE CONNECTION & INITIALIZATION
# ==========================================
@st.cache_resource
def get_db_engine():
    """Neon Postgres Connection engine with robust pooling and pre-ping."""
    db_url = st.secrets["DATABASE_URL"]
    if "?sslmode=" not in db_url:
        db_url += "?sslmode=require"
    
    return create_engine(
        db_url,
        pool_size=10,
        max_overflow=20,
        pool_recycle=300,
        pool_pre_ping=True
    )

engine = get_db_engine()

def init_db():
    """Initializes schema v2. Safely drops and recreates table if 'id' column is missing."""
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
# 5. CACHED DATA DOWNLOADING (ANTI-RATE LIMIT)
# ==========================================
@st.cache_data(ttl=120)
def fetch_ticker_data(symbol, timeframe):
    """Downloads and returns market data from yfinance with cache protection."""
    return yf.download(symbol, period="5d", interval=timeframe, progress=False)

# ==========================================
# 6. CUSTOM SVG CHART GENERATOR (BYPASSING ALTAIR BUG)
# ==========================================
def generate_svg_chart(df, width=900, height=350):
    """Generates a responsive and beautiful SVG line chart for Close and VWAP."""
    if df is None or df.empty:
        return "<div style='color: white; padding: 20px;'>No market analysis data available for chart visualization.</div>"
    
    # Reset index to extract sequential intervals
    df_reset = df.reset_index()
    close_vals = df_reset['Close'].tolist()
    vwap_vals = df_reset['VWAP'].tolist()
    dates = df_reset.iloc[:, 0].dt.strftime('%H:%M').tolist()
    
    # Scale ranges with dynamic padding
    all_vals = close_vals + vwap_vals
    min_val = min(all_vals) * 0.999
    max_val = max(all_vals) * 1.001
    val_range = max_val - min_val if max_val != min_val else 1.0
    
    padding_top = 25
    padding_bottom = 35
    padding_left = 70
    padding_right = 25
    
    plot_width = width - padding_left - padding_right
    plot_height = height - padding_top - padding_bottom
    
    def get_x(i):
        return padding_left + (i / (len(close_vals) - 1)) * plot_width
        
    def get_y(val):
        return padding_top + plot_height - ((val - min_val) / val_range) * plot_height
        
    # Plot line paths
    close_points = [f"{get_x(i):.1f},{get_y(close_vals[i]):.1f}" for i in range(len(close_vals))]
    vwap_points = [f"{get_x(i):.1f},{get_y(vwap_vals[i]):.1f}" for i in range(len(vwap_vals))]
    
    close_path = "M " + " L ".join(close_points)
    vwap_path = "M " + " L ".join(vwap_points)
    
    # Construct complete responsive SVG code
    svg = f"""
    <svg viewBox="0 0 {width} {height}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="background-color: #0E1117; border-radius: 8px; font-family: system-ui, sans-serif;">
        <!-- Axes lines -->
        <line x1="{padding_left}" y1="{padding_top}" x2="{padding_left}" y2="{padding_top + plot_height}" stroke="#31333F" stroke-width="1"/>
        <line x1="{padding_left}" y1="{padding_top + plot_height}" x2="{width - padding_right}" y2="{padding_top + plot_height}" stroke="#31333F" stroke-width="1"/>
        
        <!-- Y Gridlines and Labels -->
        <text x="{padding_left - 12}" y="{get_y(min_val) + 4}" fill="#808495" font-size="11" text-anchor="end">{min_val:.2f}</text>
        <text x="{padding_left - 12}" y="{get_y((min_val + max_val)/2) + 4}" fill="#808495" font-size="11" text-anchor="end">{(min_val + max_val)/2:.2f}</text>
        <text x="{padding_left - 12}" y="{get_y(max_val) + 4}" fill="#808495" font-size="11" text-anchor="end">{max_val:.2f}</text>
        
        <line x1="{padding_left}" y1="{get_y((min_val+max_val)/2)}" x2="{width - padding_right}" y2="{get_y((min_val+max_val)/2)}" stroke="#262730" stroke-width="1" stroke-dasharray="4"/>
        <line x1="{padding_left}" y1="{get_y(max_val)}" x2="{width - padding_right}" y2="{get_y(max_val)}" stroke="#262730" stroke-width="1" stroke-dasharray="4"/>
        
        <!-- X Labels (First, Middle, Last) -->
        <text x="{get_x(0)}" y="{height - 12}" fill="#808495" font-size="11" text-anchor="middle">{dates[0]}</text>
        <text x="{get_x(len(dates)//2)}" y="{height - 12}" fill="#808495" font-size="11" text-anchor="middle">{dates[len(dates)//2]}</text>
        <text x="{get_x(len(dates)-1)}" y="{height - 12}" fill="#808495" font-size="11" text-anchor="middle">{dates[-1]}</text>
        
        <!-- Daily VWAP line (Dashed Neon Coral) -->
        <path d="{vwap_path}" fill="none" stroke="#FF4B4B" stroke-width="2" stroke-dasharray="3"/>
        
        <!-- Close Price line (Cyan Neon Solid) -->
        <path d="{close_path}" fill="none" stroke="#00F0FF" stroke-width="2.5"/>
        
        <!-- Custom Legends -->
        <rect x="{padding_left + 20}" y="10" width="10" height="10" fill="#00F0FF" rx="2"/>
        <text x="{padding_left + 35}" y="20" fill="#F0F2F6" font-size="12">Close Price</text>
        
        <rect x="{padding_left + 140}" y="10" width="10" height="10" fill="#FF4B4B" rx="2"/>
        <text x="{padding_left + 155}" y="20" fill="#F0F2F6" font-size="12">Daily VWAP</text>
    </svg>
    """
    return svg

# ==========================================
# 7. CORE INSTITUTIONAL LOGIC (WITH FALLBACK)
# ==========================================
def generate_signals_engine(selected_symbol, timeframe="15m"):
    """Core algorithmic engine with Yahoo Finance fallback protection."""
    try:
        actual_symbol = selected_symbol
        df = fetch_ticker_data(actual_symbol, timeframe)
        
        # Fallback Mechanism: Switching to Crypto if blocked
        if df.empty or len(df) < 35:
            st.warning(f"⚠️ {actual_symbol} data blocked by Yahoo API. Auto-switching to BTC-USD Fallback.")
            actual_symbol = "BTC-USD"
            df = fetch_ticker_data(actual_symbol, timeframe)
            
        if df.empty or len(df) < 35:
            return None
        
        # Clean Column Names (Handles MultiIndex)
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
        # Calculate Daily VWAP
        cum_vol = df['Volume'].cumsum()
        cum_vol_price = (df['Close'] * df['Volume']).cumsum()
        df['VWAP'] = (cum_vol_price / cum_vol).fillna(df['Close'])
        
        # Volatility and Average Volume
        df['ATR'] = df['Close'].diff().abs().rolling(14).mean()
        df['Vol_MA'] = df['Volume'].rolling(20).mean()
        
        # Lookback for Swings
        lookback = 30
        df['Recent_High'] = df['High'].rolling(lookback).max()
        df['Recent_Low'] = df['Low'].rolling(lookback).min()
        
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        # Candle timestamp for DB unique constraint mapping
        candle_timestamp = df.index[-1]
        if candle_timestamp.tzinfo is not None:
            candle_timestamp = candle_timestamp.tz_convert('UTC').tz_localize(None)
        
        current_price = float(last_row['Close'])
        current_volume = float(last_row['Volume'])
        avg_volume = float(last_row['Vol_MA'])
        vwap = float(last_row['VWAP'])
        atr = float(last_row['ATR'])
        
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
            "actual_symbol": actual_symbol,
            "signal": signal,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "vwap": vwap,
            "current_price": current_price,
            "news_blocked": news_block,
            "timestamp": candle_timestamp,
            "plot_df": df[['Close', 'VWAP']].tail(50)  # Safe sliced dataframe for plotting
        }
    except Exception as e:
        return None

# ==========================================
# 8. STREAMLIT UI & DASHBOARD
# ==========================================
st.title("🛡️ Institutional Grade Algorithmic Terminal")

# Main Screen - Market Analysis Controls (Asset Selection)
st.markdown("### ⚙️ Market Analysis Settings")
col_select1, col_select2 = st.columns(2)
with col_select1:
    symbol_input = st.selectbox("Select Asset", ["GC=F", "BTC-USD", "ETH-USD"], index=0)
with col_select2:
    timeframe = st.selectbox("Select Timeframe", ["5m", "15m", "1h"], index=1)

# Sidebar - Admin Dashboard Panel Only
st.sidebar.subheader("⚙️ Admin Control Panel")
admin_password = st.sidebar.text_input("Enter Admin Password", type="password")

if admin_password == "mubeen123":
    st.sidebar.success("Access Granted!")
    col_admin1, col_admin2 = st.sidebar.columns(2)
    with col_admin1:
        if st.button("🔴 Cancel All Pending"):
            cancel_all_pending_signals()
            st.sidebar.success("Pending Signals Cancelled!")
            st.rerun()
    with col_admin2:
        if st.button("⚠️ Reset Entire DB"):
            reset_entire_database()
            st.sidebar.warning("Database Tables Cleared!")
            st.rerun()

# Run Engine and Auto-Timeout Updates
engine_output = generate_signals_engine(symbol_input, timeframe)

if engine_output:
    used_symbol = engine_output["actual_symbol"]
    current_price = engine_output["current_price"]
    run_auto_timeout_and_updates(used_symbol, current_price)
    
    # Display Status Cards
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(f"Current Price ({used_symbol})", f"{current_price:.2f}")
    with col2:
        st.metric("Daily VWAP Level", f"{engine_output['vwap']:.2f}")
    with col3:
        status_text = "🚨 MACRO NEWS BLOCK" if engine_output["news_blocked"] else "⚡ RUNNING"
        st.metric("News Filter Status", status_text)
        
    # Process New Signals
    signal_type = engine_output["signal"]
    if signal_type in ["BUY", "SELL"]:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO signals_v2 (symbol, timestamp, signal_type, entry_price, tp, sl, status, result)
                VALUES (:symbol, :timestamp, :signal_type, :entry_price, :tp, :sl, 'PENDING', 'OPEN')
                ON CONFLICT (symbol, timestamp, signal_type, entry_price) DO NOTHING
            """), {
                "symbol": used_symbol,
                "timestamp": engine_output["timestamp"],  # Precise candle time to prevent duplicates
                "signal_type": signal_type,
                "entry_price": engine_output["entry"],
                "tp": engine_output["tp"],
                "sl": engine_output["sl"]
            })
            
    # Display Live Price vs VWAP Chart using safe custom SVG (Anti-Crash)
    st.subheader(f"📈 {used_symbol} Live Price vs Daily VWAP")
    chart_svg = generate_svg_chart(engine_output["plot_df"])
    st.components.v1.html(chart_svg, height=360)
else:
    st.error("⚠️ Yahoo Finance API is currently rate-limited or offline. Terminal features are active, but new price feeds are delayed.")

# Display Current Signals Status from DB (Always Visible!)
st.subheader("📊 Live Tracking Dashboard")
try:
    with engine.connect() as conn:
        active_signals = conn.execute(text(
            "SELECT timestamp, signal_type, symbol, entry_price, tp, sl, status, result FROM signals_v2 ORDER BY id DESC LIMIT 10"
        )).fetchall()
        
    if active_signals:
        df_signals = pd.DataFrame(active_signals, columns=["Time", "Type", "Asset", "Entry Price", "Take Profit", "Stop Loss", "Status", "Result"])
        st.dataframe(df_signals, use_container_width=True)
    else:
        st.info("No active signals recorded yet. Waiting for structural sweeps...")
except Exception as e:
    st.error(f"Database error while loading dashboard: {e}")
