import os
import requests
import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="V35.6 訊號燈號強化版", layout="wide")

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

    if any(ord(ch) > 127 for ch in LINE_TOKEN) or any(ord(ch) > 127 for ch in LINE_USER_ID):
        return False, "LINE_TOKEN / LINE_USER_ID 內含非英文字符，請檢查 secrets.toml"

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {LINE_TOKEN}"
    }

    payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
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
        return False, f"LINE 推播失敗：{r.status_code} | {r.text[:200]}"
    except Exception as e:
        return False, f"LINE 推播例外：{e}"

# =========================
# 工具函式
# =========================
def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    for c in required_cols:
        if c not in out.columns:
            return pd.DataFrame()

    out = out.dropna(subset=["Close"])
    return out

def fetch_symbol_data(symbol: str, period: str = "6mo") -> tuple[pd.DataFrame, str, str]:
    symbol = symbol.strip().upper()

    if symbol.isdigit():
        for suffix in [".TW", ".TWO"]:
            real_symbol = symbol + suffix
            try:
                df = yf.download(real_symbol, period=period, progress=False, auto_adjust=True)
                df = normalize_df(df)
                if not df.empty and len(df) >= 60:
                    return df, real_symbol, "台股"
            except Exception:
                pass
        return pd.DataFrame(), symbol, "台股"

    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        df = normalize_df(df)
        if not df.empty and len(df) >= 60:
            return df, symbol, "美股"
    except Exception:
        pass

    return pd.DataFrame(), symbol, "美股"

def calc_signal_from_df(df: pd.DataFrame, symbol: str, market: str, capital: float, risk_percent: float):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    price = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])

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
        signal = "🟢 可打"
        signal_color = "green"
    elif score >= 50:
        signal = "🟡 觀望"
        signal_color = "yellow"
    else:
        signal = "🔴 避開"
        signal_color = "red"

    buy_zone_low = round(ma20 * 0.99, 2)
    buy_zone_high = round(ma20 * 1.01, 2)
    stop_loss = round(ma20 - atr14, 2)
    take_profit = round(price + (price - stop_loss) * 2, 2)

    risk_amount = capital * (risk_percent / 100)
    per_share_risk = max(price - stop_loss, 0.01)
    suggested_shares = int(risk_amount / per_share_risk)

    return {
        "市場": market,
        "股票": symbol,
        "現價": round(price, 2),
        "20MA": round(ma20, 2),
        "60MA": round(ma60, 2),
        "ATR14": round(atr14, 2),
        "評分": score,
        "訊號": signal,
        "燈號": signal_color,
        "買進區下緣": buy_zone_low,
        "買進區上緣": buy_zone_high,
        "停損價": stop_loss,
        "停利價": take_profit,
        "建議股數": suggested_shares
    }

# =========================
# UI
# =========================
st.title("🛡️ V35.6 訊號燈號強化版")
st.caption("紅綠燈判斷｜台股 / 美股分流｜國泰手動下單｜LINE通知")

