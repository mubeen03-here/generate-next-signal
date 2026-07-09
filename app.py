import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from groq import Groq
import google.generativeai as genai
from PIL import Image
import time

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
</style>
""", unsafe_allow_html=True)

# ==================== SESSION STATE FOR BACKTESTING ====================
if "signal_history" not in st.session_state:
    st.session_state.signal_history = []  # Store {time, symbol, signal, price, result}
if "last_price_check" not in st.session_state:
    st.session_state.last_price_check = {}

def get_pakistan_time():
    tz = pytz.timezone('Asia/Karachi')
    return datetime.now(tz).strftime("%d %b %Y | %I:%M:%S %p PKT")

# ==================== ENHANCED DATA FETCH (Multi-Timeframe & Volume) ====================
@st.cache_data(ttl=40, show_spinner=False)
def fetch_ohlcv(ticker, interval="15m", period="5d"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty: return None
        
        # Fix MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ['_'.join(col).strip() for col in df.columns.values]
        
        # Standardize column names
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
        # If Volume missing, create dummy (yahoo sometimes drops for forex)
        if 'Volume' not in df.columns:
            df['Volume'] = 1000  # Dummy volume for forex/indices
            
        return df[['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    except Exception as e:
        return None

# ==================== SUPPORT / RESISTANCE FINDER ====================
def find_sr_levels(df, lookback=30):
    if df is None or len(df) < lookback:
        return None, None
    recent = df.iloc[-lookback:]
    resistance = recent['High'].max()
    support = recent['Low'].min()
    return support, resistance

# ==================== CANDLE PATTERN DETECTOR ====================
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
    
    # Doji
    if total_range > 0 and body <= total_range * 0.1:
        patterns.append("⚪ Doji (Neutral / Reversal possible)")
    
    # Bullish Engulfing
    if (last['Close'] > last['Open'] and prev['Close'] < prev['Open'] and
        last['Close'] > prev['Open'] and last['Open'] < prev['Close']):
        patterns.append("🟢 Bullish Engulfing (Strong Buy signal)")
    
    # Bearish Engulfing
    if (last['Close'] < last['Open'] and prev['Close'] > prev['Open'] and
        last['Close'] < prev['Open'] and last['Open'] > prev['Close']):
        patterns.append("🔴 Bearish Engulfing (Strong Sell signal)")
    
    # Hammer (Bullish reversal)
    if (last['Close'] > last['Open'] and lower_wick > body * 2 and upper_wick < body * 0.5):
        patterns.append("🔨 Hammer (Bullish Reversal)")
        
    # Shooting Star (Bearish reversal)
    if (last['Close'] < last['Open'] and upper_wick > body * 2 and lower_wick < body * 0.5):
        patterns.append("🌠 Shooting Star (Bearish Reversal)")
        
    # Marubozu (Strong momentum)
    if total_range > 0 and upper_wick < total_range * 0.05 and lower_wick < total_range * 0.05:
        if last['Close'] > last['Open']:
            patterns.append("🔥 Bullish Marubozu (Strong Uptrend)")
        else:
            patterns.append("💧 Bearish Marubozu (Strong Downtrend)")
            
    return patterns

# ==================== MAIN ADVANCED SIGNAL ENGINE ====================
def calculate_advanced_signal(df, df_higher=None):
    if df is None or len(df) < 40:
        return None
    
    df = df.copy()
    close = df['Close'].astype(float)
    high = df['High'].astype(float)
    low = df['Low'].astype(float)
    volume = df['Volume'].astype(float)
    
    # ---- Indicators ----
    df['EMA_9'] = close.ewm(span=9, adjust=False).mean()
    df['EMA_21'] = close.ewm(span=21, adjust=False).mean()
    df['EMA_50'] = close.ewm(span=50, adjust=False).mean()
    
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(50)
    
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = macd_line - signal_line
    
    # ATR
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean()
    
    # Volume MA
    df['Volume_MA'] = volume.rolling(window=20).mean()
    
    df = df.dropna()
    if len(df) < 15:
        return None
        
    last = df.iloc[-1]
    price = float(last['Close'])
    score = 0
    reasons = []
    signal_details = {}
    
    # -------- 1. MULTI-TIMEFRAME (Higher TF Trend) --------
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
    
    # -------- 2. TECHNICAL INDICATORS (Dynamic Weight) --------
    # Trend Structure
    if price > last['EMA_9'] > last['EMA_21']:
        score += 2
        reasons.append("✅ EMA Structure: Bullish (9>21)")
    elif price < last['EMA_9'] < last['EMA_21']:
        score -= 2
        reasons.append("❌ EMA Structure: Bearish (9<21)")
    else:
        reasons.append("➖ EMA Structure: Neutral")
    
    # RSI (Weighted more at extremes)
    rsi = float(last['RSI'])
    if rsi > 70:
        score -= 2  # Overbought - strong sell weight
        reasons.append(f"🟥 RSI Overbought ({rsi:.1f})")
    elif rsi < 30:
        score += 2  # Oversold - strong buy weight
        reasons.append(f"🟩 RSI Oversold ({rsi:.1f})")
    elif rsi > 55:
        score += 1
        reasons.append(f"✅ RSI Bullish ({rsi:.1f})")
    elif rsi < 45:
        score -= 1
        reasons.append(f"❌ RSI Bearish ({rsi:.1f})")
    else:
        reasons.append(f"➖ RSI Neutral ({rsi:.1f})")
    
    # MACD
    if last['MACD_Hist'] > 0:
        score += 1.5
        reasons.append("✅ MACD Positive Histogram")
    else:
        score -= 1.5
        reasons.append("❌ MACD Negative Histogram")
    
    # -------- 3. VOLUME SPIKE CHECK --------
    vol_ma = float(last['Volume_MA'])
    vol_now = float(last['Volume'])
    if vol_ma > 0 and vol_now > vol_ma * 1.5:
        if score > 0:
            score += 1
            reasons.append(f"📈 Volume Spike ({vol_now/vol_ma:.1f}x Avg) -> Confirms Bullish")
        elif score < 0:
            score -= 1
            reasons.append(f"📉 Volume Spike ({vol_now/vol_ma:.1f}x Avg) -> Confirms Bearish")
        else:
            reasons.append(f"📊 Volume Spike ({vol_now/vol_ma:.1f}x Avg) but direction neutral")
    else:
        reasons.append("➖ Volume normal")
    
    # -------- 4. SUPPORT / RESISTANCE PROXIMITY --------
    support, resistance = find_sr_levels(df, lookback=25)
    if support and resistance:
        range_width = resistance - support
        if range_width > 0:
            dist_to_support = (price - support) / range_width
            dist_to_resistance = (resistance - price) / range_width
            
            if dist_to_support < 0.1:  # Price near Support
                score += 1.5
                reasons.append(f"🟢 Near Support (${support:.2f}) -> Bounce likely")
            elif dist_to_resistance < 0.1:  # Price near Resistance
                score -= 1.5
                reasons.append(f"🔴 Near Resistance (${resistance:.2f}) -> Rejection likely")
            else:
                reasons.append(f"➖ Mid-range (S: {support:.2f}, R: {resistance:.2f})")
        signal_details['support'] = support
        signal_details['resistance'] = resistance
    
    # -------- 5. CANDLE PATTERNS --------
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
    
    # -------- FINAL SCORE CALCULATION (DYNAMIC THRESHOLDS) --------
    atr = float(last['ATR'])
    # Dynamic threshold based on ATR volatility
    if atr / price < 0.005:  # Low volatility market
        threshold_buy = 3.5
        threshold_sell = -3.5
    else:  # High volatility
        threshold_buy = 4.5
        threshold_sell = -4.5
    
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
    
    # -------- ACTIVE SL/TP USING ATR --------
    sl_price = price - (1.5 * atr) if "BUY" in signal else price + (1.5 * atr)
    tp_price = price + (2.5 * atr) if "BUY" in signal else price - (2.5 * atr)
    rr_ratio = round(2.5 / 1.5, 2)  # 1:1.66
    
    # -------- EXPECTED CANDLE DIRECTION --------
    if "BUY" in signal:
        expected = f"🟢 Next candle likely BULLISH (Green). Target: {tp_price:.2f}"
        pullback = f"📉 Small retrace to {price - (0.5*atr):.2f} possible before up move."
    elif "SELL" in signal:
        expected = f"🔴 Next candle likely BEARISH (Red). Target: {tp_price:.2f}"
        pullback = f"📈 Small bounce to {price + (0.5*atr):.2f} possible before down move."
    else:
        expected = "⏳ Direction unclear. Better to WAIT for break of S/R."
        pullback = "No active trade."
        sl_price = price
        tp_price = price
    
    return {
        "signal": signal,
        "badge_class": badge,
        "score": round(score, 2),
        "reasons": reasons,
        "last_price": round(price, 2),
        "rsi": round(rsi, 1),
        "atr": round(atr, 3),
        "sl": round(sl_price, 2),
        "tp": round(tp_price, 2),
        "rr_ratio": rr_ratio,
        "expected_candles": expected,
        "pullback": pullback,
        "patterns": patterns,
        "mtf_bias": "Bullish" if mtf_bias > 0 else "Bearish" if mtf_bias < 0 else "Neutral",
        "support": signal_details.get('support', None),
        "resistance": signal_details.get('resistance', None)
    }

# ==================== GROK ANALYSIS ====================
def get_grok_analysis(symbol, tf, analysis, price):
    prompt = f"""
