import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="V35.2 操盤面板", layout="wide")

st.title("🛡️ V35.2 智能交易儀表板")
st.caption("評分｜今日3檔｜買進區｜停損｜停利｜建議股數")

# 可調參數
watchlist = st.text_input("輸入股票（逗號分隔）", "2330, NVDA, TSLA")
capital = st.number_input("總資金", min_value=10000, value=100000, step=10000)
risk_percent = st.slider("單筆風險比例 (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.5)

if st.button("開始掃描"):
    symbols = []
for x in watchlist.split(","):
    s = x.strip().upper()
    if s.isdigit():   # 台股
        s = s + ".TW"
    symbols.append(s)
    results = []

    for s in symbols:
        try:
            df = yf.download(s, period="6mo", progress=False)

            if df.empty or len(df) < 60:
                continue

            close = df["Close"]
            high = df["High"]
            low = df["Low"]

            price = float(close.iloc[-1])
            ma20 = float(close.rolling(20).mean().iloc[-1])
            ma60 = float(close.rolling(60).mean().iloc[-1])

            # ATR 簡化算法
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr14 = float(tr.rolling(14).mean().iloc[-1])

            score = 0
            if price > ma20:
                score += 40
            if ma20 > ma60:
                score += 30
            if price > ma60:
                score += 20
            if close.iloc[-1] > close.iloc[-5]:
                score += 10

            if score >= 80:
                status = "🟢強勢"
            elif price < ma20:
                status = "🔴弱勢"
            else:
                status = "🟡觀望"

            # 實戰價格
            buy_zone_low = round(ma20 * 0.99, 2)
            buy_zone_high = round(ma20 * 1.01, 2)
            stop_loss = round(ma20 - atr14, 2)
            take_profit = round(price + (price - stop_loss) * 2, 2)

            # 單筆風險計算
            risk_amount = capital * (risk_percent / 100)
            per_share_risk = max(price - stop_loss, 0.01)
            suggested_shares = int(risk_amount / per_share_risk)

            results.append({
                "股票": s,
                "現價": round(price, 2),
                "20MA": round(ma20, 2),
                "60MA": round(ma60, 2),
                "ATR14": round(atr14, 2),
                "評分": score,
                "狀態": status,
                "買進區下緣": buy_zone_low,
                "買進區上緣": buy_zone_high,
                "停損價": stop_loss,
                "停利價": take_profit,
                "建議股數": suggested_shares
            })

        except Exception:
            pass

    if results:
        df_show = pd.DataFrame(results)

        st.subheader("📊 全部掃描結果")
        st.dataframe(df_show, use_container_width=True)

        top3 = df_show.sort_values(["評分", "現價"], ascending=[False, True]).head(3)

        st.subheader("🔥 今日最強3檔")
        st.dataframe(top3, use_container_width=True)

        st.subheader("🎯 今日操作重點")
        for _, row in top3.iterrows():
            st.markdown(
                f"""
                ### {row['股票']}｜{row['狀態']}
                - 現價：**{row['現價']}**
                - 買進區：**{row['買進區下緣']} ~ {row['買進區上緣']}**
                - 停損價：**{row['停損價']}**
                - 停利價：**{row['停利價']}**
                - 建議股數：**{row['建議股數']}**
                - 評分：**{row['評分']}**
                """
            )
    else:
        st.warning("查無資料，請確認股票代碼。")
