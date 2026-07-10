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

if "FINNHUB_API_KEY" not in st.secrets:
    st.warning("⚠️ FINNHUB_API_KEY missing. News feature will be disabled.")

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
    .smc-box { background-color: #1a1a3e; border-left: 5px solid #8866ff; padding: 10px; border-radius: 8px; margin: 5px 0; }
    .news-box { background-color: #1a2a1a; border-left: 5px solid #00ff88; padding: 10px; border-radius: 8px; margin: 5px 0; }
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
if "alerts_enabled" not in st.session_state:
    st.session_state.alerts_enabled = True
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = None
if "selected_name" not in st.session_state:
    st.session_state.selected_name = None

def get_pakistan_time():
    tz = pytz.timezone('Asia/Karachi')
    return datetime.now(tz).strftime("%d %b %Y | %I:%M:%S %p PKT")

# ==================== COOLDOWN CHECK ====================
def check_alert_cooldown(ticker, tf):
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

# ==================== NEON DATABASE ====================
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

# ==================== AUTO BACKTEST ====================
def update_old_signals(symbol, df):
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

# ==================== SMC FUNCTIONS ====================
def detect_market_structure(df, lookback=10):
    if df is None or len(df) < lookback + 2:
        return 0, "Neutral"
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    recent_high = df['High'].iloc[-lookback:].max()
    recent_low = df['Low'].iloc[-lookback:].min()
    
    bos_bullish = last['High'] > recent_high and prev['High'] <= recent_high
    bos_bearish = last['Low'] < recent_low and prev['Low'] >= recent_low
    
    choch_bullish = last['Low'] > recent_low and last['High'] < recent_high and prev['Low'] <= recent_low
    choch_bearish = last['High'] < recent_high and last['Low'] > recent_low and prev['High'] >= recent_high
    
    if bos_bullish:
        return 2, "BOS Bullish"
    elif bos_bearish:
        return -2, "BOS Bearish"
    elif choch_bullish:
        return 1, "CHoCH Bullish"
    elif choch_bearish:
        return -1, "CHoCH Bearish"
    
    return 0, "Neutral"

def detect_order_blocks(df, lookback=5):
    if df is None or len(df) < lookback + 2:
        return None, None, "No OB"
    
    prev_candles = df.iloc[-lookback-1:-1]
    
    for i in range(len(prev_candles) - 1):
        c1 = prev_candles.iloc[i]
        c2 = prev_candles.iloc[i + 1]
        
        if c1['Close'] < c1['Open'] and c2['Close'] > c2['Open'] and c2['Close'] > c1['High']:
            return c1['Low'], c1['High'], "Bullish OB"
        
        if c1['Close'] > c1['Open'] and c2['Close'] < c2['Open'] and c2['Close'] < c1['Low']:
            return c1['Low'], c1['High'], "Bearish OB"
    
    return None, None, "No OB"

def detect_equal_highs_lows(df, lookback=30, threshold=0.001):
    if df is None or len(df) < lookback:
        return None, None
    
    recent = df.iloc[-lookback:]
    price = float(df['Close'].iloc[-1])
    
    highs = recent['High'].tolist()
    lows = recent['Low'].tolist()
    
    eqh_level = None
    for i in range(len(highs) - 1):
        for j in range(i + 1, len(highs)):
            if abs(highs[i] - highs[j]) / price < threshold:
                eqh_level = (highs[i] + highs[j]) / 2
                break
        if eqh_level:
            break
    
    eql_level = None
    for i in range(len(lows) - 1):
        for j in range(i + 1, len(lows)):
            if abs(lows[i] - lows[j]) / price < threshold:
                eql_level = (lows[i] + lows[j]) / 2
                break
        if eql_level:
            break
    
    return eqh_level, eql_level

def detect_fvg(df):
    if df is None or len(df) < 3:
        return None, None, "No FVG"
    
    c1 = df.iloc[-3]
    c2 = df.iloc[-2]
    
    if c2['Low'] > c1['High']:
        return c1['High'], c2['Low'], "Bullish FVG"
    
    if c2['High'] < c1['Low']:
        return c2['High'], c1['Low'], "Bearish FVG"
    
    return None, None, "No FVG"

def detect_premium_discount(df, lookback=50):
    if df is None or len(df) < lookback:
        return "Neutral", 0
    
    recent = df.iloc[-lookback:]
    high = recent['High'].max()
    low = recent['Low'].min()
    price = float(df['Close'].iloc[-1])
    
    range_val = high - low
    if range_val == 0:
        return "Neutral", 0
    
    position = (price - low) / range_val
    
    if position > 0.7:
        return "Premium Zone (SELL)", -2
    elif position < 0.3:
        return "Discount Zone (BUY)", 2
    else:
        return "Equilibrium Zone", 0

# ==================== NEWS SENTIMENT FUNCTION ====================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_news_sentiment(symbol):
    """
    Finnhub se news le kar Grok ke zariye sentiment nikaalna
    """
    if "FINNHUB_API_KEY" not in st.secrets:
        return "Neutral", ["API Key Missing"], "No News"
    
    try:
        # Symbol mapping for Finnhub
        if "BTC" in symbol:
            finnhub_symbol = "BINANCE:BTCUSDT"
        elif "NAS" in symbol or "NQ" in symbol:
            finnhub_symbol = "US100"
        elif "USDJPY" in symbol:
            finnhub_symbol = "FX_IDC:USDJPY"
        else:
            finnhub_symbol = symbol
        
        url = f"https://finnhub.io/api/v1/news?symbol={finnhub_symbol}&token={st.secrets['FINNHUB_API_KEY']}"
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            return "Neutral", ["No News"], "No News"
        
        news_data = response.json()
        if not news_data:
            return "Neutral", ["No News"], "No News"
        
        # Top 5 headlines
        headlines = [item['headline'] for item in news_data[:5]]
        headlines_text = "\n".join(headlines)
        
        # Groq se sentiment poochho
        prompt = f"""
        In financial news headlines ko dekho aur batao ke inka overall sentiment kya hai: Bullish, Bearish, ya Neutral?
        Sirf ek word jawab do (Bullish/Bearish/Neutral) aur ek choti reason.
        Headlines:
        {headlines_text}
        """
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=50
        )
        result = response.choices[0].message.content.strip()
        
        if "Bullish" in result:
            return "Bullish", headlines, headlines_text
        elif "Bearish" in result:
            return "Bearish", headlines, headlines_text
        else:
            return "Neutral", headlines, headlines_text
            
    except Exception as e:
        return "Neutral", [f"Error: {str(e)}"], "No News"

# ==================== CANDLE STATUS ====================
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

# ==================== SIGNAL ENGINE (WITH SMC + NEWS) ====================
def calculate_advanced_signal(df, df_higher=None, symbol=""):
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

    # ==================== SMC LOGIC ====================
    structure_score, structure_signal = detect_market_structure(df, lookback=10)
    score += structure_score
    reasons.append(f"🔹 SMC Structure: {structure_signal}")

    ob_low, ob_high, ob_signal = detect_order_blocks(df, lookback=5)
    if ob_signal != "No OB":
        signal_details['order_block_high'] = ob_high
        signal_details['order_block_low'] = ob_low
        signal_details['order_block_signal'] = ob_signal
        if "Bullish" in ob_signal:
            score += 2
            reasons.append(f"🔹 {ob_signal}")
        elif "Bearish" in ob_signal:
            score -= 2
            reasons.append(f"🔹 {ob_signal}")

    eqh, eql = detect_equal_highs_lows(df, lookback=30, threshold=0.001)
    if eqh:
        signal_details['eqh'] = eqh
        if price > eqh * 0.998 and price < eqh * 1.002:
            score -= 1
            reasons.append(f"🔹 Near Equal High (Liquidity) @ {eqh:.2f}")
    if eql:
        signal_details['eql'] = eql
        if price > eql * 0.998 and price < eql * 1.002:
            score += 1
            reasons.append(f"🔹 Near Equal Low (Liquidity) @ {eql:.2f}")

    fvg_top, fvg_bottom, fvg_signal = detect_fvg(df)
    if fvg_signal != "No FVG":
        signal_details['fvg_top'] = fvg_top
        signal_details['fvg_bottom'] = fvg_bottom
        signal_details['fvg_signal'] = fvg_signal
        if "Bullish" in fvg_signal:
            score += 1
            reasons.append(f"🔹 {fvg_signal}")
        elif "Bearish" in fvg_signal:
            score -= 1
            reasons.append(f"🔹 {fvg_signal}")

    zone_label, zone_score = detect_premium_discount(df, lookback=50)
    score += zone_score
    reasons.append(f"🔹 Zone: {zone_label}")

    # ==================== NEWS SENTIMENT SCORE ====================
    if symbol:
        news_sentiment, news_headlines, news_text = fetch_news_sentiment(symbol)
        signal_details['news_sentiment'] = news_sentiment
        signal_details['news_headlines'] = news_headlines
        signal_details['news_text'] = news_text
        
        if news_sentiment == "Bullish":
            score += 1
            reasons.append(f"📰 News Sentiment: BULLISH (+1)")
        elif news_sentiment == "Bearish":
            score -= 1
            reasons.append(f"📰 News Sentiment: BEARISH (-1)")
        else:
            reasons.append(f"📰 News Sentiment: Neutral")
    else:
        signal_details['news_sentiment'] = 'Neutral'
        signal_details['news_headlines'] = ['No Symbol']
        signal_details['news_text'] = 'No Symbol'

    # ==================== MTF TREND ====================
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

    # ==================== EMA STRUCTURE ====================
    if price > last['EMA_9'] > last['EMA_21']:
        score += 2
        reasons.append("✅ EMA Structure: Bullish (9>21)")
    elif price < last['EMA_9'] < last['EMA_21']:
        score -= 2
        reasons.append("❌ EMA Structure: Bearish (9<21)")
    else:
        reasons.append("➖ EMA Structure: Neutral")

    # ==================== RSI ====================
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

    # ==================== MACD ====================
    if last['MACD_Hist'] > 0:
        score += 1.5
        reasons.append("✅ MACD Positive Histogram")
    else:
        score -= 1.5
        reasons.append("❌ MACD Negative Histogram")

    # ==================== VOLUME SPIKE ====================
    vol_ma = float(last['Volume_MA'])
    vol_now = float(last['Volume'])
    if vol_ma > 0 and vol_now > vol_ma * 1.5:
        if score > 0:
            score += 1
            reasons.append(f"📈 Volume Spike ({vol_now/vol_ma:.1f}x Avg)")
        elif score < 0:
            score -= 1
            reasons.append(f"📉 Volume Spike ({vol_now/vol_ma:.1f}x Avg)")
        else:
            reasons.append(f"📊 Volume Spike ({vol_now/vol_ma:.1f}x Avg)")
    else:
        reasons.append("➖ Volume normal")

    # ==================== CANDLE PATTERNS ====================
    patterns = detect_candle_patterns(df)
    if patterns:
        for p in patterns:
            if "Bullish" in p or "Buy" in p:
                score += 1.5
                reasons.append(f"📊 {p}")
            elif "Bearish" in p or "Sell" in p:
                score -= 1.5
                reasons.append(f"📊 {p}")
            else:
                reasons.append(f"📊 {p}")
    else:
        reasons.append("➖ No strong pattern detected")

    # ==================== FINAL SCORE ====================
    atr = float(last['ATR'])
    # Scalping Mode (1:1 Ratio)
    if atr / price < 0.005:
        threshold_buy, threshold_sell = 3.5, -3.5
    else:
        threshold_buy, threshold_sell = 4.5, -4.5

    if score >= threshold_buy:
        signal, badge = "STRONG BUY", "strong-buy"
    elif score >= threshold_buy - 1:
        signal, badge = "BUY", "buy"
    elif score <= threshold_sell:
        signal, badge = "STRONG SELL", "strong-sell"
    elif score <= threshold_sell + 1:
        signal, badge = "SELL", "sell"
    else:
        signal, badge = "WAIT", "neutral"

    # SL/TP (2x ATR each for 1:1)
    sl_price = price - (2.0 * atr) if "BUY" in signal else price + (2.0 * atr)
    tp_price = price + (2.0 * atr) if "BUY" in signal else price - (2.0 * atr)
    rr_ratio = round(2.0 / 2.0, 2)

    if "BUY" in signal:
        expected = f"🟢 NEXT CANDLE LIKELY BULLISH (Green). Target: {tp_price:.2f}"
        pullback = f"📉 Small retrace to {price - (0.5*atr):.2f} possible before up move."
    elif "SELL" in signal:
        expected = f"🔴 NEXT CANDLE LIKELY BEARISH (Red). Target: {tp_price:.2f}"
        pullback = f"📈 Small bounce to {price + (0.5*atr):.2f} possible before down move."
    else:
        expected = "⏳ Direction unclear. Better to WAIT for break of S/R."
        pullback = "No active trade."
        sl_price = price
        tp_price = price

    return {
        "signal": signal, "badge_class": badge, "score": round(score, 2),
        "reasons": reasons, "last_price": round(price, 2), "rsi": round(rsi, 1),
        "atr": round(atr, 3), "sl": round(sl_price, 2), "tp": round(tp_price, 2),
        "rr_ratio": rr_ratio, "expected_candles": expected, "pullback": pullback,
        "patterns": patterns, "mtf_bias": "Bullish" if mtf_bias > 0 else "Bearish" if mtf_bias < 0 else "Neutral",
        "eqh": signal_details.get('eqh', None), "eql": signal_details.get('eql', None),
        "fvg_top": signal_details.get('fvg_top', None), "fvg_bottom": signal_details.get('fvg_bottom', None),
        "fvg_signal": signal_details.get('fvg_signal', None),
        "order_block_high": signal_details.get('order_block_high', None),
        "order_block_low": signal_details.get('order_block_low', None),
        "order_block_signal": signal_details.get('order_block_signal', None),
        "news_sentiment": signal_details.get('news_sentiment', 'Neutral'),
        "news_headlines": signal_details.get('news_headlines', ['No News']),
        "news_text": signal_details.get('news_text', 'No News')
    }

# ==================== GROK & GEMINI ====================
def get_grok_analysis(symbol, tf, analysis, price):
    prompt = f"Symbol: {symbol} | TF: {tf} | Price: {price}\nSignal: {analysis['signal']} | Score: {analysis['score']}\nReasons: {', '.join(analysis['reasons'])}\nPatterns: {', '.join(analysis['patterns']) if analysis['patterns'] else 'None'}\nMTF Bias: {analysis['mtf_bias']}\nSL: {analysis['sl']} | TP: {analysis['tp']}\nNews Sentiment: {analysis.get('news_sentiment', 'Neutral')}\n\nAs a pro trader, give short, direct advice on this trade:\n1. Is this signal reliable? Why?\n2. What is the probability of next candle going as expected?\n3. Should we enter now or wait? (give specific price action triggers)\nMax 6 lines."
    try:
        response = groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}], temperature=0.4, max_tokens=250)
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Grok unavailable: {str(e)}"