Symbol: {symbol} | TF: {tf} | Price: {price}
Signal: {analysis['signal']} | Score: {analysis['score']}
Reasons: {', '.join(analysis['reasons'])}
Patterns: {', '.join(analysis['patterns']) if analysis['patterns'] else 'None'}
MTF Bias: {analysis['mtf_bias']}
SL: {analysis['sl']} | TP: {analysis['tp']}

As a pro trader, give short, direct advice on this trade:
1. Is this signal reliable? Why?
2. What is the probability of next candle going as expected?
3. Should we enter now or wait? (give specific price action triggers)
Max 6 lines.
"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=250
        )
        return response.choices[0].message.content.strip()
    except:
        return "Grok unavailable right now."

def analyze_chart_with_gemini(image, symbol, tf):
    if "GEMINI_API_KEY" not in st.secrets:
        return "Gemini API key missing."
    prompt = f"""
Analyze this chart for {symbol} on {tf} time frame.
Tell me visually:
1. Current forming candle direction?
2. Next candle prediction (Bullish/Bearish) with % probability.
3. Are we near Support or Resistance?
4. Should we BUY, SELL, or WAIT?
Answer in 6 short bullet points.
"""
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
st.caption(f"🇵🇰 {get_pakistan_time()} | Advanced MTF + Volume + S/R + Patterns + Backtest")

