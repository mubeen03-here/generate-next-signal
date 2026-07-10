import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from sqlalchemy import text
import time
import requests

# ==================== API KEYS SETUP (Optional - Alerts Disabled) ====================
st.set_page_config(page_title="Pro Trading System", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: #fafafa; }
    .main-header { 
        font-size: 1.3rem; 
        font-weight: 700; 
        background: linear-gradient(90deg, #00ff9f, #00b8ff);
        -webkit-background-clip: text; 
        -webkit-text-fill-color: transparent; 
        display: inline-block;
        margin: 0;
        padding: 0;
    }
    .symbol-card { 
        background-color: #161b22; 
        border: 1px solid #30363d; 
        border-radius: 8px; 
        padding: 0.4rem 0.6rem; 
        margin: 0.2rem 0;
    }
    .symbol-card strong { font-size: 0.8rem; }
    .symbol-card .metric-value { font-size: 1rem; font-weight: 700; }
    .signal-badge { 
        padding: 0.1rem 0.5rem; 
        border-radius: 12px; 
        font-weight: 700; 
        font-size: 0.65rem; 
        display: inline-block; 
    }
    .signal-buy { background-color: #00c853; color: white; }
    .signal-sell { background-color: #f44336; color: white; }
    .signal-wait { background-color: #ff9800; color: white; }
    .signal-neutral { background-color: #666; color: white; }
    .info-box { background-color: #1a2332; border-left: 3px solid #00b8ff; padding: 6px 10px; border-radius: 4px; font-size: 0.8rem; margin: 2px 0; }
    .regime-box { background-color: #1a2a1a; border-left: 3px solid #44ff88; padding: 6px 10px; border-radius: 4px; font-size: 0.8rem; margin: 2px 0; }
    .kpi-card { background-color: transparent; padding: 0.3rem 0.2rem; text-align: center; border-radius: 0; }
    .kpi-icon { font-size: 1rem; display: inline-block; }
    .kpi-value { font-size: 1rem; font-weight: 700; }
    .kpi-label { color: #666; font-size: 0.55rem; text-transform: uppercase; letter-spacing: 0.5px; }
</style>
""", unsafe_allow_html=True)

# ==================== SESSION STATE ====================
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = None
if "selected_name" not in st.session_state:
    st.session_state.selected_name = None

def get_pakistan_time():
    tz = pytz.timezone('Asia/Karachi')
    return datetime.now(tz).strftime("%d %b %Y | %I:%M:%S %p PKT")

# ==================== DATABASE (No Alerts) ====================
def get_conn():
    try:
        return st.connection("neon", type="sql")
    except:
        return None

def init_db():
    conn = get_conn()
    if conn is None:
        return False
    try:
        with conn.session as s:
            s.execute(text("""
                CREATE TABLE IF NOT EXISTS signal_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TEXT,
                    symbol TEXT,
                    signal TEXT,
                    entry_price REAL,
                    target_price REAL,
                    stop_loss REAL,
                    status TEXT DEFAULT 'PENDING',
                    result TEXT
                )
            """))
            s.commit()
            return True
    except:
        return False

def save_signal(symbol, signal, entry, target, sl):
    conn = get_conn()
    if conn is None:
        return
    try:
        with conn.session as s:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            s.execute(text("""
                INSERT INTO signal_history (timestamp, symbol, signal, entry_price, target_price, stop_loss)
                VALUES (:ts, :sym, :sig, :entry, :target, :sl)
            """), {
                "ts": now,
                "sym": symbol,
                "sig": signal,
                "entry": entry,
                "target": target,
                "sl": sl
            })
            s.commit()
    except:
        pass

def get_stats(symbol):
    conn = get_conn()
    if conn is None:
        return None, 0
    try:
        df = conn.query(
            f"SELECT * FROM signal_history WHERE symbol='{symbol}' AND status='CLOSED' ORDER BY timestamp DESC LIMIT 10",
            ttl="5s"
        )
        if len(df) == 0:
            return None, 0
        wins = len(df[df['result'] == 'WIN'])
        return round((wins / len(df)) * 100), len(df)
    except:
        return None, 0

# ==================== DATA FETCH ====================
@st.cache_data(ttl=40, show_spinner=False)
def fetch_ohlcv(ticker, interval="15m", period="3d"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ['_'.join(col).strip() for col in df.columns.values]
        df = df.reset_index()
        rename_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if 'datetime' in col_lower or 'date' in col_lower:
                rename_map[col] = 'Datetime'
            elif 'open' in col_lower:
                rename_map[col] = 'Open'
            elif 'high' in col_lower:
                rename_map[col] = 'High'
            elif 'low' in col_lower:
                rename_map[col] = 'Low'
            elif 'close' in col_lower:
                rename_map[col] = 'Close'
            elif 'volume' in col_lower:
                rename_map[col] = 'Volume'
        df = df.rename(columns=rename_map)
        if 'Close' not in df.columns:
            return None
        if 'Volume' not in df.columns:
            df['Volume'] = 1000
        return df[['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    except:
        return None

# ==================== SESSION DETECTION ====================
def detect_session():
    """Current trading session detect karega (Pakistan Time)"""
    tz = pytz.timezone('Asia/Karachi')
    now = datetime.now(tz)
    hour = now.hour
    minute = now.minute
    time_now = hour + minute / 60.0
    
    asia_start, asia_end = 0.0, 6.0
    london_start, london_end = 6.0, 14.0
    ny_start, ny_end = 13.0, 22.0
    
    session = "UNKNOWN"
    session_info = ""
    
    if london_start <= time_now < london_end:
        session = "LONDON"
        if 7.0 <= time_now < 9.0:
            session_info = "🔥 London Kill Zone (High Volatility)"
        else:
            session_info = "🇬🇧 London Session"
    elif ny_start <= time_now < ny_end:
        session = "NEW YORK"
        if 13.5 <= time_now < 15.5:
            session_info = "🔥 New York Kill Zone (High Volatility)"
        else:
            session_info = "🇺🇸 New York Session"
    elif asia_start <= time_now < asia_end:
        session = "ASIA"
        if 2.0 <= time_now < 4.0:
            session_info = "🔥 Asia Kill Zone"
        else:
            session_info = "🇯🇵 Asia Session"
    else:
        session_info = "⏳ Off-Session / Low Volatility"
    
    return session, session_info

# ==================== ADVANCED TREND REGIME (ADX + RANGE) ====================
def detect_trend_regime(df):
    """ADX aur Bollinger Bands use kar ke trending/ranging detect karega"""
    if df is None or len(df) < 30:
        return "NEUTRAL", "NEUTRAL", "Insufficient data"
    
    close = df['Close'].astype(float)
    high = df['High'].astype(float)
    low = df['Low'].astype(float)
    
    # True Range
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Directional Movement
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0))
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0))
    
    atr = tr.rolling(window=14).mean()
    plus_di = 100 * (plus_dm.rolling(window=14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=14).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(window=14).mean()
    
    adx_value = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 20
    
    # Bollinger Bands Width
    sma = close.rolling(window=20).mean()
    std = close.rolling(window=20).std()
    bb_width = (2 * std / sma) * 100
    bb_width_value = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 2.0
    
    # Trend Direction (EMA)
    ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    direction = "BULLISH" if ema9 > ema21 else "BEARISH" if ema9 < ema21 else "NEUTRAL"
    
    # Regime Classification
    if adx_value > 30 and bb_width_value > 3.0:
        regime = "STRONG TRENDING"
        regime_info = f"ADX: {adx_value:.0f} (Strong) | BB Width: {bb_width_value:.1f}%"
    elif adx_value > 20 and bb_width_value > 2.0:
        regime = "WEAK TRENDING"
        regime_info = f"ADX: {adx_value:.0f} (Moderate) | BB Width: {bb_width_value:.1f}%"
    else:
        regime = "RANGING"
        regime_info = f"ADX: {adx_value:.0f} (Weak) | BB Width: {bb_width_value:.1f}% (Compressed)"
    
    return regime, direction, regime_info

# ==================== PROFESSIONAL MARKET STRUCTURE ENGINE ====================
def find_swing_points(df, lookback=5):
    """Find swing highs and lows"""
    highs = []
    lows = []
    for i in range(lookback, len(df) - lookback):
        if df['High'].iloc[i] == df['High'].iloc[i-lookback:i+lookback+1].max():
            highs.append((i, df['High'].iloc[i]))
        if df['Low'].iloc[i] == df['Low'].iloc[i-lookback:i+lookback+1].min():
            lows.append((i, df['Low'].iloc[i]))
    return highs, lows

def detect_market_structure(df, lookback=5):
    """Professional market structure detection"""
    if df is None or len(df) < lookback + 2:
        return "NEUTRAL", "No structure"
    
    highs, lows = find_swing_points(df, lookback)
    if len(highs) < 2 and len(lows) < 2:
        return "NEUTRAL", "Not enough swings"
    
    price = float(df['Close'].iloc[-1])
    last_high = highs[-1][1] if highs else price
    last_low = lows[-1][1] if lows else price
    
    if price > last_high:
        return "BULLISH", f"BOS Break above {last_high:.2f}"
    if price < last_low:
        return "BEARISH", f"BOS Break below {last_low:.2f}"
    
    if len(highs) > 2 and len(lows) > 2:
        if highs[-1][1] < highs[-2][1] and lows[-1][1] > lows[-2][1]:
            return "NEUTRAL", "CHoCH detected - possible reversal"
    
    return "NEUTRAL", "No clear structure"

def detect_liquidity_sweep(df, lookback=10):
    """Detect if price swept recent EQH/EQL"""
    if df is None or len(df) < lookback:
        return False, None, None
    
    recent = df.iloc[-lookback:-1]
    price = float(df['Close'].iloc[-1])
    
    highs = recent['High'].tolist()
    eqh = None
    for i in range(len(highs) - 1):
        for j in range(i + 1, len(highs)):
            if abs(highs[i] - highs[j]) / price < 0.001:
                eqh = (highs[i] + highs[j]) / 2
                break
        if eqh:
            break
    
    lows = recent['Low'].tolist()
    eql = None
    for i in range(len(lows) - 1):
        for j in range(i + 1, len(lows)):
            if abs(lows[i] - lows[j]) / price < 0.001:
                eql = (lows[i] + lows[j]) / 2
                break
        if eql:
            break
    
    if eqh and price > eqh:
        return True, f"EQH Swept @ {eqh:.2f}", eqh
    if eql and price < eql:
        return True, f"EQL Swept @ {eql:.2f}", eql
    
    return False, None, None

def calculate_vwap(df):
    """Calculate VWAP from OHLCV data"""
    if df is None or len(df) < 10:
        return None
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    cum_volume = df['Volume'].cumsum()
    cum_typical_volume = (typical_price * df['Volume']).cumsum()
    vwap = cum_typical_volume / cum_volume
    return float(vwap.iloc[-1])

def calculate_volume_profile(df, bins=20):
    """Calculate Volume Profile (POC, VAH, VAL)"""
    if df is None or len(df) < 10:
        return None, None, None
    
    prices = df['Close'].tolist()
    volumes = df['Volume'].tolist()
    min_price = min(prices)
    max_price = max(prices)
    bin_width = (max_price - min_price) / bins if bins > 0 else 1
    
    volume_by_price = {}
    for p, v in zip(prices, volumes):
        bin_idx = int((p - min_price) / bin_width) if bin_width > 0 else 0
        volume_by_price[bin_idx] = volume_by_price.get(bin_idx, 0) + v
    
    if not volume_by_price:
        return None, None, None
    
    poc_idx = max(volume_by_price, key=volume_by_price.get)
    poc_price = min_price + (poc_idx + 0.5) * bin_width
    
    total_volume = sum(volume_by_price.values())
    target_volume = total_volume * 0.7
    sorted_bins = sorted(volume_by_price.items())
    cum_vol = 0
    vah = None
    val = None
    
    for idx, vol in sorted_bins:
        cum_vol += vol
        if cum_vol >= target_volume / 2 and not val:
            val = min_price + (idx + 0.5) * bin_width
        if cum_vol >= target_volume:
            vah = min_price + (idx + 0.5) * bin_width
            break
    
    return poc_price, vah, val

# ==================== MAIN SIGNAL ENGINE ====================
def calculate_professional_signal(df, symbol=""):
    """Professional signal engine - Structure + Liquidity + VWAP + Volume Profile + Session + Regime"""
    if df is None or len(df) < 30:
        return None
    
    df = df.copy()
    price = float(df['Close'].iloc[-1])
    signal_details = {}
    
    # ---- 1. Session Detection ----
    session, session_info = detect_session()
    signal_details['session'] = session
    signal_details['session_info'] = session_info
    
    # ---- 2. Trend Regime ----
    regime, direction, regime_info = detect_trend_regime(df)
    signal_details['regime'] = regime
    signal_details['direction'] = direction
    signal_details['regime_info'] = regime_info
    
    # ---- 3. Market Structure ----
    structure, structure_reason = detect_market_structure(df, lookback=5)
    signal_details['structure'] = structure
    signal_details['structure_reason'] = structure_reason
    
    # ---- 4. Liquidity Sweep ----
    sweep_detected, sweep_type, sweep_level = detect_liquidity_sweep(df, lookback=10)
    signal_details['sweep_detected'] = sweep_detected
    signal_details['sweep_type'] = sweep_type
    
    # ---- 5. VWAP ----
    vwap = calculate_vwap(df)
    vwap_position = "Above VWAP" if vwap and price > vwap else "Below VWAP" if vwap else "Unknown"
    signal_details['vwap'] = vwap
    signal_details['vwap_position'] = vwap_position
    
    # ---- 6. Volume Profile ----
    poc, vah, val = calculate_volume_profile(df, bins=20)
    signal_details['poc'] = poc
    signal_details['vah'] = vah
    signal_details['val'] = val
    
    # ---- 7. ATR ----
    high = df['High'].astype(float)
    low = df['Low'].astype(float)
    close = df['Close'].astype(float)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]
    signal_details['atr'] = atr if not pd.isna(atr) else 0
    
    # ---- 8. ENTRY LOGIC (Reaction Based) ----
    signal = "WAIT"
    reason = []
    entry_price = price
    stop_loss = None
    take_profit = None
    
    # Rule 1: Liquidity Sweep + Structure Break = High Probability Setup
    if sweep_detected:
        if "EQH" in sweep_type and structure == "BULLISH":
            signal = "BUY"
            reason.append(f"✅ EQH Sweep @ {sweep_level:.2f} + BULLISH structure")
        elif "EQL" in sweep_type and structure == "BEARISH":
            signal = "SELL"
            reason.append(f"✅ EQL Sweep @ {sweep_level:.2f} + BEARISH structure")
    
    # Rule 2: Structure Break + VWAP Confirmation
    if structure == "BULLISH" and vwap and price > vwap and signal == "WAIT":
        signal = "BUY"
        reason.append(f"✅ BULLISH structure + {vwap_position}")
    elif structure == "BEARISH" and vwap and price < vwap and signal == "WAIT":
        signal = "SELL"
        reason.append(f"✅ BEARISH structure + {vwap_position}")
    
    # Rule 3: Volume Profile POC Bounce
    if poc and abs(price - poc) / price < 0.005:
        if structure == "BULLISH":
            signal = "BUY"
            reason.append(f"✅ Price near POC @ {poc:.2f} + BULLISH structure")
        elif structure == "BEARISH":
            signal = "SELL"
            reason.append(f"✅ Price near POC @ {poc:.2f} + BEARISH structure")
    
    # ---- Session Filter (Info only) ----
    if session == "ASIA" and "Kill" not in session_info and signal != "WAIT":
        reason.append("⏳ Asia Session (Low volatility) - Caution advised")
    
    # ---- Regime Filter (Info only) ----
    if regime == "RANGING" and signal != "WAIT":
        reason.append(f"⚠️ Ranging regime ({regime_info}) - Consider partial size")
    
    # ---- RISK MANAGEMENT ----
    atr_val = signal_details['atr']
    if signal == "BUY":
        stop_loss = price - (1.5 * atr_val) if atr_val > 0 else price * 0.98
        take_profit = price + (2.0 * atr_val) if atr_val > 0 else price * 1.02
        reason.append(f"📈 BUY | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        save_signal(symbol, signal, entry_price, take_profit, stop_loss)
    elif signal == "SELL":
        stop_loss = price + (1.5 * atr_val) if atr_val > 0 else price * 1.02
        take_profit = price - (2.0 * atr_val) if atr_val > 0 else price * 0.98
        reason.append(f"📉 SELL | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        save_signal(symbol, signal, entry_price, take_profit, stop_loss)
    else:
        reason.append("⏳ WAIT - No clear setup")
    
    signal_details['signal'] = signal
    signal_details['price'] = price
    signal_details['reason'] = reason
    signal_details['stop_loss'] = stop_loss
    signal_details['take_profit'] = take_profit
    
    return signal_details

# ==================== UI ====================
st.markdown('<span class="main-header">🚀 Pro Trading System</span>', unsafe_allow_html=True)
st.caption(f"📅 {get_pakistan_time()} | Structure + Liquidity + VWAP + Volume Profile + Session + Regime")

# ==================== ALERTS STATUS ====================
st.info("🔕 **Alerts Disabled:** Telegram & Email alerts are temporarily paused. System will save signals to database only.")

MAIN_SYMBOLS = {"Bitcoin (BTC)": "BTC-USD", "USD/JPY": "USDJPY=X", "NAS100": "NQ=F"}

db_initialized = init_db()
if db_initialized:
    st.success("✅ Database connected successfully!")
else:
    st.warning("⚠️ Database connection failed. Signals will not be saved.")

cols = st.columns(3)
for idx, (name, ticker) in enumerate(MAIN_SYMBOLS.items()):
    with cols[idx]:
        qdf = fetch_ohlcv(ticker, interval="60m", period="2d")
        price, pct, sig = 0.0, 0.0, "WAIT"
        temp_analysis = None
        if qdf is not None and len(qdf) > 1:
            price = float(qdf['Close'].iloc[-1])
            pct = ((price - float(qdf['Close'].iloc[0])) / float(qdf['Close'].iloc[0])) * 100
            temp_analysis = calculate_professional_signal(qdf, ticker)
            if temp_analysis:
                sig = temp_analysis['signal']
        st.markdown(
            f"<div class='symbol-card'><strong>{name}</strong><br><span class='metric-value'>{price:,.2f}</span> "
            f"<span style='color:{'#00c853' if pct >= 0 else '#f44336'};'> {pct:+.2f}%</span><br>"
            f"<span class='signal-badge signal-{sig.lower()}'>{sig}</span></div>",
            unsafe_allow_html=True
        )
        if st.button(f"Analyze", key=f"btn_{idx}"):
            st.session_state.selected_symbol = ticker
            st.session_state.selected_name = name
            st.rerun()

if st.session_state.get("selected_symbol"):
    ticker = st.session_state.selected_symbol
    name = st.session_state.get("selected_name", ticker)
    st.divider()
    st.subheader(f"📊 {name} ({ticker})")
    
    tf_lower = st.selectbox("Timeframe", ["5m", "15m", "30m", "1h"], index=1)
    
    df = fetch_ohlcv(ticker, interval=tf_lower, period="3d")
    
    if df is None or len(df) < 30:
        st.error("Data nahi aa raha. Kuch der baad refresh karein.")
        st.stop()
    
    analysis = calculate_professional_signal(df, ticker)
    
    if analysis:
        # ==================== KPI CARDS ====================
        st.markdown("### 📊 Key Metrics")
        kpi_cols = st.columns(5)
        with kpi_cols[0]:
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">💰</span>
                <span class="kpi-value">{analysis['price']:,.2f}</span>
                <div class="kpi-label">PRICE</div>
            </div>
            """, unsafe_allow_html=True)
        with kpi_cols[1]:
            badge_color = "#00c853" if analysis['signal'] == "BUY" else "#f44336" if analysis['signal'] == "SELL" else "#ff9800"
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">📊</span>
                <span class="kpi-value" style="color:{badge_color}">{analysis['signal']}</span>
                <div class="kpi-label">SIGNAL</div>
            </div>
            """, unsafe_allow_html=True)
        with kpi_cols[2]:
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">📈</span>
                <span class="kpi-value">{analysis['structure']}</span>
                <div class="kpi-label">STRUCTURE</div>
            </div>
            """, unsafe_allow_html=True)
        with kpi_cols[3]:
            atr_val = f"{analysis['atr']:.2f}" if analysis['atr'] else "N/A"
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">⚡</span>
                <span class="kpi-value">{atr_val}</span>
                <div class="kpi-label">ATR</div>
            </div>
            """, unsafe_allow_html=True)
        with kpi_cols[4]:
            winrate, total = get_stats(ticker) if db_initialized else (None, 0)
            wr = f"{winrate}%" if winrate is not None else "N/A"
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">🏆</span>
                <span class="kpi-value">{wr}</span>
                <div class="kpi-label">WIN RATE</div>
            </div>
            """, unsafe_allow_html=True)
        
        # ==================== SESSION & REGIME ====================
        st.markdown("### 🌐 Market Context")
        st.markdown(f"""
        <div class="regime-box">
            <b>Session:</b> {analysis.get('session_info', 'Unknown')}<br>
            <b>Regime:</b> {analysis.get('regime', 'Unknown')} | {analysis.get('direction', 'Unknown')}<br>
            <b>Details:</b> {analysis.get('regime_info', '')}
        </div>
        """, unsafe_allow_html=True)
        
        # ==================== SIGNAL REASONS ====================
        st.markdown("### 🎯 Signal Analysis")
        for r in analysis['reason']:
            st.write(f"- {r}")
        
        # ==================== MARKET STRUCTURE ====================
        st.markdown("### 📐 Market Structure")
        st.markdown(f"""
        <div class="info-box">
            <b>Structure:</b> {analysis['structure']}<br>
            <b>Details:</b> {analysis['structure_reason']}<br>
            <b>Liquidity Sweep:</b> {analysis['sweep_type'] if analysis['sweep_detected'] else 'None'}
        </div>
        """, unsafe_allow_html=True)
        
        # ==================== INSTITUTIONAL LEVELS ====================
        st.markdown("### 📊 Institutional Levels")
        vwap_text = f"{analysis['vwap']:.2f}" if analysis['vwap'] else "N/A"
        poc_text = f"{analysis['poc']:.2f}" if analysis['poc'] else "N/A"
        vah_text = f"{analysis['vah']:.2f}" if analysis['vah'] else "N/A"
        val_text = f"{analysis['val']:.2f}" if analysis['val'] else "N/A"
        st.markdown(f"""
        <div class="info-box">
            <b>VWAP:</b> {vwap_text} ({analysis['vwap_position']})<br>
            <b>Volume Profile:</b> POC: {poc_text} | VAH: {vah_text} | VAL: {val_text}
        </div>
        """, unsafe_allow_html=True)
        
        # ==================== RISK MANAGEMENT ====================
        if analysis['signal'] in ["BUY", "SELL"]:
            st.markdown("### 🛡️ Risk Management")
            risk_points = abs(analysis['price'] - analysis['stop_loss']) if analysis['stop_loss'] else 0
            reward_points = abs(analysis['take_profit'] - analysis['price']) if analysis['take_profit'] else 0
            rr_ratio = reward_points / risk_points if risk_points > 0 else 0
            st.info(
                f"- **Entry:** {analysis['price']:.2f}\n"
                f"- **Stop Loss:** {analysis['stop_loss']:.2f} ({risk_points:.2f} points risk)\n"
                f"- **Take Profit:** {analysis['take_profit']:.2f} ({reward_points:.2f} points reward)\n"
                f"- **Risk:Reward:** 1 : {rr_ratio:.2f}"
            )
        else:
            st.info("⏳ No active trade. Waiting for structure + liquidity setup.")
        
        # ==================== BACKTEST ====================
        st.markdown("### 📜 Backtest Performance")
        if db_initialized:
            winrate, total = get_stats(ticker)
            if winrate is not None:
                st.progress(winrate / 100, text=f"Win Rate (Last {total} signals): {winrate}%")
                if winrate >= 60:
                    st.success("✅ System consistent perform kar raha hai!")
                else:
                    st.warning("⚠️ System ko optimize karne ki zaroorat hai.")
            else:
                st.info("📭 Abhi koi closed signal nahi.")
        else:
            st.warning("⚠️ Database connected nahi hai.")
        st.caption("💾 Data Neon PostgreSQL Mein Store Ho Raha Hai (Permanent)")
        
        # ==================== TECHNICAL BREAKDOWN ====================
        with st.expander("🧠 Technical Breakdown"):
            st.write(f"**Market Structure:** {analysis['structure']} - {analysis['structure_reason']}")
            st.write(f"**Liquidity Sweep:** {'✅ ' + analysis['sweep_type'] if analysis['sweep_detected'] else '❌ None'}")
            st.write(f"**VWAP:** {analysis['vwap_position']}")
            st.write(f"**Trend Regime:** {analysis['regime']} ({analysis['direction']})")
            st.write(f"**Regime Details:** {analysis['regime_info']}")
            st.write(f"**Session:** {analysis['session_info']}")
            if analysis['poc']:
                st.write(f"**Volume Profile POC:** {analysis['poc']:.2f}")
            if analysis['vah']:
                st.write(f"**VAH:** {analysis['vah']:.2f} | **VAL:** {analysis['val']:.2f}")
            st.write(f"**ATR:** {analysis['atr']:.2f}")
    else:
        st.error("Insufficient data for analysis. Try larger timeframe.")
else:
    st.info("👈 Left side se koi bhi symbol click karein detailed analysis ke liye.")

st.caption("⚡ Professional System v9.0 | Structure + Liquidity + VWAP + Volume Profile + Session + Regime | Alerts Paused")