def analyze_chart_with_gemini(image, symbol, tf):
    if "GEMINI_API_KEY" not in st.secrets:
        return "Gemini API key missing."
    prompt = f"Analyze this chart for {symbol} on {tf} time frame. Tell me visually:\n1. Current forming candle direction?\n2. Next candle prediction (Bullish/Bearish) with % probability.\n3. Are we near Support or Resistance?\n4. Should we BUY, SELL, or WAIT?\nAnswer in 6 short bullet points."
    models = ['gemini-1.5-flash', 'gemini-2.0-flash', 'gemini-2.5-flash']
    for m in models:
        try:
            model = genai.GenerativeModel(m)
            res = model.generate_content([prompt, image])
            if res and res.text:
                return res.text
        except:
            continue
    return "Gemini analysis failed."

# ==================== UI ====================
st.markdown('<h1 class="main-header">🚀 Pro Max Trading Signals</h1>', unsafe_allow_html=True)
st.caption(f"🇵🇰 {get_pakistan_time()} | SMC + News Sentiment + MTF + Volume + Patterns | Neon DB")

col1, col2, col3 = st.columns([1, 1, 2])
with col1:
    if st.session_state.alerts_enabled:
        if st.button("🔔 Alerts: ON", key="alert_toggle", help="Click to turn OFF alerts (Telegram/Email)"):
            st.session_state.alerts_enabled = False
            st.rerun()
    else:
        if st.button("🔕 Alerts: OFF", key="alert_toggle", help="Click to turn ON alerts (Telegram/Email)"):
            st.session_state.alerts_enabled = True
            st.rerun()