watchlist = st.text_input("輸入股票（逗號分隔）", "2330, 2317, NVDA, TSLA, AMD")
capital = st.number_input("總資金", min_value=10000, value=100000, step=10000)
risk_percent = st.slider("單筆風險比例 (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.5)
only_tradeable = st.checkbox("只顯示 🟢 可打", value=False)

col1, col2 = st.columns([1, 1])
scan_clicked = col1.button("開始掃描")
test_line_clicked = col2.button("測試 LINE 推播")

if test_line_clicked:
    ok, msg = push_line("V35.6 測試推播成功")
    if ok:
        st.success(msg)
    else:
        st.warning(msg)

results = []
failed_symbols = []

if scan_clicked:
    symbols = [x.strip().upper() for x in watchlist.split(",") if x.strip()]

    for s in symbols:
        df, real_symbol, market = fetch_symbol_data(s)

        if df.empty:
            failed_symbols.append(s)
            continue

        try:
            row = calc_signal_from_df(df, real_symbol, market, capital, risk_percent)
            results.append(row)
        except Exception:
            failed_symbols.append(s)

    if results:
        df_show = pd.DataFrame(results)

        if only_tradeable:
            df_show = df_show[df_show["訊號"] == "🟢 可打"]

        green_count = (df_show["訊號"] == "🟢 可打").sum() if not df_show.empty else 0
        yellow_count = (df_show["訊號"] == "🟡 觀望").sum() if not df_show.empty else 0
        red_count = (df_show["訊號"] == "🔴 避開").sum() if not df_show.empty else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("🟢 可打", int(green_count))
        c2.metric("🟡 觀望", int(yellow_count))
        c3.metric("🔴 避開", int(red_count))

        tab1, tab2, tab3 = st.tabs(["📊 全部", "🇹🇼 台股", "🇺🇸 美股"])

        with tab1:
            st.subheader("全部掃描結果")
            st.dataframe(df_show, use_container_width=True)

        with tab2:
            tw_df = df_show[df_show["市場"] == "台股"]
            if not tw_df.empty:
                st.dataframe(tw_df, use_container_width=True)
            else:
                st.info("目前沒有台股結果")

        with tab3:
            us_df = df_show[df_show["市場"] == "美股"]
            if not us_df.empty:
                st.dataframe(us_df, use_container_width=True)
            else:
                st.info("目前沒有美股結果")

        tradable = df_show[df_show["訊號"] == "🟢 可打"].sort_values(["評分", "現價"], ascending=[False, True])

        st.subheader("🔥 今日可打名單")
        if not tradable.empty:
            st.dataframe(tradable, use_container_width=True)
        else:
            st.info("今天沒有明確可打標的")

        top3 = tradable.head(3)

        if not top3.empty:
            st.subheader("🎯 今日最強3檔")

            cols = st.columns(min(3, len(top3)))
            for i, (_, row) in enumerate(top3.iterrows()):
                with cols[i]:
                    st.markdown(
                        f"""
                        ### {row['股票']}
                        - 市場：**{row['市場']}**
                        - 訊號：**{row['訊號']}**
                        - 現價：**{row['現價']}**
                        - 買進區：**{row['買進區下緣']} ~ {row['買進區上緣']}**
                        - 停損：**{row['停損價']}**
                        - 停利：**{row['停利價']}**
                        - 建議股數：**{row['建議股數']}**
                        - 評分：**{row['評分']}**
                        """
                    )

            st.subheader("💰 國泰手動下單面板")
            selected_symbol = st.selectbox("選擇下單標的", top3["股票"].tolist())
            selected_row = top3[top3["股票"] == selected_symbol].iloc[0]

            order_price = st.number_input("下單價格", value=float(selected_row["現價"]), step=0.1)
            order_qty = st.number_input("下單股數", value=int(selected_row["建議股數"]), step=1, min_value=1)
            order_action = st.selectbox("操作", ["BUY 買進", "SELL 賣出", "REDUCE 減碼"])

            st.markdown("### 🧾 下單摘要")
            st.write(f"市場：{selected_row['市場']}")
            st.write(f"標的：{selected_symbol}")
            st.write(f"操作：{order_action}")
            st.write(f"價格：{order_price}")
            st.write(f"股數：{order_qty}")
            st.write(f"停損：{selected_row['停損價']}")
            st.write(f"停利：{selected_row['停利價']}")

            if st.button("推播下單摘要到 LINE"):
                msg = (
                    f"🏛️ V35.6 國泰手動下單摘要\n"
                    f"市場：{selected_row['市場']}\n"
                    f"標的：{selected_symbol}\n"
                    f"訊號：{selected_row['訊號']}\n"
                    f"操作：{order_action}\n"
                    f"價格：{order_price}\n"
                    f"股數：{order_qty}\n"
                    f"停損：{selected_row['停損價']}\n"
                    f"停利：{selected_row['停利價']}"
                )
                ok, line_msg = push_line(msg)
                if ok:
                    st.success("已推播到 LINE")
                else:
                    st.warning(line_msg)

            if ENABLE_LINE:
                msg_lines = ["🚦 V35.6 今日最強3檔"]
                for _, row in top3.iterrows():
                    msg_lines.append(
                        f"{row['市場']} | {row['股票']} | {row['訊號']} | 現價:{row['現價']} | 停損:{row['停損價']} | 停利:{row['停利價']}"
                    )
                push_line("\n".join(msg_lines))

    else:
        st.warning("查無可用資料。")

    if failed_symbols:
        st.subheader("⚠️ 無法取得資料的股票")
        st.write(", ".join(failed_symbols))
