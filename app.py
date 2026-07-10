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

# ==================== PROFESSIONAL UI/CSS ====================
st.markdown("""
<style>
    /* Base Theme */
    .stApp { 
        background-color: #0A0E17; 
        color: #E2E8F0; 
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    
    /* Sleek Header */
    .main-header { 
        font-size: 1.6rem; 
        font-weight: 600; 
        color: #FFFFFF; 
        border-bottom: 2px solid #2563EB;
        padding-bottom: 8px;
        display: inline-block;
        margin-bottom: 10px;
        letter-spacing: 0.5px;
    }
    
    /* Interactive Symbol Cards */
    .symbol-card { 
        background-color: #111827; 
        border: 1px solid #1E293B; 
        border-radius: 8px; 
        padding: 16px; 
        margin: 5px 0;
        text-align: center;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        transition: all 0.2s ease-in-out;
    }
    .symbol-card:hover { 
        transform: translateY(-2px); 
        border-color: #3B82F6;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
    }
    .symbol-card strong { font-size: 1rem; color: #94A3B8; font-weight: 500; display: block; margin-bottom: 4px;}
    .symbol-card .metric-value { font-size: 1.4rem; font-weight: 700; color: #F8FAFC; }
    
    /* Professional Badges */
    .signal-badge { 
        padding: 4px 12px; 
        border-radius: 16px; 
        font-weight: 600; 
        font-size: 0.75rem; 
        display: inline-block; 
        letter-spacing: 0.5px;
        margin-top: 8px;
        text-transform: uppercase;
    }
    .strong-buy { background-color: rgba(16, 185, 129, 0.2); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.5); }
    .buy { background-color: rgba(52, 211, 153, 0.1); color: #34D399; border: 1px solid rgba(52, 211, 153, 0.4); }
    .neutral { background-color: rgba(245, 158, 11, 0.1); color: #F59E0B; border: 1px solid rgba(245, 158, 11, 0.4); }
    .sell { background-color: rgba(248, 113, 113, 0.1); color: #F87171; border: 1px solid rgba(248, 113, 113, 0.4); }
    .strong-sell { background-color: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid rgba(239, 68, 68, 0.5); }
    
    /* Clean Info Boxes */
    .modern-box { 
        background-color: #111827; 
        border: 1px solid #1E293B;
        padding: 12px 16px; 
        border-radius: 8px; 
        font-size: 0.85rem; 
        margin: 6px 0; 
        color: #CBD5E1;
        line-height: 1.6;
    }
    .border-blue { border-left: 4px solid #3B82F6; }
    .border-green { border-left: 4px solid #10B981; }
    .border-orange { border-left: 4px solid #F59E0B; }
    .border-purple { border-left: 4px solid #8B5CF6; }
    
    /* Institutional KPI Cards */
    .kpi-card { 
        background-color: #111827; 
        border: 1px solid #1E293B;
        padding: 16px 8px; 
        text-align: center; 
        border-radius: 8px;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.02);
    }
    .kpi-icon { font-size: 1.2rem; margin-bottom: 4px; opacity: 0.8;}
    .kpi-value { font-size: 1.25rem; font-weight: 700; color: #F8FAFC; letter-spacing: 0.5px;}
    .kpi-label { color: #64748B; font-size: 0.65rem; text-transform: uppercase; font-weight: 600; letter-spacing: 1px; margin-top: 6px; }
    
    /* Tabs Styling */
    .stTabs [data-baseweb="tab-list"] { background-color: #0A0E17; border-bottom: 1px solid #1E293B; gap: 24px;}
    .stTabs [data-baseweb="tab"] { color: #94A3B8; padding-top: 10px; padding-bottom: 10px; font-weight: 500; font-size: 0.9rem;}
    .stTabs [aria-selected="true"] { color: #3B82F6 !important; border-bottom: 2px solid #3B82F6 !important; }
    
    /* Buttons */
    .stButton>button { border-radius: 6px; font-weight: 500; border: 1px solid #334155; }
    
    hr { border-color: #1E293B; margin: 1.5rem 0; }
</style>
""", unsafe_allow_html=True)

# ==================== SESSION STATE ====================
if "signal_history" not in st.session_state:
    st.session_state.signal_history = []
if "last_price_check" not in st.session_state:
    st.session_state.last_price_check = {}
if "data_source" not in st.session_state:
    st.session_state.data_source = "Yahoo Finance"