with col2:
    if st.button("🔄 Refresh Data & Backtest"):
        st.cache_data.clear()
        st.rerun()

with st.expander("⚙️ Cooldown Settings (Telegram & Email Alerts Only)"):
    st.info("⏳ Cooldown sirf Telegram aur Email alerts ke liye hai. Dashboard signals hamesha generate honge.")
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.session_state.cooldown_settings["5m"] = st.number_input("5m", min_value=1, max_value=30, value=st.session_state.cooldown_settings["5m"])
    with col2:
        st.session_state.cooldown_settings["15m"] = st.number_input("15m", min_value=1, max_value=30, value=st.session_state.cooldown_settings["15m"])
    with col3:
        st.session_state.cooldown_settings["30m"] = st.number_input("30m", min_value=1, max_value=60, value=st.session_state.cooldown_settings["30m"])
    with col4:
        st.session_state.cooldown_settings["1h"] = st.number_input("1h", min_value=1, max_value=120, value=st.session_state.cooldown_settings["1h"])
    with col5:
        st.session_state.cooldown_settings["4h"] = st.number_input("4h", min_value=1, max_value=240, value=st.session_state.cooldown_settings["4h"])

MAIN_SYMBOLS = {"Bitcoin (BTC)": "BTC-USD", "USD/JPY": "USDJPY=X", "NAS100": "NQ=F"}

