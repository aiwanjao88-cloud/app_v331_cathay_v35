import os
import json
import requests
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="V36.2 自動篩選器版", layout="wide")

WATCHLIST_FILE = "watchlist_memory.json"

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
        return False, "LINE_TOKEN / LINE_USER_ID 內含非英文字符"

    headers = {
        "Content-Type": "application/json; charset=utf-8",
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
# watchlist 記憶
# =========================
def load_watchlist_memory(default_value: str) -> str:
    try:
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("watchlist", default_value)
    except Exception:
        pass
    return default_value

def save_watchlist_memory(watchlist: str):
    try:
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"watchlist": watchlist}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# =========================
# 工具
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

def fetch_symbol_data(symbol: str, period: str = "1y") -> tuple[pd.DataFrame, str, str]:
    symbol = symbol.strip().upper()

    if symbol.isdigit():
        for suffix in [".TW", ".TWO"]:
            real_symbol = symbol + suffix
            try:
                df = yf.download(real_symbol, period=period, progress=False, auto_adjust=True)
                df = normalize_df(df)
                if not df.empty and len(df) >= 100:
                    return df, real_symbol, "台股"
            except Exception:
                pass
        return pd.DataFrame(), symbol, "台股"

    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        df = normalize_df(df)
        if not df.empty and len(df) >= 100:
            return df, symbol, "美股"
    except Exception:
        pass

    return pd.DataFrame(), symbol, "美股"

def calc_kd(df: pd.DataFrame, n=9):
    low_min = df["Low"].rolling(n).min()
    high_max = df["High"].rolling(n).max()
    rsv = (df["Close"] - low_min) / (high_max - low_min + 1e-9) * 100
    k = rsv.ewm(com=2).mean()
    d = k.ewm(com=2).mean()
    return float(k.iloc[-1]), float(d.iloc[-1])

def get_fundamental_data(symbol: str):
    """
    盡量抓，但不保證每檔都有完整資料
    """
    try:
        tk = yf.Ticker(symbol)
        info = tk.info if tk.info else {}
    except Exception:
        info = {}

    pe = info.get("trailingPE", None)
    market_cap = info.get("marketCap", None)
    profit_margin = info.get("profitMargins", None)
    revenue_growth = info.get("revenueGrowth", None)
    earnings_growth = info.get("earningsGrowth", None)

    # 股本替代估法：market cap 有抓到就當作流動性/規模參考
    return {
        "pe": pe,
        "market_cap": market_cap,
        "profit_margin": profit_margin,
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth
    }

