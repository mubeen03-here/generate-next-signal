import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from groq import Groq
import google.generativeai as genai
from PIL import Image
from sqlalchemy import text
import time
import requests
import smtplib
from email.message import EmailMessage
from alpha_vantage.timeseries import TimeSeries

# ==================== API KEYS SETUP ====================
if "GROQ_API_KEY" in st.secrets:
    groq_client = Groq(api_key=st.secrets["GROQ_API_KEY"])
else:
    st.error("GROQ_API_KEY missing in Secrets!")
    st.stop()

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.warning("⚠️ GEMINI_API_KEY missing (Image analysis will be disabled)")

st.set_page_config(page_title="Pro Max Trading Signals", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: #fafafa; }
    .main-header { font-size: 2.5rem; font-weight: 700; background: linear-gradient(90deg, #00ff9f, #00b8ff);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .symbol-card { background-color: #161b22; border: 1px solid #30363d; border-radius: 14px; padding: 1rem; }
    .signal-badge { padding: 0.3rem 0.9rem; border-radius: 20px; font-weight: 700; display: inline-block; }
    .strong-buy { background-color: #00c853; color: white; }
    .buy { background-color: #4caf50; color: white; }
    .neutral { background-color: #ff9800; color: white; }
    .sell { background-color: #f44336; color: white; }
    .strong-sell { background-color: #d32f2f; color: white; }
    .metric-value { font-size: 1.7rem; font-weight: 700; }
    .mtf-box { background-color: #1a2332; border-left: 5px solid #00b8ff; padding: 10px; border-radius: 8px; }
    .sr-box { background-color: #2a1a2e; border-left: 5px solid #ff9800; padding: 10px; border-radius: 8px; }
    .backtest-box { background-color: #1e2a2a; border: 1px solid #4caf50; padding: 10px; border-radius: 8px; }
    .candle-status { background-color: #1a1a2e; border-left: 5px solid #ffaa00; padding: 8px 15px; border-radius: 8px; display: inline-block; }
    .alert-toggle-on { background-color: #00c853; color: white; padding: 5px 15px; border-radius: 20px; }
    .alert-toggle-off { background-color: #f44336; color: white; padding: 5px 15px; border-radius: 20px; }
</style>
""", unsafe_allow_html=True)

# ==================== SESSION STATE ====================
if "signal_history" not in st.session_state:
    st.session_state.signal_history = []
if "last_price_check" not in st.session_state:
    st.session_state.last_price_check = {}
if "data_source" not in st.session_state:
    st.session_state.data_source = "Yahoo Finance"
if "last_alert_time" not in st.session_state:
    st.session_state.last_alert_time = {}
if "cooldown_settings" not in st.session_state:
    st.session_state.cooldown_settings = {
        "5m": 5, "15m": 10, "30m": 15, "1h": 30, "4h": 60
    }
# ==================== ALERT TOGGLE (ON/OFF) ====================
if "alerts_enabled" not in st.session_state:
    st.session_state.alerts_enabled = True
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = None
if "selected_name" not in st.session_state:
    st.session_state.selected_name = None

def get_pakistan_time():
    tz = pytz.timezone('Asia/Karachi')
    return datetime.now(tz).strftime("%d %b %Y | %I:%M:%S %p PKT")

# ==================== COOLDOWN CHECK (ONLY FOR ALERTS) ====================
def check_alert_cooldown(ticker, tf):
    """Check if cooldown period has passed for alerts (Telegram/Email) only"""
    cooldown_minutes = st.session_state.cooldown_settings.get(tf, 10)
    current_time = time.time()
    
    if ticker in st.session_state.last_alert_time:
        time_diff = (current_time - st.session_state.last_alert_time[ticker]) / 60
        if time_diff < cooldown_minutes:
            remaining = int(cooldown_minutes - time_diff)
            return False, remaining
    return True, 0

def update_alert_cooldown(ticker):
    st.session_state.last_alert_time[ticker] = time.time()

# ==================== TELEGRAM ALERT ====================
def send_telegram_alert(symbol, signal, price, tp, sl):
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        message = f"""
🚨 *TRADING SIGNAL* 🚨
📊 Symbol: {symbol}
📈 Signal: {signal}
💰 Price: {price:.2f}
🎯 Target: {tp:.2f}
🛑 Stop Loss: {sl:.2f}
⏰ Time: {datetime.now().strftime('%I:%M %p PKT')}
        """
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
        return True
    except Exception as e:
        st.warning(f"Telegram alert fail: {str(e)}")
        return False

# ==================== EMAIL ALERT ====================
def send_email_alert(symbol, signal, price, tp, sl):
    try:
        msg = EmailMessage()
        msg.set_content(f"""
Trading Signal Alert!

Symbol: {symbol}
Signal: {signal}
Entry Price: {price:.2f}
Target: {tp:.2f}
Stop Loss: {sl:.2f}
Time: {datetime.now().strftime('%d %b %Y %I:%M %p PKT')}

---
This is an automated alert from your Trading App.
        """)
        msg['Subject'] = f"🚨 {signal} Signal on {symbol}!"
        msg['From'] = st.secrets["EMAIL_SENDER"]
        msg['To'] = st.secrets["EMAIL_RECEIVER"]
        
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(st.secrets["EMAIL_SENDER"], st.secrets["EMAIL_PASSWORD"])
            smtp.send_message(msg)
        return True
    except Exception as e:
        st.warning(f"Email alert fail: {str(e)}")
        return False

# ==================== NEON DATABASE (WITH RETRY) ====================
def get_conn():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return st.connection("neon", type="sql")
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                st.error(f"❌ Database connection failed after {max_retries} attempts: {str(e)}")
                return None

def init_db():
    conn = get_conn()
    if conn is None:
        return False
    max_retries = 3
    for attempt in range(max_retries):
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
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                st.error(f"❌ Failed to create table after {max_retries} attempts: {str(e)}")
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
    except Exception as e:
        st.error(f"❌ Failed to save signal: {str(e)}")

# ==================== AUTO BACKTEST (HIGH/LOW CHECK) ====================
def update_old_signals(symbol, df):
    """Auto backtest - High/Low check with timezone fix"""
    conn = get_conn()
    if conn is None:
        return
    
    conn2 = get_conn()
    if conn2 is None:
        return
    try:
        with conn2.session as s:
            rows = s.execute(text("""
                SELECT id, timestamp, signal, target_price, stop_loss 
                FROM signal_history 
                WHERE symbol = :sym AND status = 'PENDING'
            """), {"sym": symbol}).fetchall()
            
            if not rows:
                return
            
            df['Datetime'] = pd.to_datetime(df['Datetime']).dt.tz_localize(None)
            
            for row in rows:
                signal_id, signal_time, sig, target, sl = row
                signal_dt = pd.to_datetime(signal_time)
                
                next_candles = df[df['Datetime'] > signal_dt]
                if next_candles.empty:
                    continue
                
                next_candle = next_candles.iloc[0]
                candle_high = float(next_candle['High'])
                candle_low = float(next_candle['Low'])
                
                result = None
                if "BUY" in sig:
                    if candle_high >= target:
                        result = "WIN"
                    elif candle_low <= sl:
                        result = "LOSS"
                elif "SELL" in sig:
                    if candle_low <= target:
                        result = "WIN"
                    elif candle_high >= sl:
                        result = "LOSS"
                
                if result:
                    with conn.session as s2:
                        s2.execute(text("""
                            UPDATE signal_history 
                            SET status='CLOSED', result=:res 
                            WHERE id=:id
                        """), {"res": result, "id": signal_id})
                        s2.commit()
    except Exception as e:
        st.error(f"❌ Failed to update signals: {str(e)}")

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
    except Exception as e:
        st.error(f"❌ Failed to get stats: {str(e)}")
        return None, 0

# ==================== IMPROVED S/R DETECTION (MULTIPLE TOUCHES) ====================
def find_strong_sr_levels(df, lookback=50, touch_threshold=2):
    if df is None or len(df) < lookback:
        return None, None
    
    recent = df.iloc[-lookback:]
    resistance = recent['High'].max()
    support = recent['Low'].min()
    
    touch_count_res = 0
    touch_count_sup = 0
    tolerance = (resistance - support) * 0.002
    
    for i in range(len(recent) - 1):
        high = recent.iloc[i]['High']
        low = recent.iloc[i]['Low']
        if abs(high - resistance) <= tolerance:
            touch_count_res += 1
        if abs(low - support) <= tolerance:
            touch_count_sup += 1
    
    if touch_count_res >= touch_threshold:
        resistance = resistance
    else:
        resistance = None
    
    if touch_count_sup >= touch_threshold:
        support = support
    else:
        support = None
    
    return support, resistance

# ==================== CANDLE STATUS (PRESENT/NEXT) ====================
def get_candle_status(df):
    if df is None or len(df) < 2:
        return "Unknown", "Unknown", "Unknown", "Unknown"
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    if last['Close'] > last['Open']:
        present_color = "🟢 Green (Bullish)"
        present_emoji = "🟢"
    else:
        present_color = "🔴 Red (Bearish)"
        present_emoji = "🔴"
    
    if last['Close'] > prev['Close']:
        next_prediction = "🟢 Bullish expected" if last['Close'] > last['Open'] else "🔄 Reversal possible"
        next_emoji = "🟢" if last['Close'] > last['Open'] else "🔄"
    else:
        next_prediction = "🔴 Bearish expected" if last['Close'] < last['Open'] else "🔄 Reversal possible"
        next_emoji = "🔴" if last['Close'] < last['Open'] else "🔄"
    
    return present_color, present_emoji, next_prediction, next_emoji

# ==================== FAILOVER DATA FETCH ====================
@st.cache_data(ttl=40, show_spinner=False)
def fetch_ohlcv(ticker, interval="15m", period="5d"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is not None and not df.empty:
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
            st.session_state.data_source = "Yahoo Finance"
            return df[['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    except:
        pass

    try:
        if "ALPHA_VANTAGE_KEY" not in st.secrets:
            return None
            
        ts = TimeSeries(key=st.secrets["ALPHA_VANTAGE_KEY"])
        av_interval = "15min"
        if interval == "5m":
            av_interval = "5min"
        elif interval == "15m":
            av_interval = "15min"
        elif interval == "30m":
            av_interval = "30min"
        elif interval == "60m" or interval == "1h":
            av_interval = "60min"
        
        av_symbol = ticker.replace("-", "").replace("=X", "")
        data, meta = ts.get_intraday(symbol=av_symbol, interval=av_interval, outputsize='compact')
        df = pd.DataFrame.from_dict(data, orient='index')
        df = df.rename(columns={
            '1. open': 'Open',
            '2. high': 'High',
            '3. low': 'Low',
            '4. close': 'Close',
            '5. volume': 'Volume'
        })
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
        df = df.reset_index().rename(columns={'index': 'Datetime'})
        st.session_state.data_source = "Alpha Vantage (Backup)"
        return df
    except Exception as e:
        st.session_state.data_source = "ERROR"
        return None

# ==================== CANDLE PATTERNS ====================
def detect_candle_patterns(df):
    if df is None or len(df) < 2:
        return []
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last['Close'] - last['Open'])
    upper_wick = last['High'] - max(last['Close'], last['Open'])
    lower_wick = min(last['Close'], last['Open']) - last['Low']
    total_range = last['High'] - last['Low']
    patterns = []
    if total_range > 0 and body <= total_range * 0.1:
        patterns.append("⚪ Doji (Neutral / Reversal possible)")
    if (last['Close'] > last['Open'] and prev['Close'] < prev['Open'] and
        last['Close'] > prev['Open'] and last['Open'] < prev['Close']):
        patterns.append("🟢 Bullish Engulfing (Strong Buy)")
    if (last['Close'] < last['Open'] and prev['Close'] > prev['Open'] and
        last['Close'] < prev['Open'] and last['Open'] > prev['Close']):
        patterns.append("🔴 Bearish Engulfing (Strong Sell)")
    if (last['Close'] > last['Open'] and lower_wick > body * 2 and upper_wick < body * 0.5):
        patterns.append("🔨 Hammer (Bullish Reversal)")
    if (last['Close'] < last['Open'] and upper_wick > body * 2 and lower_wick < body * 0.5):
        patterns.append("🌠 Shooting Star (Bearish Reversal)")
    if total_range > 0 and upper_wick < total_range * 0.05 and lower_wick < total_range * 0.05:
        patterns.append("🔥 Bullish Marubozu (Strong)" if last['Close'] > last['Open'] else "💧 Bearish Marubozu (Strong)")
    return patterns

# ==================== SIGNAL ENGINE ====================
def calculate_advanced_signal(df, df_higher=None):
    if df is None or len(df) < 40:
        return None
    df = df.copy()
    close = df['Close'].astype(float)
    high = df['High'].astype(float)
    low = df['Low'].astype(float)
    volume = df['Volume'].astype(float)

    df['EMA_9'] = close.ewm(span=9, adjust=False).mean()
    df['EMA_21'] = close.ewm(span=21, adjust=False).mean()
    df['EMA_50'] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(50)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['MACD_Hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean()
    df['Volume_MA'] = volume.rolling(window=20).mean()
    df = df.dropna()
    if len(df) < 15:
        return None

    last = df.iloc[-1]
    price = float(last['Close'])
    
    score = 0
    reasons = []
    signal_details = {}
    mtf_bias = 0

    if df_higher is not None and len(df_higher) > 20:
        h_close = df_higher['Close']
        h_ema50 = h_close.ewm(span=50, adjust=False).mean().iloc[-1]
        h_price = float(h_close.iloc[-1])
        if h_price > h_ema50:
            mtf_bias = 2
            reasons.append("✅ 1-Hour Trend: BULLISH (Price > EMA50)")
        else:
            mtf_bias = -2
            reasons.append("❌ 1-Hour Trend: BEARISH (Price < EMA50)")
        score += mtf_bias
    else:
        reasons.append("⚠️ Higher TF data missing, using only lower TF")

    if price > last['EMA_9'] > last['EMA_21']:
        score += 2
        reasons.append("✅ EMA Structure: Bullish (9>21)")
    elif price < last['EMA_9'] < last['EMA_21']:
        score -= 2
        reasons.append("❌ EMA Structure: Bearish (9<21)")
    else:
        reasons.append("➖ EMA Structure: Neutral")

    rsi = float(last['RSI'])
    if rsi > 70:
        score -= 2
        reasons.append(f"🟥 RSI Overbought ({rsi:.1f})")
    elif rsi < 30:
        score += 2
        reasons.append(f"🟩 RSI Oversold ({rsi:.1f})")
    elif rsi > 55:
        score += 1
        reasons.append(f"✅ RSI Bullish ({rsi:.1f})")
    elif rsi < 45:
        score -= 1
        reasons.append(f"❌ RSI Bearish ({rsi:.1f})")
    else:
        reasons.append(f"➖ RSI Neutral ({rsi:.1f})")

    if last['MACD_Hist'] > 0:
        score += 1.5
        reasons.append("✅ MACD Positive Histogram")
    else:
        score -= 1.5
        reasons.append("❌ MACD Negative Histogram")

    vol_ma = float(last['Volume_MA'])
    vol_now = float(last['Volume'])
    if vol_ma > 
