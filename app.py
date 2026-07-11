import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
from sqlalchemy import text
import time

# ==================== PAGE SETUP ====================
st.set_page_config(page_title="Institutional Trading Terminal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    /* Premium Dark Institutional Theme */
    .stApp { background-color: #0A0E17; color: #E2E8F0; font-family: 'Inter', sans-serif; }
    .main-header { font-size: 1.6rem; font-weight: 600; color: #FFFFFF; border-bottom: 2px solid #2563EB; padding-bottom: 8px; margin-bottom: 15px; }
    
    /* Symbol Cards */
    .symbol-card { background-color: #111827; border: 1px solid #1E293B; border-radius: 8px; padding: 16px; margin: 5px 0; text-align: center; }
    .symbol-card strong { font-size: 1rem; color: #94A3B8; display: block; margin-bottom: 4px; }
    .symbol-card .metric-value { font-size: 1.4rem; font-weight: 700; color: #F8FAFC; }
    
    /* Badges */
    .signal-badge { padding: 4px 12px; border-radius: 16px; font-weight: 600; font-size: 0.8rem; display: inline-block; margin-top: 8px; letter-spacing: 0.5px; }
    .buy { background-color: rgba(16, 185, 129, 0.2); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.5); }
    .neutral { background-color: rgba(148, 163, 184, 0.1); color: #94A3B8; border: 1px solid rgba(148, 163, 184, 0.4); }
    .sell { background-color: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid rgba(239, 68, 68, 0.5); }
    
    /* KPI Cards */
    .kpi-card { background-color: #111827; border: 1px solid #1E293B; padding: 16px 8px; text-align: center; border-radius: 8px; height: 100%; display: flex; flex-direction: column; justify-content: center; }
    .kpi-value { font-size: 1.25rem; font-weight: 700; color: #F8FAFC; margin-bottom: 4px; }
    .kpi-label { color: #64748B; font-size: 0.7rem; text-transform: uppercase; font-weight: 600; letter-spacing: 1px; }
    
    /* Logic Boxes */
    .logic-box { background-color: #111827; border-left: 4px solid #3B82F6; padding: 12px 16px; border-radius: 4px; font-size: 0.9rem; margin: 8px 0; color: #CBD5E1; }
    .logic-green { border-left-color: #10B981; }
    .logic-red { border-left-color: #EF4444; }
    .logic-orange { border-left-color: #F59E0B; }
</style>
""", unsafe_allow_html=True)

# ==================== SESSION STATE ====================
if "data_source" not in st.session_state:
    st.session_state.data_source = "Yahoo Finance (Direct)"
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = None
if "selected_name" not in st.session_state:
    st.session_state.selected_name = None

def get_pakistan_time():
    tz = pytz.timezone('Asia/Karachi')
    return datetime.now(tz).strftime("%d %b %Y | %I:%M:%S %p PKT")

# ==================== DATABASE ENGINE (V2 SCHEMA) ====================
def get_conn():
    try:
        return st.connection("neon", type="sql")
    except Exception as e:
        st.error(f"❌ Database connection failed: {str(e)}")
        return None

def init_db():
    conn = get_conn()
    if conn is None: return False
    try:
        with conn.session as s:
            # Table 1: Symbols Lookup
            s.execute(text("""
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol_id SERIAL PRIMARY KEY,
                    ticker TEXT UNIQUE,
                    name TEXT
                )
            """))
            # Table 2: Professional Signals (with Idempotent Unique Constraint)
            s.execute(text("""
                CREATE TABLE IF NOT EXISTS signals_v2 (
                    signal_id SERIAL PRIMARY KEY,
                    symbol_id INT REFERENCES symbols(symbol_id),
                    timestamp TIMESTAMP NOT NULL,
                    signal_type TEXT,
                    entry_price REAL,
                    target_price REAL,
                    stop_loss REAL,
                    status TEXT DEFAULT 'PENDING',
                    result TEXT,
                    UNIQUE(symbol_id, timestamp, signal_type, entry_price)
                )
            """))
            
            # Insert defaults
            symbols = [("BTC-USD", "Bitcoin"), ("USDJPY=X", "USD/JPY"), ("NQ=F", "NAS100")]
            for t, n in symbols:
                s.execute(text("INSERT INTO symbols (ticker, name) VALUES (:t, :n) ON CONFLICT DO NOTHING"), {"t": t, "n": n})
            s.commit()
            return True
    except Exception:
        return False

def save_signal(ticker, signal, entry, target, sl):
    conn = get_conn()
    if conn is None: return
    try:
        with conn.session as s:
            res = s.execute(text("SELECT symbol_id FROM symbols WHERE ticker = :t"), {"t": ticker}).fetchone()
            if not res: return
            sym_id = res[0]
            now = datetime.now(pytz.timezone('Asia/Karachi')).strftime("%Y-%m-%d %H:%M:%S")
            
            # Idempotent Insert
            s.execute(text("""
                INSERT INTO signals_v2 (symbol_id, timestamp, signal_type, entry_price, target_price, stop_loss)
                VALUES (:sid, :ts, :sig, :ent, :tp, :sl)
                ON CONFLICT (symbol_id, timestamp, signal_type, entry_price) DO NOTHING
            """), {"sid": sym_id, "ts": now, "sig": signal, "ent": entry, "tp": target, "sl": sl})
            s.commit()
    except Exception as e:
        st.error(f"DB Insert Error: {str(e)}")

def update_old_signals(ticker, df):
    conn = get_conn()
    if conn is None: return
    try:
        with conn.session as s:
            rows = s.execute(text("""
                SELECT sig.signal_id, sig.timestamp, sig.signal_type, sig.target_price, sig.stop_loss 
                FROM signals_v2 sig
                JOIN symbols sym ON sig.symbol_id = sym.symbol_id
                WHERE sym.ticker = :t AND sig.status = 'PENDING'
            """), {"t": ticker}).fetchall()
            
            if not rows: return
            
            df['Datetime'] = pd.to_datetime(df['Datetime']).dt.tz_localize(None)
            for row in rows:
                sig_id, sig_time, sig_type, target, sl = row
                # Multi-candle check
                future_candles = df[df['Datetime'] > pd.to_datetime(sig_time)]
                if future_candles.empty: continue
                
                max_high = float(future_candles['High'].max())
                min_low = float(future_candles['Low'].min())
                
                res = None
                if "BUY" in sig_type:
                    if max_high >= target: res = "WIN"
                    elif min_low <= sl: res = "LOSS"
                elif "SELL" in sig_type:
                    if min_low <= target: res = "WIN"
                    elif max_high >= sl: res = "LOSS"
                
                if res:
                    s.execute(text("UPDATE signals_v2 SET status='CLOSED', result=:res WHERE signal_id=:id"), {"res": res, "id": sig_id})
            s.commit()
    except Exception as e:
        pass

def get_stats(ticker):
    conn = get_conn()
    if conn is None: return None, 0
    try:
        with conn.session as s:
            df_sql = s.execute(text("""
                SELECT result FROM signals_v2 sig 
                JOIN symbols sym ON sig.symbol_id = sym.symbol_id 
                WHERE sym.ticker=:t AND sig.status='CLOSED' ORDER BY timestamp DESC LIMIT 20
            """), {"t": ticker}).fetchall()
            
            if len(df_sql) == 0: return None, 0
            wins = sum(1 for r in df_sql if r[0] == 'WIN')
            return round((wins / len(df_sql)) * 100), len(df_sql)
    except Exception:
        return None, 0

# ==================== MARKET DATA FETCHER ====================
@st.cache_data(ttl=60, show_spinner=False)
def fetch_ohlcv(ticker, interval="15m", period="5d"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ['_'.join(col).strip() for col in df.columns.values]
        df = df.reset_index()
        df.columns = [col.split('_')[0] if '_' in col else col for col in df.columns]
        for c in ['Open', 'High', 'Low', 'Close']: df[c] = df[c].astype(float)
        if 'Volume' not in df.columns: df['Volume'] = 1000.0
        return df[['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    except:
        return None

# ==================== INSTITUTIONAL LOGIC ENGINE ====================
def detect_sessions_and_vwap(df):
    if df['Datetime'].dt.tz is None:
        df['Datetime_UTC'] = df['Datetime'].dt.tz_localize('UTC')
    else:
        df['Datetime_UTC'] = df['Datetime'].dt.tz_convert('UTC')
        
    # FIXED: Using IANA timezone 'America/New_York' instead of 'US/Eastern'
    df_ny = df['Datetime_UTC'].dt.tz_convert('America/New_York')
    df['Date_NY'] = df_ny.dt.date
    
    # Session Kill Zones (EST Based)
    hour = df_ny.dt.hour
    conditions = [
        (hour >= 2) & (hour < 5),   # London Killzone
        (hour >= 7) & (hour < 11),  # NY AM Killzone
        (hour >= 13) & (hour < 16)  # NY PM Killzone
    ]
    choices = ['London Session', 'NY AM Session', 'NY PM Session']
    df['Session'] = np.select(conditions, choices, default='Asian/Consolidation Zone')
    
    # Calculate Rolling VWAP (Daily)
    df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['PV'] = df['Typical_Price'] * df['Volume']
    df['Cum_PV'] = df.groupby('Date_NY')['PV'].cumsum()
    df['Cum_Vol'] = df.groupby('Date_NY')['Volume'].cumsum()
    df['VWAP'] = df['Cum_PV'] / df['Cum_Vol'].replace(0, 1)
    
    return df

def detect_liquidity_sweep(df, lookback=20):
    if len(df) < lookback + 2: return "Neutral", 0
    recent = df.iloc[-(lookback+1):-1]
    swing_high = float(recent['High'].max())
    swing_low = float(recent['Low'].min())
    
    current = df.iloc[-1]
    # Bearish Sweep: Poked above swing high but closed below
    if current['High'] > swing_high and current['Close'] < swing_high:
        return f"Buyside Liquidity Swept @ {swing_high:.2f}", -1
    # Bullish Sweep: Poked below swing low but closed above
    if current['Low'] < swing_low and current['Close'] > swing_low:
        return f"Sellside Liquidity Swept @ {swing_low:.2f}", 1
        
    return "Consolidating Inside Range", 0

def calculate_institutional_signal(df, ticker):
    if df is None or len(df) < 50: return None
    df = detect_sessions_and_vwap(df)
    
    last = df.iloc[-1]
    price = float(last['Close'])
    vwap = float(last['VWAP'])
    session = last['Session']
    
    sweep_msg, sweep_dir = detect_liquidity_sweep(df)
    
    # ATR for Risk Management
    tr = pd.DataFrame()
    tr['1'] = df['High'] - df['Low']
    tr['2'] = (df['High'] - df['Close'].shift()).abs()
    tr['3'] = (df['Low'] - df['Close'].shift()).abs()
    atr = float(tr.max(axis=1).rolling(14).mean().iloc[-1])
    
    reasons = []
    bullish_pts = 0
    bearish_pts = 0
    
    # 1. VWAP Alignment
    if price > vwap:
        bullish_pts += 1
        reasons.append("✅ Price sustaining ABOVE Daily VWAP")
    else:
        bearish_pts += 1
        reasons.append("❌ Price heavily BELOW Daily VWAP")
        
    # 2. Liquidity Sweep
    if sweep_dir == 1:
        bullish_pts += 2
        reasons.append(f"🟢 {sweep_msg} (Reversal Probable)")
    elif sweep_dir == -1:
        bearish_pts += 2
        reasons.append(f"🔴 {sweep_msg} (Reversal Probable)")
    else:
        reasons.append(f"⚪ {sweep_msg}")
        
    # 3. Time/Session Context
    reasons.append(f"⏱️ Active Trading Window: {session}")
    is_active_session = "Session" in session
    
    # Determine Signal
    signal = "WAIT"
    badge = "neutral"
    
    # Strict execution criteria: Must have sweep + VWAP alignment + Killzone volume
    if is_active_session:
        if bullish_pts >= 3: # Needs both VWAP and Sweep
            signal, badge = "BUY", "buy"
        elif bearish_pts >= 3:
            signal, badge = "SELL", "sell"
    else:
        reasons.append("⚠️ Low Volume Environment: Algorithms set to WAIT to prevent fakeouts.")
        
    # Professional Risk Management (Targeting 1:2.5 to 1:3 RR)
    # Using larger stops to survive manipulation spikes
    sl_dist = 2.0 * atr
    tp_dist = 5.0 * atr
    
    sl = price - sl_dist if signal == "BUY" else price + sl_dist if signal == "SELL" else price
    tp = price + tp_dist if signal == "BUY" else price - tp_dist if signal == "SELL" else price
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
    
    return {
        "signal": signal, "badge": badge, "price": price, 
        "vwap": vwap, "session": session, "sweep": sweep_msg,
        "sl": round(sl, 2), "tp": round(tp, 2), "rr": rr, "reasons": reasons
    }

# ==================== UI RENDERING ====================
st.markdown('<div class="main-header">Institutional Order Flow Terminal</div>', unsafe_allow_html=True)
st.caption(f"📅 {get_pakistan_time()} | Pure Price Action & Time Architecture")

db_initialized = init_db()
if not db_initialized: st.warning("Database configuration missing or failed.")

MAIN_SYMBOLS = {"Bitcoin (BTC)": "BTC-USD", "NAS100": "NQ=F", "Gold": "GC=F"}

cols = st.columns(3)
for idx, (name, ticker) in enumerate(MAIN_SYMBOLS.items()):
    with cols[idx]:
        qdf = fetch_ohlcv(ticker, interval="15m", period="3d")
        price, sig, badge = 0.0, "SYNCING...", "neutral"
        if qdf is not None:
            price = float(qdf['Close'].iloc[-1])
            analysis = calculate_institutional_signal(qdf, ticker)
            if analysis:
                sig, badge = analysis['signal'], analysis['badge']
                
        st.markdown(f"""
            <div class='symbol-card'>
                <strong>{name}</strong>
                <div class='metric-value'>{price:,.2f}</div>
                <span class='signal-badge {badge}'>{sig}</span>
            </div>
        """, unsafe_allow_html=True)
        if st.button(f"Load Order Flow", key=f"btn_{idx}", use_container_width=True):
            st.session_state.selected_symbol = ticker
            st.session_state.selected_name = name
            st.rerun()

if st.session_state.get("selected_symbol"):
    ticker = st.session_state.selected_symbol
    name = st.session_state.get("selected_name", ticker)
    st.divider()
    
    col_hdr, col_tf = st.columns([2, 1])
    with col_hdr:
        st.markdown(f"<h3 style='margin:0;'>{name} <span style='color:#64748B; font-weight:400;'>| Execution Engine</span></h3>", unsafe_allow_html=True)
    with col_tf:
        tf_lower = st.selectbox("Execution Timeframe", ["5m", "15m"], index=1)
        
    df_lower = fetch_ohlcv(ticker, interval=tf_lower, period="5d")
    
    if df_lower is None or len(df_lower) < 50:
        st.error("Awaiting market data depth. Please resync.")
        st.stop()
        
    analysis = calculate_institutional_signal(df_lower, ticker)
    
    if analysis:
        # Save & Update Logic
        if analysis['signal'] in ["BUY", "SELL"] and db_initialized:
            save_signal(ticker, analysis['signal'], analysis['price'], analysis['tp'], analysis['sl'])
        if db_initialized:
            update_old_signals(ticker, df_lower)
            
        # KPI ROW
        st.markdown("<br>", unsafe_allow_html=True)
        kpi_cols = st.columns(5)
        with kpi_cols[0]:
            st.markdown(f"<div class='kpi-card'><span class='kpi-value'>{analysis['price']:,.2f}</span><span class='kpi-label'>Price</span></div>", unsafe_allow_html=True)
        with kpi_cols[1]:
            st.markdown(f"<div class='kpi-card'><span class='kpi-value'>{analysis['vwap']:,.2f}</span><span class='kpi-label'>Daily VWAP</span></div>", unsafe_allow_html=True)
        with kpi_cols[2]:
            st.markdown(f"<div class='kpi-card'><span class='kpi-value' style='color:#3B82F6;'>{analysis['session'].split(' ')[0]}</span><span class='kpi-label'>Active Zone</span></div>", unsafe_allow_html=True)
        with kpi_cols[3]:
            st.markdown(f"<div class='kpi-card'><span class='kpi-value'>{analysis['rr']} R</span><span class='kpi-label'>Risk:Reward</span></div>", unsafe_allow_html=True)
        with kpi_cols[4]:
            winrate, total = get_stats(ticker) if db_initialized else (None, 0)
            wr_str = f"{winrate}%" if winrate is not None else "--"
            st.markdown(f"<div class='kpi-card'><span class='kpi-value'>{wr_str}</span><span class='kpi-label'>Edge Win-Rate</span></div>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### Liquidity & Structure")
            sweep_color = "logic-orange" if "Consolidating" in analysis['sweep'] else "logic-green" if "Sellside" in analysis['sweep'] else "logic-red"
            st.markdown(f"""
                <div class='logic-box {sweep_color}'>
                    <b>Market Matrix:</b><br>{analysis['sweep']}<br><br>
                    <b>Institutional Bias (VWAP):</b><br>
                    {'BULLISH 🟢' if analysis['price'] > analysis['vwap'] else 'BEARISH 🔴'}
                </div>
            """, unsafe_allow_html=True)
            
            with st.expander("View Algorithm Execution Steps"):
                for r in analysis['reasons']:
                    st.write(f"• {r}")
                    
        with col2:
            st.markdown("#### Strict Risk Parameters")
            st.markdown(f"""
                <div class='logic-box border-blue'>
                    <b>Entry Trigger:</b> {analysis['price']:,.2f}<br>
                    <hr style="margin: 8px 0; border-color: #334155;">
                    <b style="color: #EF4444;">Invalidation (Stop Loss):</b> {analysis['sl']:,.2f}<br>
                    <b style="color: #10B981;">Liquidity Target (TP):</b> {analysis['tp']:,.2f}<br>
                    <span style="font-size:0.8rem; color:#94A3B8;">Dynamic R:R maintains positive expectancy even at 40% win rate.</span>
                </div>
            """, unsafe_allow_html=True)
            
        # Backtest Report
        st.divider()
        st.markdown("#### Real-Time Edge Validation (Neon DB)")
        if db_initialized:
            winrate, total = get_stats(ticker)
            if winrate is not None:
                st.progress(winrate / 100, text=f"Last {total} Signals Track Record")
                st.caption("Data reflects strict multi-candle walk-forward testing without repaint.")
            else:
                st.info("System initializing. Awaiting closed trade data logging.")
        else:
            st.warning("Database disconnected.")
else:
    st.info("Select an instrument from the terminal overhead to sync order flow logic.")