def calc_screening_signal(df: pd.DataFrame, symbol: str, market: str, capital: float, risk_percent: float):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    price = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])

    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    latest_vol = float(vol.iloc[-1])

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])

    k, d = calc_kd(df)

    # 旗竿概念：近20日漲幅
    flagpole_return = (float(close.iloc[-1]) / float(close.iloc[-20]) - 1) * 100 if len(close) >= 20 else 0

    # 整理突破概念：近10日高點突破
    breakout_10 = price >= float(high.rolling(10).max().iloc[-1]) * 0.995

    # 基本面
    f = get_fundamental_data(symbol)

    fundamental_score = 0
    notes = []

    if f["profit_margin"] is not None and f["profit_margin"] > 0:
        fundamental_score += 15
        notes.append("獲利能力正向")

    if f["revenue_growth"] is not None and f["revenue_growth"] > 0:
        fundamental_score += 15
        notes.append("營收成長正向")

    if f["earnings_growth"] is not None and f["earnings_growth"] > 0:
        fundamental_score += 10
        notes.append("EPS成長正向")

    if f["pe"] is not None and f["pe"] > 0 and f["pe"] < 15:
        fundamental_score += 10
        notes.append("本益比相對合理")

    if f["market_cap"] is not None and f["market_cap"] > 3e9:
        fundamental_score += 10
        notes.append("規模/流動性較佳")

    # 技術面
    technical_score = 0

    if latest_vol > avg_vol20 * 1.5:
        technical_score += 20
        notes.append("近期爆量")

    if price > ma20 and ma20 > ma60:
        technical_score += 20
        notes.append("站上中長均線")

    if k > d and k > 50:
        technical_score += 10
        notes.append("KD 黃金交叉偏強")

    if k > 80:
        technical_score += 10
        notes.append("KD 高檔鈍化")

    if breakout_10:
        technical_score += 15
        notes.append("接近突破整理區")

    if flagpole_return > 15:
        technical_score += 10
        notes.append("旗竿動能明顯")

    total_score = fundamental_score + technical_score

    # 訊號燈號
    if total_score >= 70:
        signal = "🟢 可打"
    elif total_score >= 45:
        signal = "🟡 觀望"
    else:
        signal = "🔴 避開"

    buy_zone_low = round(ma20 * 0.99, 2)
    buy_zone_high = round(ma20 * 1.01, 2)
    stop_loss = round(ma20 - atr14, 2)
    take_profit = round(price + (price - stop_loss) * 2, 2)

    risk_amount = capital * (risk_percent / 100)
    per_share_risk = max(price - stop_loss, 0.01)
    suggested_shares = int(risk_amount / per_share_risk)

    # 提醒
    if buy_zone_low <= price <= buy_zone_high and signal == "🟢 可打":
        alert = "✅ 接近買點"
    elif price < stop_loss:
        alert = "🚨 跌破停損"
    elif price >= take_profit:
        alert = "🎯 到達停利觀察"
    elif signal == "🟢 可打":
        alert = "👀 可列入建倉"
    elif signal == "🟡 觀望":
        alert = "⏳ 等待確認"
    else:
        alert = "⛔ 先避開"

    return {
        "市場": market,
        "股票": symbol,
        "現價": round(price, 2),
        "20MA": round(ma20, 2),
        "60MA": round(ma60, 2),
        "量能": round(latest_vol, 0),
        "20日均量": round(avg_vol20, 0),
        "KD_K": round(k, 2),
        "KD_D": round(d, 2),
        "旗竿漲幅%": round(flagpole_return, 2),
        "基本面分": fundamental_score,
        "技術面分": technical_score,
        "總分": total_score,
        "訊號": signal,
        "提醒": alert,
        "買進區下緣": buy_zone_low,
        "買進區上緣": buy_zone_high,
        "停損價": stop_loss,
        "停利價": take_profit,
        "建議股數": suggested_shares,
        "備註": " / ".join(notes[:5]) if notes else "資料有限"
    }

# =========================
# 自動篩選候選池
# =========================
TW_CANDIDATES = ["2330", "2317", "2454", "2382", "2303", "3037", "2376", "3017", "2603", "2609"]
US_CANDIDATES = ["NVDA", "AMD", "TSLA", "PLTR", "SMCI", "META", "AMZN", "AVGO", "MSFT", "QQQ"]

# =========================
# 預設狀態
# =========================
DEFAULT_WATCHLIST = "2330, 2317, NVDA, TSLA, AMD"
if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist_memory(DEFAULT_WATCHLIST)

if "capital" not in st.session_state:
    st.session_state.capital = 100000

if "risk_percent" not in st.session_state:
    st.session_state.risk_percent = 1.0

if "only_tradeable" not in st.session_state:
    st.session_state.only_tradeable = False

# =========================
# UI
# =========================
st.title("🛡️ V36.2 自動篩選器版")
st.caption("監控清單 + 台美股自動篩選器｜飆股綜合條件｜國泰手動下單")

st.subheader("📌 監控清單")
watchlist = st.text_input("輸入股票（逗號分隔）", value=st.session_state.watchlist)
save_watchlist_memory(watchlist)
st.session_state.watchlist = watchlist

st.subheader("⚙️ 資金模式")
c1, c2, c3, c4 = st.columns(4)

if c1.button("標準模式"):
    st.session_state.capital = 100000
    st.session_state.risk_percent = 1.0
    st.session_state.only_tradeable = False

if c2.button("小資可進攻 20K"):
    st.session_state.capital = 20000
    st.session_state.risk_percent = 2.0
    st.session_state.only_tradeable = True

if c3.button("進攻 30K"):
    st.session_state.capital = 30000
    st.session_state.risk_percent = 2.0
    st.session_state.only_tradeable = True

if c4.button("積極 50K"):
    st.session_state.capital = 50000
    st.session_state.risk_percent = 1.5
    st.session_state.only_tradeable = True

capital = st.number_input("總資金", min_value=10000, value=int(st.session_state.capital), step=10000)
risk_percent = st.slider("單筆風險比例 (%)", min_value=0.5, max_value=5.0, value=float(st.session_state.risk_percent), step=0.5)
only_tradeable = st.checkbox("只顯示 🟢 可打", value=st.session_state.only_tradeable)

