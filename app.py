import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
from groq import Groq
import google.generativeai as genai
from PIL import Image

# ==================== API KEYS SETUP ====================
if "GROQ_API_KEY" in st.secrets:
    groq_client = Groq(api_key=st.secrets["GROQ_API_KEY"])
else:
    st.error("GROQ_API_KEY is missing in Streamlit Secrets!")
    st.stop()

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.warning("⚠️ GEMINI_API_KEY missing in Secrets! Chart Image Analysis won't work until added.")

st.set_page_config(page_title="Pro Trading Signals", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: #fafafa; }
    .main-header { font-size: 2.2rem; font-weight: 700; background: linear-gradient(90deg, #00ff9f, #00b8ff);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 0.5rem; }
    .symbol-card { background-color: #161b22; border: 1px solid #30363d; border-radius: 14px; padding: 1rem; margin-bottom: 1rem; }
    .signal-badge { padding: 0.3rem 0.9rem; border-radius: 20px; font-weight: 700; font-size: 0.9rem; display: inline-block; }
    .strong-buy { background-color: #00c853; color: white; }
    .buy { background-color: #4caf50; color: white; }
    .neutral { background-color: #ff9800; color: white; }
    .sell { background-color: #f44336; color: white; }
    .strong-sell { background-color: #d32f2f; color: white; }
    .metric-value { font-size: 1.7rem; font-weight: 700; }
    .wait-box { background-color: #2d2d2d; border: 2px solid #ff9800; border-radius: 12px; padding: 1rem; margin: 0.5rem 0; }
    .ai-box { background-color: #1a1f2e; border: 1px solid #4a90e2; border-radius: 12px; padding: 1rem; margin-top: 1rem; }
    .gemini-box { background-color: #1c2833; border: 1px solid #00ff9f; border-radius: 12px; padding: 1rem; margin-top: 1rem; }
    .current-candle-box { background-color: #1f2a3d; border: 1px solid #4a90e2; border-radius: 12px; padding: 1rem; margin: 1rem 0; }
</style>
""", unsafe_allow_html=True)

if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = None

MAIN_SYMBOLS = {
    "Bitcoin (BTC)": {"yf_ticker": "BTC-USD", "display": "BTC/USD"},
    "USD/JPY": {"yf_ticker": "USDJPY=X", "display": "USD/JPY"},
    "NAS100": {"yf_ticker": "NQ=F", "display": "NAS100 (NQ)"},
}

def get_pakistan_time():
    tz = pytz.timezone('Asia/Karachi')
    return datetime.now(tz).strftime("%d %b %Y  |  %I:%M:%S %p PKT")

@st.cache_data(ttl=40, show_spinner=False)
def fetch_ohlcv(ticker, interval="15m", period="5d"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
            
        df = df.reset_index()
        df.columns = [str(c).capitalize() for c in df.columns]
        
        rename_map = {}
        for col in df.columns:
            if "datetime" in col.lower() or "date" in col.lower(): rename_map[col] = "Datetime"
            elif col.lower() in ["close", "open", "high", "low"]: rename_map[col] = col.capitalize()
        df = df.rename(columns=rename_map)
        
        if "Close" not in df.columns: return None
        return df[["Datetime", "Open", "High", "Low", "Close"]].dropna()
    except:
        return None

def calculate_technical_signal(df):
    if df is None or len(df) < 40: return None
    df = df.copy()
    
    close = df['Close'].astype(float)
    high = df['High'].astype(float)
    low = df['Low'].astype(float)
    
    df['EMA_9'] = close.ewm(span=9, adjust=False).mean()
    df['EMA_21'] = close.ewm(span=21, adjust=False).mean()
    
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
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = macd_line - signal_line
    
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean()
    
    df = df.dropna()
    if len(df) < 15: return None
    
    last = df.iloc[-1]
    price = float(last['Close'])
    score = 0
    reasons = []
    
    if price > last['EMA_9'] > last['EMA_21']: score += 2; reasons.append("✅ Bullish structure")
    elif price > last['EMA_9']: score += 1; reasons.append("✅ Above EMA9")
    elif price < last['EMA_9'] < last['EMA_21']: score -= 2; reasons.append("❌ Bearish structure")
    
    rsi = float(last['RSI'])
    if rsi > 58: score += 1; reasons.append("✅ RSI bullish")
    elif rsi < 42: score -= 1; reasons.append("❌ RSI bearish")
    
    if last['MACD_Hist'] > 0: score += 1; reasons.append("✅ MACD positive")
    else: score -= 1; reasons.append("❌ MACD negative")
    
    if score >= 4: signal, badge = "STRONG BUY", "strong-buy"
    elif score >= 2: signal, badge = "BUY", "buy"
    elif score <= -4: signal, badge = "STRONG SELL", "strong-sell"
    elif score <= -2: signal, badge = "SELL", "sell"
    else: signal, badge = "WAIT", "neutral"
    
    if "BUY" in signal:
        expected = "Next candle likely bullish (green)"
        pullback = "Possible small red pullback then continuation"
    elif "SELL" in signal:
        expected = "Next candle likely bearish (red)"
        pullback = "Possible small green pullback then continuation"
    else:
        expected = "Next candle direction unclear - better to wait"
        pullback = ""
    
    return {
        "signal": signal, "badge_class": badge, "score": score, "reasons": reasons,
        "last_price": round(price, 2), "rsi": round(rsi, 1), "atr": round(float(last['ATR']), 2),
        "expected_candles": expected, "pullback": pullback
    }

def get_current_candle_status(df):
    if df is None or len(df) < 2: return None
    last = df.iloc[-1]
    last_color = "🟢 Green" if last['Close'] > last['Open'] else "🔴 Red"
    forming = "🟢 Bullish forming" if last['Close'] > last['Open'] else "🔴 Bearish forming"
    return {"last_closed": last_color, "forming_now": forming}

def get_grok_analysis(symbol, tf, technical_signal, recent_data):
    prompt = f"""
You are a professional price action trader.

Symbol: {symbol}
Timeframe: {tf}
Current Price: {recent_data}
Technical Signal: {technical_signal}

Give your independent analysis:
1. What is happening right now?
2. Probability that the NEXT candle will be bullish or bearish?
3. Why do you think so? (Give clear reason)
4. Should we take the trade or Wait? Why?

Be honest and critical. Max 6-7 lines.
"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=220
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Analysis failed: {str(e)}"

def analyze_chart_with_gemini(image, symbol, tf):
    """Gemini Vision AI model to analyze chart image and predict next candle"""
    if "GEMINI_API_KEY" not in st.secrets:
        return "GEMINI_API_KEY is not configured in Streamlit Secrets."
        
    prompt = f"""
You are an elite price action trader and candlestick patterns expert.
Analyze this chart screenshot for {symbol} on the {tf} timeframe.

Analyze:
1. **Current Forming Candle:** Look at the live/present forming candle (wicks, body, momentum).
2. **Key Price Action:** Support/Resistance levels, trendlines, or active chart patterns.
3. **NEXT Candle Prediction:** Predict the direction of the upcoming NEXT candle (Bullish Green 🟢 or Bearish Red 🔴) with estimated probability (%).
4. **Actionable Signal:** BUY, SELL, or WAIT with exact reasons based on the chart image.

Keep your answer clear, direct, professional, and limited to 6-8 bullet points.
"""
    # Active Gemini models list for auto-fallback (Fixes 404 error)
    model_candidates = [
        'gemini-2.5-flash',
        'gemini-2.0-flash',
        'gemini-1.5-flash-latest',
        'gemini-1.5-flash'
    ]
    
    last_error = None
    for model_name in model_candidates:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([prompt, image])
            if response and response.text:
                return response.text
        except Exception as e:
            last_error = e
            continue
            
    return f"Gemini Image Analysis failed: {str(last_error)}"

# ==================== UI ====================
st.markdown('<h1 class="main-header">📈 Pro Trading Signals</h1>', unsafe_allow_html=True)
st.caption(f"Pakistan Time: {get_pakistan_time()}  |  Technical + Grok + Gemini Vision Analysis")

if st.button("🔄 Refresh All Data"):
    st.cache_data.clear()
    st.rerun()

# Grid Layout
cols = st.columns(3)
for idx, (disp_name, meta) in enumerate(MAIN_SYMBOLS.items()):
    col = cols[idx % 3]
    with col:
        quick_df = fetch_ohlcv(meta["yf_ticker"], interval="60m", period="2d")
        price, pct, sig, badge = 0.0, 0.0, "NEUTRAL", "neutral"
        if quick_df is not None and len(quick_df) > 1:
            price = float(quick_df['Close'].iloc[-1])
            pct = ((price - float(quick_df['Close'].iloc[0])) / float(quick_df['Close'].iloc[0])) * 100
            anal = calculate_technical_signal(quick_df)
            if anal: 
                sig = anal["signal"]
                badge = anal["badge_class"]
        
        st.markdown(f"""
        <div class="symbol-card">
            <strong>{meta['display']}</strong><br>
            <span class="metric-value">{price:,.2f}</span>
            <span style="color:{'#00c853' if pct >= 0 else '#f44336'};"> {pct:+.2f}%</span><br>
            <span class="signal-badge {badge}">{sig}</span>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button(f"View Analysis", key=f"btn_{disp_name}"):
            st.session_state.selected_symbol = disp_name
            st.rerun()

# Detailed View
if st.session_state.selected_symbol:
    selected = st.session_state.selected_symbol
    meta = MAIN_SYMBOLS[selected]
    st.divider()
    st.subheader(f"📊 {selected}")
    
    tf = st.selectbox("Timeframe", ["5m", "15m", "30m", "1h", "4h"], index=2)
    df = fetch_ohlcv(meta["yf_ticker"], interval=tf, period="5d")
    analysis = calculate_technical_signal(df)
    
    if analysis:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Price", f"{analysis['last_price']:,}")
        c2.metric("Technical Signal", analysis['signal'])
        c3.metric("RSI", analysis['rsi'])
        c4.metric("ATR (14)", analysis['atr'])
        
        if analysis['signal'] == "WAIT":
            st.markdown(f"""
            <div class="wait-box">
            <h3>⏳ WAIT - No Clear Setup</h3>
            <p>Technical confluence is low. Better to wait for clear structure.</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("### 🎯 Trade Setup")
            st.code(f"Entry around: {analysis['last_price']}\nUse ATR ({analysis['atr']}) for SL & TP placement.")
        
        # Current Candle
        current = get_current_candle_status(df)
        if current:
            st.markdown("### 📍 Current Market Candle (Real-time)")
            st.markdown(f"""
            <div class="current-candle-box">
            <b>Last Closed:</b> {current['last_closed']}<br>
            <b>Currently Forming:</b> {current['forming_now']}
            </div>
            """, unsafe_allow_html=True)
        
        # Technical Next Candle
        st.markdown("### 🕯️ Next Candle Expectation (Technical)")
        st.info(analysis['expected_candles'])
        if analysis.get('pullback'):
            st.warning(analysis['pullback'])
        
        # Grok Text Analysis
        st.markdown("### 🤖 Grok Text Analysis")
        if st.button("🔍 Analyze Text with Grok", key="grok_btn"):
            with st.spinner("Getting Grok's analysis..."):
                recent = f"Price: {analysis['last_price']}, RSI: {analysis['rsi']}, ATR: {analysis['atr']}"
                grok_response = get_grok_analysis(selected, tf, analysis['signal'], recent)
            st.markdown(f"""
            <div class="ai-box">
            {grok_response}
            </div>
            """, unsafe_allow_html=True)
        
        # Gemini Chart Screenshot Analysis (Vision)
        st.markdown("---")
        st.markdown("### 📸 Gemini AI Chart Screenshot Analysis (Next Candle Predictor)")
        st.write("Current chalne wali candle ki photo upload karein taake Gemini next candle predict kare.")
        
        uploaded_file = st.file_uploader(
            f"Upload {selected} ({tf}) Chart Image", 
            type=["png", "jpg", "jpeg"],
            key="image_uploader"
        )
        
        if uploaded_file is not None:
            image = Image.open(uploaded_file)
            st.image(image, caption=f"Uploaded Chart Screenshot ({tf})", use_container_width=True)
            
            if st.button("🔮 Predict Next Candle with Gemini Vision", key="gemini_btn"):
                with st.spinner("Gemini is analyzing chart candles, price action & predicting next candle..."):
                    gemini_result = analyze_chart_with_gemini(image, selected, tf)
                st.markdown(f"""
                <div class="gemini-box">
                <h4>🔮 Gemini AI Prediction:</h4>
                {gemini_result}
                </div>
                """, unsafe_allow_html=True)
        
        st.markdown("---")
        st.markdown("### 🧠 Technical Reasons")
        for r in analysis['reasons']:
            st.write(r)
            
    else:
        st.error("Not enough data for this timeframe. Try a larger timeframe or wait for market updates.")

st.caption("Technical + Grok + Gemini Vision • Live Candle Prediction")
