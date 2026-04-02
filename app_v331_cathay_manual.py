import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="V35 操盤面板", layout="wide")

st.title("🛡️ V35 智能交易儀表板")

watchlist = st.text_input("輸入股票（逗號分隔）", "2330, NVDA, TSLA")

if st.button("開始掃描"):
    symbols = [x.strip() for x in watchlist.split(",")]

    results = []

    for s in symbols:
        try:
            df = yf.download(s, period="3mo", progress=False)

            if not df.empty:
                price = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                ma60 = df["Close"].rolling(60).mean().iloc[-1]

                # 🔥 評分系統
                score = 0
                if price > ma20:
                    score += 50
                if ma20 > ma60:
                    score += 50

                # 🔥 狀態判斷
                if score >= 80:
                    status = "🟢強勢"
                elif price < ma20:
                    status = "🔴弱勢"
                else:
                    status = "🟡觀望"

                results.append({
                    "股票": s,
                    "現價": round(price,2),
                    "20MA": round(ma20,2),
                    "60MA": round(ma60,2),
                    "評分": score,
                    "狀態": status
                })

        except:
            pass

    if results:
        df_show = pd.DataFrame(results)

        st.subheader("📊 全部掃描")
        st.dataframe(df_show, use_container_width=True)

        # 🔥 今日最強3檔
        top3 = df_show.sort_values("評分", ascending=False).head(3)

        st.subheader("🔥 今日最強3檔")
        st.dataframe(top3, use_container_width=True)

    else:
        st.warning("查無資料")