if "alerts_enabled" not in st.session_state:
    st.session_state.alerts_enabled = True
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = None
if "selected_name" not in st.session_state:
    st.session_state.selected_name = None

def get_pakistan_time():
    tz = pytz.timezone('Asia/Karachi')
    return datetime.now(tz).strftime("%d %b %Y | %I:%M:%S %p PKT")

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
                
                try:
                    s.execute(text("ALTER TABLE signal_history ADD COLUMN alert_sent BOOLEAN DEFAULT FALSE"))
                    s.commit()
                except Exception:
                    pass
                
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
                INSERT INTO signal_history (timestamp, symbol, signal, entry_price, target_price, stop_loss, alert_sent)
                VALUES (:ts, :sym, :sig, :entry, :target, :sl, FALSE)
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

def mark_alert_sent(symbol, signal, entry_price):
    conn = get_conn()
    if conn is None:
        return
    try:
        with conn.session as s:
            s.execute(text("""
                UPDATE signal_history 
                SET alert_sent = TRUE 
                WHERE symbol = :sym AND signal = :sig AND entry_price = :price 
                AND alert_sent = FALSE
                AND timestamp = (
                    SELECT timestamp FROM signal_history 
                    WHERE symbol = :sym AND signal = :sig AND entry_price = :price 
                    ORDER BY timestamp DESC LIMIT 1
                )
            """), {"sym": symbol, "sig": signal, "price": entry_price})
            s.commit()
    except Exception as e:
        st.error(f"❌ Failed to mark alert sent: {str(e)}")

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

def check_alert_sent(symbol, signal, price):
    conn = get_conn()
    if conn is None:
        return False
    try:
        with conn.session as s:
            result = s.execute(text("""
                SELECT id FROM signal_history 
                WHERE symbol = :sym AND signal = :sig 
                AND entry_price = :price 
                AND alert_sent = TRUE
                ORDER BY timestamp DESC LIMIT 1
            """), {"sym": symbol, "sig": signal, "price": price}).fetchone()
            return result is not None
    except Exception as e:
        return False

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
    if "FINNHUB_API_KEY" not in st.secrets:
        return "Neutral", "API Key Missing"
    
    try:
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
            return "Neutral", "No News"
        
        news_data = response.json()
        if not news_data:
            return "Neutral", "No News"
        
        headlines = [item['headline'] for item in news_data[:5]]
        headlines_text = "\n".join(headlines)
        
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
            return "Bullish", headlines
        elif "Bearish" in result:
            return "Bearish", headlines
        else:
            return "Neutral", headlines
            
    except Exception as e:
        return "Neutral", f"Error: {str(e)}"

# ==================== WHALE TRACKING FUNCTIONS ====================
@st.cache_data(ttl=180, show_spinner=False)
def fetch_whale_data(symbol):
    try:
        import mcp
        MCP_SERVER = "https://mcp.swisswhaleintelligence.com/mcp"
        
        if "BTC" in symbol:
            whale_symbol = "BTC"
        elif "ETH" in symbol:
            whale_symbol = "ETH"
        else:
            return "Neutral", "Symbol not supported for whale tracking"
        
        client = mcp.Client(MCP_SERVER)
        result = client.call_tool("whale_tracker", {
            "symbol": whale_symbol,
            "min_value": 100,
            "limit": 10
        })
        
        if result and result.get('transactions'):
            txs = result.get('transactions', [])
            total_buy = 0
            total_sell = 0
            for tx in txs:
                amount = tx.get('amount', 0)
                tx_type = tx.get('type', '')
                if 'buy' in tx_type.lower() or 'incoming' in tx_type.lower():
                    total_buy += amount
                elif 'sell' in tx_type.lower() or 'outgoing' in tx_type.lower():
                    total_sell += amount
            
            if total_buy > total_sell * 1.2:
                return "Bullish", f"🐋 Whale buying: {total_buy - total_sell:.1f} more {whale_symbol} bought"
            elif total_sell > total_buy * 1.2:
                return "Bearish", f"🐋 Whale selling: {total_sell - total_buy:.1f} more {whale_symbol} sold"
            else:
                return "Neutral", "Whale flow balanced"
    except:
        try:
            if "BTC" in symbol:
                url = "https://blockchain.info/unconfirmed-transactions?format=json"
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    txs = data.get('txs', [])[:5]
                    total_btc = 0
                    for tx in txs:
                        out_total = sum([out.get('value', 0) for out in tx.get('out', [])]) / 100000000
                        if out_total > 10:
                            total_btc += out_total
                    if total_btc > 50:
                        return "Bullish", f"🐋 Large BTC inflow: {total_btc:.1f} BTC"
                    elif total_btc > 20:
                        return "Neutral", f"🐋 Moderate BTC activity: {total_btc:.1f} BTC"
        except:
            pass
            
        try:
            if "ETH" in symbol and "ETHERSCAN_API_KEY" in st.secrets:
                url = f"https://api.etherscan.io/api?module=account&action=txlist&address=0x28C6c06298d514Db089934071355E5743bf21d60&apikey={st.secrets['ETHERSCAN_API_KEY']}"
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('status') == '1':
                        txs = data.get('result', [])[:5]
                        total_eth = 0
                        for tx in txs:
                            val = int(tx.get('value', 0)) / 10**18
                            if val > 100:
                                total_eth += val
                        if total_eth > 500:
                            return "Bullish", f"🐋 Large ETH inflow: {total_eth:.0f} ETH"
        except:
            pass
    
    return "Neutral", "No whale activity detected"