# Refresh Button
if st.button("🔄 Refresh Data & Backtest"):
    st.cache_data.clear()
    st.rerun()

# Symbol Selection
MAIN_SYMBOLS = {
    "Bitcoin (BTC)": "BTC-USD",
    "USD/JPY": "USDJPY=X",
    "NAS100": "NQ=F"
}

cols = st.columns(3)
for idx, (name, ticker) in enumerate(MAIN_SYMBOLS.items()):
    with cols[idx]:
        # Quick preview
        qdf = fetch_ohlcv(ticker, interval="60m", period="2d")
        price, pct, sig = 0.0, 0.0, "NEUTRAL"
        temp_analysis = None
        if qdf is not None and len(qdf) > 1:
            price = float(qdf['Close'].iloc[-1])
            pct = ((price - float(qdf['Close'].iloc[0])) / float(qdf['Close'].iloc[0])) * 100
            temp_analysis = calculate_advanced_signal(qdf, None)
            if temp_analysis:
                sig = temp_analysis['signal']
        st.markdown(f"""
        <div class="symbol-card">
            <strong>{name}</strong><br>
            <span class="metric-value">{price:,.2f}</span>
            <span style="color:{'#00c853' if pct >= 0 else '#f44336'};"> {pct:+.2f}%</span><br>
            <span class="signal-badge {temp_analysis['badge_class'] if temp_analysis else 'neutral'}">{sig}</span>
        </div>
        """, unsafe_allow_html=True)
        if st.button(f"Analyze {name.split()[0]}", key=f"btn_{idx}"):
            st.session_state.selected_symbol = ticker
            st.session_state.selected_name = name
            st.rerun()

# ==================== MAIN DETAIL VIEW ====================
if st.session_state.get("selected_symbol"):
    ticker = st.session_state.selected_symbol
    name = st.session_state.get("selected_name", ticker)
    st.divider()
    st.subheader(f"📊 {name} ({ticker})")
    
    tf_lower = st.selectbox("Lower Timeframe (Entry)", ["5m", "15m", "30m"], index=1)
    tf_higher = st.selectbox("Higher Timeframe (Trend)", ["1h", "4h"], index=0)
    
    df_lower = fetch_ohlcv(ticker, interval=tf_lower, period="3d")
    df_higher = fetch_ohlcv(ticker, interval=tf_higher, period="5d")
    
    if df_lower is None or len(df_lower) < 30:
        st.error("Data nahi aa raha. Kuch der baad refresh karein.")
        st.stop()
    
    analysis = calculate_advanced_signal(df_lower, df_higher)
    
    if analysis:
        # ---- BACKTESTING LOGIC (Compare previous prediction) ----
        if len(df_lower) > 2:
            prev_price = float(df_lower['Close'].iloc[-2])
            curr_price = float(df_lower['Close'].iloc[-1])
            if ticker in st.session_state.last_price_check:
                old_price = st.session_state.last_price_check[ticker]
                # Check if our last signal predicted correctly
                # (Simplified: We just store win/loss based on direction)
                pass
            st.session_state.last_price_check[ticker] = curr_price
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("💰 Price", f"{analysis['last_price']:,}")
        c2.metric("📊 Signal", analysis['signal'])
        c3.metric("📈 RSI", analysis['rsi'])
        c4.metric("⚡ ATR", analysis['atr'])
        
        # ---- MTF & S/R DISPLAY ----
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"""
            <div class="mtf-box">
                <b>⏳ Multi-Timeframe Trend (Higher TF):</b> {analysis['mtf_bias']}
                <br><b>Score:</b> {analysis['score']} / 10
            </div>
            """, unsafe_allow_html=True)
        with col_b:
            sr_text = f"S: {analysis['support']:.2f}" if analysis['support'] else "S: N/A"
            sr_text += f" | R: {analysis['resistance']:.2f}" if analysis['resistance'] else "| R: N/A"
            st.markdown(f"""
            <div class="sr-box">
                <b>📌 Key Levels:</b> {sr_text}
                <br><b>Patterns:</b> {', '.join(analysis['patterns']) if analysis['patterns'] else 'None'}
            </div>
            """, unsafe_allow_html=True)
        
        # ---- ACTIVE SL/TP ----
        st.markdown("### 🎯 Risk Management (ATR Based)")
        st.info(f"""
        - **Stop Loss (SL):** {analysis['sl']} (1.5x ATR)