st.session_state.capital = capital
st.session_state.risk_percent = risk_percent
st.session_state.only_tradeable = only_tradeable

st.subheader("🔎 篩選模式")
m1, m2, m3, m4 = st.columns(4)
scan_watchlist = m1.button("掃描監控清單")
scan_tw_auto = m2.button("台股自動篩選")
scan_us_auto = m3.button("美股自動篩選")
test_line_clicked = m4.button("測試 LINE 推播")

if test_line_clicked:
    ok, msg = push_line("V36.2 測試推播成功")
    if ok:
        st.success(msg)
    else:
        st.warning(msg)

results = []
failed_symbols = []

symbols_to_scan = []

if scan_watchlist:
    symbols_to_scan = [x.strip().upper() for x in watchlist.split(",") if x.strip()]
elif scan_tw_auto:
    symbols_to_scan = TW_CANDIDATES
elif scan_us_auto:
    symbols_to_scan = US_CANDIDATES

if symbols_to_scan:
    for s in symbols_to_scan:
        df, real_symbol, market = fetch_symbol_data(s)

        if df.empty:
            failed_symbols.append(s)
            continue

        try:
            row = calc_screening_signal(df, real_symbol, market, capital, risk_percent)
            results.append(row)
        except Exception:
            failed_symbols.append(s)

    if results:
        df_show = pd.DataFrame(results)

        if only_tradeable:
            df_show = df_show[df_show["訊號"] == "🟢 可打"]

        c1, c2, c3 = st.columns(3)
        c1.metric("🟢 可打", int((df_show["訊號"] == "🟢 可打").sum()))
        c2.metric("🟡 觀望", int((df_show["訊號"] == "🟡 觀望").sum()))
        c3.metric("🔴 避開", int((df_show["訊號"] == "🔴 避開").sum()))

        st.subheader("📊 掃描結果")
        st.dataframe(df_show, use_container_width=True)

        tradable = df_show[df_show["訊號"] == "🟢 可打"].sort_values(["總分", "現價"], ascending=[False, True])

        st.subheader("🔥 今日可打名單")
        if not tradable.empty:
            st.dataframe(tradable, use_container_width=True)
        else:
            st.info("今天沒有符合條件的可打標的")

        top3 = tradable.head(3)
        if not top3.empty:
            st.subheader("🎯 今日最強3檔")
            st.dataframe(top3, use_container_width=True)

            st.subheader("💰 國泰手動下單面板")
            selected_symbol = st.selectbox("選擇下單標的", top3["股票"].tolist())
            selected_row = top3[top3["股票"] == selected_symbol].iloc[0]

            order_price = st.number_input("下單價格", value=float(selected_row["現價"]), step=0.1)
            order_qty = st.number_input("下單股數", value=max(int(selected_row["建議股數"]), 1), step=1, min_value=1)
            order_action = st.selectbox("操作", ["BUY 買進", "SELL 賣出", "REDUCE 減碼"])

            st.markdown("### 🧾 下單摘要")
            st.write(f"市場：{selected_row['市場']}")
            st.write(f"標的：{selected_symbol}")
            st.write(f"訊號：{selected_row['訊號']}")
            st.write(f"提醒：{selected_row['提醒']}")
            st.write(f"操作：{order_action}")
            st.write(f"價格：{order_price}")
            st.write(f"股數：{order_qty}")
            st.write(f"停損：{selected_row['停損價']}")
            st.write(f"停利：{selected_row['停利價']}")
            st.write(f"備註：{selected_row['備註']}")

            if st.button("推播下單摘要到 LINE"):
                msg = (
                    f"🏛️ V36.2 國泰手動下單摘要\n"
                    f"市場：{selected_row['市場']}\n"
                    f"標的：{selected_symbol}\n"
                    f"訊號：{selected_row['訊號']}\n"
                    f"提醒：{selected_row['提醒']}\n"
                    f"操作：{order_action}\n"
                    f"價格：{order_price}\n"
                    f"股數：{order_qty}\n"
                    f"停損：{selected_row['停損價']}\n"
                    f"停利：{selected_row['停利價']}\n"
                    f"備註：{selected_row['備註']}"
                )
                ok, line_msg = push_line(msg)
                if ok:
                    st.success("已推播到 LINE")
                else:
                    st.warning(line_msg)

    else:
        st.warning("查無可用資料。")

    if failed_symbols:
        st.subheader("⚠️ 無法取得資料的股票")
        st.write(", ".join(failed_symbols))
