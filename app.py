import streamlit as st
import yfinance as yf
from sqlalchemy import create_engine, text

st.title("🛠️ System Diagnostic Tool")

# Test 1: Secrets Check
try:
    db_url = st.secrets["DATABASE_URL"]
    st.success("✅ STEP 1: Secrets Successfully Loaded")
except Exception as e:
    st.error("❌ STEP 1 FAILED: 'DATABASE_URL' Streamlit secrets mein mojood nahi.")
    st.stop()

# Test 2: Database Connection
try:
    if "?sslmode=" not in db_url:
        db_url += "?sslmode=require"
    engine = create_engine(db_url, pool_size=1, max_overflow=1)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    st.success("✅ STEP 2: Neon DB Connected Successfully")
except Exception as e:
    st.error(f"❌ STEP 2 FAILED (DB Error): {str(e)}")
    st.stop()

# Test 3: Yahoo Finance API
try:
    df = yf.download("BTC-USD", period="1d", interval="15m", progress=False)
    if df.empty:
        st.error("❌ STEP 3 FAILED: Yahoo API ne Streamlit IP block kar di hai.")
    else:
        st.success("✅ STEP 3: Live Market Data Fetching Active")
        st.dataframe(df.tail(2))
except Exception as e:
    st.error(f"❌ STEP 3 FAILED (YFinance Error): {str(e)}")