def fetch_eth_whale_details():
    if "ETHERSCAN_API_KEY" not in st.secrets:
        return "No ETH data"
    
    whales = [
        "0x28C6c06298d514Db089934071355E5743bf21d60",
        "0xab5801a7d398351b8be11c439e05c5b3259aec9b",
    ]
    
    try:
        details = []
        for addr in whales[:2]:
            url = f"https://api.etherscan.io/api?module=account&action=balance&address={addr}&tag=latest&apikey={st.secrets['ETHERSCAN_API_KEY']}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == '1':
                    balance = int(data.get('result', 0)) / 10**18
                    details.append(f"🏦 {addr[:8]}...: {balance:.0f} ETH")
        return "\n".join(details) if details else "No ETH data"
    except:
        return "ETH API error"

# ==================== CANDLE STATUS ====================
def get_candle_status(df):
    if df is None or len(df) < 2:
        return "Unknown", "Unknown", "Unknown", "Unknown"
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    if last['Close'] > last['Open']:
        present_color = "🟢 Bullish"
        present_emoji = ""
    else:
        present_color = "🔴 Bearish"
        present_emoji = ""
    
    if last['Close'] > prev['Close']:
        next_prediction = "🟢 Bull Expected" if last['Close'] > last['Open'] else "🔄 Reversal Probable"
        next_emoji = ""
    else:
        next_prediction = "🔴 Bear Expected" if last['Close'] < last['Open'] else "🔄 Reversal Probable"
        next_emoji = ""
    
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

    structure_score, structure_signal = detect_market_structure(df, lookback=10)
    score += structure_score
    reasons.append(f"SMC Structure: {structure_signal}")
    signal_details['structure_signal'] = structure_signal

    ob_low, ob_high, ob_signal = detect_order_blocks(df, lookback=5)
    if ob_signal != "No OB":
        signal_details['order_block_high'] = ob_high
        signal_details['order_block_low'] = ob_low
        signal_details['order_block_signal'] = ob_signal
        if "Bullish" in ob_signal:
            score += 2
            reasons.append(f"{ob_signal}")
        elif "Bearish" in ob_signal:
            score -= 2
            reasons.append(f"{ob_signal}")

    eqh, eql = detect_equal_highs_lows(df, lookback=30, threshold=0.001)
    if eqh:
        signal_details['eqh'] = eqh
        if price > eqh * 0.998 and price < eqh * 1.002:
            score -= 1
            reasons.append(f"Near Equal High (Liquidity) @ {eqh:.2f}")
    if eql:
        signal_details['eql'] = eql
        if price > eql * 0.998 and price < eql * 1.002:
            score += 1
            reasons.append(f"Near Equal Low (Liquidity) @ {eql:.2f}")

    fvg_top, fvg_bottom, fvg_signal = detect_fvg(df)
    if fvg_signal != "No FVG":
        signal_details['fvg_top'] = fvg_top
        signal_details['fvg_bottom'] = fvg_bottom
        signal_details['fvg_signal'] = fvg_signal
        if "Bullish" in fvg_signal:
            score += 1
            reasons.append(f"{fvg_signal}")
        elif "Bearish" in fvg_signal:
            score -= 1
            reasons.append(f"{fvg_signal}")

    zone_label, zone_score = detect_premium_discount(df, lookback=50)
    score += zone_score
    reasons.append(f"Zone: {zone_label}")

    if symbol:
        news_sentiment, news_headlines = fetch_news_sentiment(symbol)
        signal_details['news_sentiment'] = news_sentiment
        signal_details['news_headlines'] = news_headlines
        
        if news_sentiment == "Bullish":
            score += 1
            reasons.append(f"News Sentiment: BULLISH (+1)")
        elif news_sentiment == "Bearish":
            score -= 1
            reasons.append(f"News Sentiment: BEARISH (-1)")
        else:
            reasons.append(f"News Sentiment: Neutral")
    else:
        signal_details['news_sentiment'] = 'Neutral'
        signal_details['news_headlines'] = 'No Symbol'

    if symbol:
        whale_sentiment, whale_reason = fetch_whale_data(symbol)
        signal_details['whale_sentiment'] = whale_sentiment
        signal_details['whale_reason'] = whale_reason
        
        if whale_sentiment == "Bullish":
            score += 2
            reasons.append(f"Whale Data: {whale_reason} (+2)")
        elif whale_sentiment == "Bearish":
            score -= 2
            reasons.append(f"Whale Data: {whale_reason} (-2)")
        else:
            reasons.append(f"Whale Data: {whale_reason}")
        
        if "ETH" in symbol and "ETHERSCAN_API_KEY" in st.secrets:
            eth_details = fetch_eth_whale_details()
            signal_details['eth_whale_details'] = eth_details

    if df_higher is not None and len(df_higher) > 20:
        h_close = df_higher['Close']
        h_ema50 = h_close.ewm(span=50, adjust=False).mean().iloc[-1]
        h_price = float(h_close.iloc[-1])
        if h_price > h_ema50:
            mtf_bias = 2
            reasons.append("1H Trend: BULLISH (Price > EMA50)")
        else:
            mtf_bias = -2
            reasons.append("1H Trend: BEARISH (Price < EMA50)")
        score += mtf_bias
    else:
        reasons.append("Higher TF data missing, using lower TF only")

    if price > last['EMA_9'] > last['EMA_21']:
        score += 2
        reasons.append("EMA Structure: Bullish (9>21)")
    elif price < last['EMA_9'] < last['EMA_21']:
        score -= 2
        reasons.append("EMA Structure: Bearish (9<21)")
    else:
        reasons.append("EMA Structure: Neutral")

    rsi = float(last['RSI'])
    if rsi > 70:
        score -= 2
        reasons.append(f"RSI Overbought ({rsi:.1f})")
    elif rsi < 30:
        score += 2
        reasons.append(f"RSI Oversold ({rsi:.1f})")
    elif rsi > 55:
        score += 1
        reasons.append(f"RSI Bullish ({rsi:.1f})")
    elif rsi < 45:
        score -= 1
        reasons.append(f"RSI Bearish ({rsi:.1f})")
    else:
        reasons.append(f"RSI Neutral ({rsi:.1f})")

    if last['MACD_Hist'] > 0:
        score += 1.5
        reasons.append("MACD Positive Histogram")
    else:
        score -= 1.5
        reasons.append("MACD Negative Histogram")

    vol_ma = float(last['Volume_MA'])
    vol_now = float(last['Volume'])
    if vol_ma > 0 and vol_now > vol_ma * 1.5:
        if score > 0:
            score += 1
            reasons.append(f"Volume Spike ({vol_now/vol_ma:.1f}x Avg)")
        elif score < 0:
            score -= 1
            reasons.append(f"Volume Spike ({vol_now/vol_ma:.1f}x Avg)")
        else:
            reasons.append(f"Volume Spike ({vol_now/vol_ma:.1f}x Avg)")
    else:
        reasons.append("Volume normal")

    patterns = detect_candle_patterns(df)
    if patterns:
        for p in patterns:
            if "Bullish" in p or "Buy" in p:
                score += 1.5
                reasons.append(f"{p}")
            elif "Bearish" in p or "Sell" in p:
                score -= 1.5
                reasons.append(f"{p}")
            else:
                reasons.append(f"{p}")
    else:
        reasons.append("No strong pattern detected")

    atr = float(last['ATR'])
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

    sl_price = price - (2.0 * atr) if "BUY" in signal else price + (2.0 * atr)
    tp_price = price + (2.0 * atr) if "BUY" in signal else price - (2.0 * atr)
    rr_ratio = round(2.0 / 2.0, 2)

    if "BUY" in signal:
        expected = f"Next Candle Likely Bullish. Target: {tp_price:.2f}"
        pullback = f"Retrace to {price - (0.5*atr):.2f} possible before up move."
    elif "SELL" in signal:
        expected = f"Next Candle Likely Bearish. Target: {tp_price:.2f}"
        pullback = f"Bounce to {price + (0.5*atr):.2f} possible before down move."
    else:
        expected = "Direction unclear. Wait for S/R break."
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
        "news_headlines": signal_details.get('news_headlines', 'No News'),
        "whale_sentiment": signal_details.get('whale_sentiment', 'Neutral'),
        "whale_reason": signal_details.get('whale_reason', 'No whale data'),
        "eth_whale_details": signal_details.get('eth_whale_details', ''),
        "structure_signal": signal_details.get('structure_signal', 'Neutral')
    }