db_initialized = init_db()
if db_initialized:
    st.success("✅ Database connected successfully!")
else:
    st.warning("⚠️ Database connection failed. Check secrets configuration.")

cols = st.columns(3)
for idx, (name, ticker) in enumerate(MAIN_SYMBOLS.items()):
    with cols[idx]:
        qdf = fetch_ohlcv(ticker, interval="60m", period="2d")
        price, pct, sig = 0.0, 0.0, "NEUTRAL"
        temp_analysis = None
        if qdf is not None and len(qdf) > 1:
            price = float(qdf['Close'].iloc[-1])
            pct = ((price - float(qdf['Close'].iloc[0])) / float(qdf['Close'].iloc[0])) * 100
            temp_analysis = calculate_advanced_signal(qdf, None, ticker)
            if temp_analysis:
                sig = temp_analysis['signal']
        st.markdown(
            f"<div class='symbol-card'><strong>{name}</strong><br><span class='metric-value'>{price:,.2f}</span> "
            f"<span style='color:{'#00c853' if pct >= 0 else '#f44336'};'> {pct:+.2f}%</span><br>"
            f"<span class='signal-badge {temp_analysis['badge_class'] if temp_analysis else 'neutral'}'>{sig}</span></div>",
            unsafe_allow_html=True
        )
        if st.button(f"Analyze {name.split()[0]}", key=f"btn_{idx}"):
            st.session_state.selected_symbol = ticker
            st.session_state.selected_name = name
            st.rerun()

