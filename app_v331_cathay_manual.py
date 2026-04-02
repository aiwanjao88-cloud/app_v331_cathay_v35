import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="V35 操盤面板", layout="wide")

st.title("🛡️ V35 智能交易儀表板")

watchlist = st.text_input("輸入股票（逗號分隔）", "2330, NVDA, TSLA")

if st.button("開始掃描"):
    symbols = [x.strip() for x in watchlist.split(",")]

    data = []
    for s in symbols:
        try:
            df = yf.download(s, period="3mo", progress=False)
            if not df.empty:
                price = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]

                status = "🟢強勢" if price > ma20 else "🔴弱勢"

                data.append({
                    "股票": s,
                    "現價": round(price,2),
                    "20MA": round(ma20,2),
                    "狀態": status
                })
        except:
            pass

    if data:
        st.dataframe(pd.DataFrame(data), use_container_width=True)
    else:
        st.warning("查無資料")