# ==================== GROK & GEMINI ====================
def get_grok_analysis(symbol, tf, analysis, price):
    prompt = f"Symbol: {symbol} | TF: {tf} | Price: {price}\nSignal: {analysis['signal']} | Score: {analysis['score']}\nReasons: {', '.join(analysis['reasons'])}\nPatterns: {', '.join(analysis['patterns']) if analysis['patterns'] else 'None'}\nMTF Bias: {analysis['mtf_bias']}\nSL: {analysis['sl']} | TP: {analysis['tp']}\nNews Sentiment: {analysis.get('news_sentiment', 'Neutral')}\nWhale Sentiment: {analysis.get('whale_sentiment', 'Neutral')}\n\nAs a pro trader, give short, direct advice on this trade:\n1. Is this signal reliable? Why?\n2. What is the probability of next candle going as expected?\n3. Should we enter now or wait? (give specific price action triggers)\nMax 6 lines."
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
col_header, col_spacer = st.columns([1, 5])
with col_header:
    st.markdown('<div class="main-header">Pro Max Terminal</div>', unsafe_allow_html=True)

st.caption(f"📅 {get_pakistan_time()} | SMC • News • Whale Tracker")

btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 4])
with btn_col1:
    if st.session_state.alerts_enabled:
        if st.button("🔔 Alerts Active", key="alert_toggle"):
            st.session_state.alerts_enabled = False
            st.rerun()
    else:
        if st.button("🔕 Alerts Muted", key="alert_toggle"):
            st.session_state.alerts_enabled = True
            st.rerun()