if st.session_state.get("selected_symbol"):
    ticker = st.session_state.selected_symbol
    name = st.session_state.get("selected_name", ticker)
    st.divider()
    st.subheader(f"📊 {name} ({ticker})")
    st.caption(f"📡 Data Source: {st.session_state.data_source}")
    
    tf_lower = st.selectbox("Lower Timeframe (Entry)", ["5m", "15m", "30m"], index=1)
    tf_higher = st.selectbox("Higher Timeframe (Trend)", ["1h", "4h"], index=0)
    
    df_lower = fetch_ohlcv(ticker, interval=tf_lower, period="3d")
    df_higher = fetch_ohlcv(ticker, interval=tf_higher, period="5d")
    
    if df_lower is None or len(df_lower) < 30:
        st.error("Data nahi aa raha. Kuch der baad refresh karein.")
        st.stop()
    
    present_color, present_emoji, next_prediction, next_emoji = get_candle_status(df_lower)
    st.markdown(f"""
    <div class="candle-status">
        <b>🕯️ Present Candle:</b> {present_emoji} {present_color}<br>
        <b>🔮 Next Candle Prediction:</b> {next_emoji} {next_prediction}
    </div>
    """, unsafe_allow_html=True)
    
    analysis = calculate_advanced_signal(df_lower, df_higher, ticker)
    
    if analysis:
        if len(df_lower) > 2:
            st.session_state.last_price_check[ticker] = float(df_lower['Close'].iloc[-1])
        
        if analysis['signal'] in ["BUY", "STRONG BUY", "SELL", "STRONG SELL"] and db_initialized:
            save_signal(
                symbol=ticker,
                signal=analysis['signal'],
                entry=analysis['last_price'],
                target=analysis['tp'],
                sl=analysis['sl']
            )
        
        if db_initialized:
            update_old_signals(ticker, df_lower)
        
        if analysis['signal'] in ["BUY", "STRONG BUY", "SELL", "STRONG SELL"] and db_initialized:
            if st.session_state.alerts_enabled:
                can_alert, remaining = check_alert_cooldown(ticker, tf_lower)
                
                if can_alert:
                    telegram_sent = send_telegram_alert(
                        symbol=name,
                        signal=analysis['signal'],
                        price=analysis['last_price'],
                        tp=analysis['tp'],
                        sl=analysis['sl']
                    )
                    email_sent = send_email_alert(
                        symbol=name,
                        signal=analysis['signal'],
                        price=analysis['last_price'],
                        tp=analysis['tp'],
                        sl=analysis['sl']
                    )
                    if telegram_sent and email_sent:
                        st.success("✅ Alerts sent to Telegram & Email!")
                        update_alert_cooldown(ticker)
                    else:
                        st.warning("⚠️ Alerts partially failed. Check logs.")
                else:
                    st.info(f"⏳ Alerts skipped due to cooldown. Next alerts available after {remaining} minutes. (Signal saved in DB)")
            else:
                st.info("🔕 Alerts are OFF. Signal saved in DB only. Turn ON alerts to receive Telegram/Email.")
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("💰 Price", f"{analysis['last_price']:,}")
        c2.metric("📊 Signal", analysis['signal'])
        c3.metric("📈 RSI", analysis['rsi'])
        c4.metric("⚡ ATR", analysis['atr'])
        
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(
                f"<div class='mtf-box'><b>⏳ Multi-Timeframe Trend (Higher TF):</b> {analysis['mtf_bias']}<br><b>Score:</b> {analysis['score']} / 10</div>",
                unsafe_allow_html=True
            )
        with col_b:
            st.markdown(
                f"<div class='smc-box'><b>🔹 SMC Analysis:</b><br>"
                f"- FVG: {analysis.get('fvg_signal', 'None')}<br>"
                f"- Order Block: {analysis.get('order_block_signal', 'None')}<br>"
                f"- EQH: {analysis.get('eqh', 'None')} | EQL: {analysis.get('eql', 'None')}</div>",
                unsafe_allow_html=True
            )
        
        # ---- NEWS SENTIMENT DISPLAY ----
        st.markdown("### 📰 News Sentiment")
        col1, col2 = st.columns([1, 3])
        with col1:
            sentiment = analysis.get('news_sentiment', 'Neutral')
            if sentiment == "Bullish":
                st.success(f"🟢 {sentiment}")
            elif sentiment == "Bearish":
                st.error(f"🔴 {sentiment}")
            else:
                st.info(f"⚪ {sentiment}")
        with col2:
            headlines = analysis.get('news_headlines', ['No News'])
            # FIX: Check if headlines is a list and not empty
            if isinstance(headlines, list) and headlines and headlines[0] != "No News" and not headlines[0].startswith("Error") and headlines[0] != "No Symbol":
                st.caption(f"📌 {headlines[0][:80]}..." if len(headlines[0]) > 80 else f"📌 {headlines[0]}")
            else:
                st.caption("📌 No recent news")
        
        st.markdown("### 🎯 Risk Management (ATR Based)")
        st.info(
            f"- **Stop Loss (SL):** {analysis['sl']} (2.0x ATR)\n"
            f"- **Take Profit (TP):** {analysis['tp']} (2.0x ATR)\n"
            f"- **Risk:Reward Ratio:** 1 : {analysis['rr_ratio']}"
        )
        
        st.markdown("### 🕯️ Next Candle Prediction")
        st.success(analysis['expected_candles'])
        st.warning(analysis['pullback'])
        
        if analysis['signal'] == "WAIT":
            st.markdown(
                f"<div class='backtest-box'><b>⏳ WAIT - Why?</b><br>"
                f"🔸 Market structure or SMC zones suggest waiting.<br>"
                f"🔸 Watch for break of structure or liquidity sweep.<br>"
                f"🔸 Entry only after clear confirmation.</div>",
                unsafe_allow_html=True
            )
        
        st.markdown("### 📜 Backtest Performance (Recent 10 Signals)")
        if db_initialized:
            winrate, total = get_stats(ticker)
            if winrate is not None:
                st.progress(winrate / 100, text=f"Win Rate (Last {total} signals): {winrate}%")
                if winrate >= 60:
                    st.success("✅ System consistent perform kar raha hai!")
                else:
                    st.warning("⚠️ System ko optimize karne ki zaroorat hai.")
            else:
                st.info("📭 Abhi koi closed signal nahi. Pehle kuch trades complete hone dein.")
        else:
            st.warning("⚠️ Database connected nahi hai. Backtest stats unavailable.")
        st.caption("💾 Data Neon PostgreSQL Mein Store Ho Raha Hai (Permanent)")
        
        with st.expander("🧠 Technical Reasons (Score Breakup)"):
            for r in analysis['reasons']:
                st.write(f"- {r}")
            st.caption(f"Total Score: {analysis['score']}")
        
        st.markdown("### 🤖 Grok Text Analysis")
        if st.button("Ask Grok", key="grok_main"):
            with st.spinner("Grok soch raha hai..."):
                resp = get_grok_analysis(name, tf_lower, analysis, analysis['last_price'])
            st.markdown(
                f"<div class='mtf-box' style='border-left-color: #4a90e2;'>{resp}</div>",
                unsafe_allow_html=True
            )
        
        st.markdown("### 📸 Gemini Vision (Upload Chart Screenshot)")
        st.write("Current candle ka screenshot upload karein taake Gemini next candle predict kare.")
        uploaded = st.file_uploader(f"Upload {name} ({tf_lower}) chart image", type=["png", "jpg"], key="gemini_upload")
        if uploaded:
            img = Image.open(uploaded)
            st.image(img, caption="Uploaded Chart", use_container_width=True)
            if st.button("🔮 Predict Next Candle via Gemini", key="gemini_main"):
                with st.spinner("Gemini analyzing chart..."):
                    gem_res = analyze_chart_with_gemini(img, name, tf_lower)
                st.markdown(
                    f"<div class='sr-box' style='border-left-color: #00ff9f;'>{gem_res}</div>",
                    unsafe_allow_html=True
                )
    else:
        st.error("Insufficient data for analysis. Try larger timeframe.")
else:
    st.info("👈 Left side se koi bhi symbol click karein detailed analysis ke liye.")

st.caption("⚡ Advanced System v6.1 | SMC + News Sentiment + Auto Backtest + Alerts | Neon DB (Permanent)")
