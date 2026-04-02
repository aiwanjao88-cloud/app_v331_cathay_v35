import os
import requests
import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="V35.3 操盤面板", layout="wide")

# =========================
# LINE 設定
# =========================
def get_secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()

LINE_TOKEN = get_secret("LINE_TOKEN")
LINE_USER_ID = get_secret("LINE_USER_ID")
ENABLE_LINE = bool(LINE_TOKEN and LINE_USER_ID)

def push_line(text: str):
    if not ENABLE_LINE:
        return False, "未設定 LINE_TOKEN / LINE_USER_ID"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }

    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json=payload,
            timeout=10
        )
        if 200 <= r.status_code < 300:
            return True, "LINE 推播成功"
        return False, f"LINE 推播失敗：{r.status_code}"
    except Exception as e:
        return False, f"LINE 推播例外：{e}"

# =========================
# UI
# =========================
st.title("🛡️ V35.3 智能交易儀表板")
st.caption("評分｜今日3檔｜買進區｜停損｜停利｜建議股數｜LINE通知")

watchlist = st.text_input("輸入股票（逗號分隔）", "2330, NVDA, TSLA")
capital = st.number_input("總資金", min_value=10000, value=100000, step=10000)
risk_percent = st.slider("單筆風險比例 (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.5)

col1, col2 = st.columns([1, 1])
scan_clicked = col1.button("開始掃描")
test_line_clicked = col2.button("測試 LINE 推播")

if test_line_clicked:
    ok, msg = push_line("V35.3 測試推播成功")
    if ok:
        st.success(msg)
    else:
        st.warning(msg)

# 先定義，避免 NameError
results = []

if scan_clicked:
    # 自動轉換台股代碼
    symbols = []
    for x in watchlist.split(","):
        s = x.strip().upper()
        if not s:
            continue
        if s.isdigit():
            s = s + ".TW"
        symbols.append(s)

    for s in symbols:
        try:
            df = yf.download(s, period="6mo", progress=False, auto_adjust=True)

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

            # 評分系統
            score = 0
            if price > ma20:
                score += 40
            if ma20 > ma60:
                score += 30
            if price > ma60:
                score += 20
            if close.iloc[-1] > close.iloc[-5]:
                score += 10

            # 狀態燈號
            if score >= 80:
                status = "🟢強勢"
            elif price < ma20:
                status = "🔴弱勢"
            else:
                status = "🟡觀望"

            # 買進區 / 停損 / 停利
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
            continue

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

        # LINE 推播今日3檔
        if ENABLE_LINE:
            msg_lines = ["🔥 V35.3 今日最強3檔"]
            for _, row in top3.iterrows():
                msg_lines.append(
                    f"{row['股票']} | {row['狀態']} | 現價:{row['現價']} | 停損:{row['停損價']} | 停利:{row['停利價']}"
                )
            ok, msg = push_line("\n".join(msg_lines))
            if ok:
                st.success("已推播今日最強3檔到 LINE")
            else:
                st.warning(msg)

    else:
        st.warning("查無資料，請確認股票代碼。")