with btn_col2:
    if st.button("🔄 Sync Data", key="refresh_btn"):
        st.cache_data.clear()
        st.rerun()

MAIN_SYMBOLS = {"Bitcoin (BTC)": "BTC-USD", "USD/JPY": "USDJPY=X", "NAS100": "NQ=F"}

db_initialized = init_db()
if not db_initialized:
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
        
        pct_color = "#10B981" if pct >= 0 else "#EF4444"
        badge_cls = temp_analysis['badge_class'] if temp_analysis else 'neutral'
        
        st.markdown(
            f"""
            <div class='symbol-card'>
                <strong>{name}</strong>
                <div style="margin: 8px 0;">
                    <span class='metric-value'>{price:,.2f}</span>
                    <span style='color:{pct_color}; font-size: 0.9rem; font-weight: 500; margin-left: 6px;'>{pct:+.2f}%</span>
                </div>
                <span class='signal-badge {badge_cls}'>{sig}</span>
            </div>
            """, unsafe_allow_html=True
        )
        if st.button(f"Load Terminal", key=f"btn_{idx}", use_container_width=True):
            st.session_state.selected_symbol = ticker
            st.session_state.selected_name = name
            st.rerun()

if st.session_state.get("selected_symbol"):
    ticker = st.session_state.selected_symbol
    name = st.session_state.get("selected_name", ticker)
    st.divider()
    
    st.markdown(f"<h3 style='margin-bottom:0;'>{name} <span style='color:#64748B; font-size:1.2rem; font-weight:400;'>| {ticker}</span></h3>", unsafe_allow_html=True)
    st.caption(f"Data Feed: {st.session_state.data_source}")
    
    col_tf1, col_tf2, _ = st.columns([1, 1, 3])
    with col_tf1:
        tf_lower = st.selectbox("Entry Timeframe", ["5m", "15m", "30m"], index=1)
    with col_tf2:
        tf_higher = st.selectbox("Trend Timeframe", ["1h", "4h"], index=0)
    
    df_lower = fetch_ohlcv(ticker, interval=tf_lower, period="3d")
    df_higher = fetch_ohlcv(ticker, interval=tf_higher, period="5d")
    
    if df_lower is None or len(df_lower) < 30:
        st.error("Insufficient market data. Please wait and resync.")
        st.stop()
    
    present_color, present_emoji, next_prediction, next_emoji = get_candle_status(df_lower)
    analysis = calculate_advanced_signal(df_lower, df_higher, ticker)
    
    if analysis:
        if analysis['signal'] in ["BUY", "STRONG BUY", "SELL", "STRONG SELL"] and db_initialized:
            save_signal(ticker, analysis['signal'], analysis['last_price'], analysis['tp'], analysis['sl'])
        if db_initialized:
            update_old_signals(ticker, df_lower)
        
        if analysis['signal'] in ["BUY", "STRONG BUY", "SELL", "STRONG SELL"] and db_initialized:
            if st.session_state.alerts_enabled:
                already_sent = check_alert_sent(ticker, analysis['signal'], analysis['last_price'])
                if not already_sent:
                    telegram_sent = send_telegram_alert(name, analysis['signal'], analysis['last_price'], analysis['tp'], analysis['sl'])
                    email_sent = send_email_alert(name, analysis['signal'], analysis['last_price'], analysis['tp'], analysis['sl'])
                    if telegram_sent and email_sent:
                        mark_alert_sent(ticker, analysis['signal'], analysis['last_price'])
        
        # ==================== KPI CARDS ====================
        st.markdown("<br>", unsafe_allow_html=True)
        kpi_cols = st.columns(5)
        
        with kpi_cols[0]:
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">💰</span>
                <span class="kpi-value">{analysis['last_price']:,.2f}</span>
                <div class="kpi-label">Current Price</div>
            </div>
            """, unsafe_allow_html=True)
            
        with kpi_cols[1]:
            badge_color = "#10B981" if "BUY" in analysis['signal'] else "#EF4444" if "SELL" in analysis['signal'] else "#F59E0B"
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">🎯</span>
                <span class="kpi-value" style="color:{badge_color}">{analysis['signal']}</span>
                <div class="kpi-label">Active Signal</div>
            </div>
            """, unsafe_allow_html=True)
            
        with kpi_cols[2]:
            rsi_color = "#10B981" if analysis['rsi'] < 30 else "#EF4444" if analysis['rsi'] > 70 else "#94A3B8"
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">📈</span>
                <span class="kpi-value" style="color:{rsi_color}">{analysis['rsi']}</span>
                <div class="kpi-label">RSI Indicator</div>
            </div>
            """, unsafe_allow_html=True)
            
        with kpi_cols[3]:
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">⚡</span>
                <span class="kpi-value">{analysis['score']}</span>
                <div class="kpi-label">Confluence Score</div>
            </div>
            """, unsafe_allow_html=True)
            
        with kpi_cols[4]:
            winrate, total = get_stats(ticker) if db_initialized else (None, 0)
            wr_str = f"{winrate}%" if winrate is not None else "--"
            st.markdown(f"""
            <div class="kpi-card">
                <span class="kpi-icon">🏆</span>
                <span class="kpi-value">{wr_str}</span>
                <div class="kpi-label">Win Rate</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ==================== TABS ====================
        tab1, tab2, tab3 = st.tabs(["Market Context", "Whale Intelligence", "System Backtest"])
        
        with tab1:
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("#### Smart Money Concepts (SMC)")
                st.markdown(
                    f"""<div class='modern-box border-purple'>
                    <div style='margin-bottom: 4px'><b>Structure:</b> {analysis.get('structure_signal', 'N/A')}</div>
                    <div style='margin-bottom: 4px'><b>Order Block:</b> {analysis.get('order_block_signal', 'None')}</div>
                    <div style='margin-bottom: 4px'><b>Fair Value Gap:</b> {analysis.get('fvg_signal', 'None')}</div>
                    <div><b>EQH:</b> {analysis.get('eqh', 'None')} | <b>EQL:</b> {analysis.get('eql', 'None')}</div>
                    </div>""", unsafe_allow_html=True
                )
                
                st.markdown("#### Trend & Momentum")
                st.markdown(
                    f"""<div class='modern-box border-blue'>
                    <div style='margin-bottom: 4px'><b>Higher TF Bias:</b> {analysis['mtf_bias']}</div>
                    <div style='margin-bottom: 4px'><b>Candle State:</b> {present_color}</div>
                    <div><b>Prediction:</b> {next_prediction}</div>
                    </div>""", unsafe_allow_html=True
                )

            with col2:
                st.markdown("#### Risk Parameters")
                st.markdown(
                    f"""<div class='modern-box border-orange'>
                    <div style='margin-bottom: 4px'><b>Stop Loss:</b> {analysis['sl']}</div>
                    <div style='margin-bottom: 4px'><b>Take Profit:</b> {analysis['tp']}</div>
                    <div><b>Risk/Reward:</b> 1 : {analysis['rr_ratio']}</div>
                    </div>""", unsafe_allow_html=True
                )
                
                st.markdown("#### Catalyst (News)")
                sentiment = analysis.get('news_sentiment', 'Neutral')
                headlines = analysis.get('news_headlines', 'No News')
                if isinstance(headlines, list):
                    headlines = headlines[0] if headlines else "No recent headlines"
                
                s_color = "border-green" if sentiment == "Bullish" else "border-red" if sentiment == "Bearish" else "border-blue"
                st.markdown(
                    f"""<div class='modern-box {s_color}'>
                    <div style='margin-bottom: 4px'><b>Bias:</b> {sentiment}</div>
                    <div style='font-size: 0.8rem; color: #94A3B8;'>{headlines[:90]}...</div>
                    </div>""", unsafe_allow_html=True
                )

            with st.expander("View Algorithmic Reasoning Log"):
                for r in analysis['reasons']:
                    st.write(f"• {r}")
        
        with tab2:
            st.markdown("#### Institutional Order Flow")
            whale_sentiment = analysis.get('whale_sentiment', 'Neutral')
            whale_reason = analysis.get('whale_reason', 'No whale data')
            
            w_color = "border-green" if whale_sentiment == "Bullish" else "border-red" if whale_sentiment == "Bearish" else "border-blue"
            st.markdown(
                f"""<div class='modern-box {w_color}'>
                <div style='margin-bottom: 4px; font-weight: 600;'>{whale_sentiment.upper()}</div>
                <div>{whale_reason}</div>
                </div>""", unsafe_allow_html=True
            )
            
            if "ETH" in ticker:
                st.markdown("#### Tracked Wallets (ETH)")
                st.code(analysis.get('eth_whale_details', 'No ETH data'), language='text')
        
        with tab3:
            st.markdown("#### Historical Performance")
            if db_initialized:
                winrate, total = get_stats(ticker)
                if winrate is not None:
                    st.progress(winrate / 100, text=f"Last {total} Signals Win Rate: {winrate}%")
                    if winrate >= 60:
                        st.success("Edge Validated: System maintaining positive expectancy.")
                    else:
                        st.warning("Drawdown Phase: Exercise strict risk management.")
                else:
                    st.info("System initializing. Awaiting closed trade data.")
            else:
                st.warning("Database disconnected.")
        
        st.divider()
        st.markdown("### AI Co-Pilot Analysis")
        col_ai1, col_ai2 = st.columns(2)
        
        with col_ai1:
            st.markdown("#### Quantitative Context (Grok)")
            if st.button("Generate Grok Report", key="grok_main"):
                with st.spinner("Processing context..."):
                    resp = get_grok_analysis(name, tf_lower, analysis, analysis['last_price'])
                st.markdown(f"<div class='modern-box border-blue'>{resp}</div>", unsafe_allow_html=True)
                
        with col_ai2:
            st.markdown("#### Visual Analysis (Gemini)")
            uploaded = st.file_uploader(f"Upload Chart ({tf_lower})", type=["png", "jpg"], label_visibility="collapsed")
            if uploaded:
                img = Image.open(uploaded)
                st.image(img, use_container_width=True)
                if st.button("Analyze Image", key="gemini_main"):
                    with st.spinner("Analyzing structures..."):
                        gem_res = analyze_chart_with_gemini(img, name, tf_lower)
                    st.markdown(f"<div class='modern-box border-purple'>{gem_res}</div>", unsafe_allow_html=True)

    else:
        st.error("Insufficient volatility or data points to calculate reliable signals.")
else:
    st.info("Select an instrument from the terminal overhead to begin analysis.")
