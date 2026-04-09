from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="V38.1 手機版操盤系統", page_icon="📱", layout="wide")

# =========================
# 預設值
# =========================
TW_DEFAULTS = ["2330.TW", "2317.TW", "2382.TW", "2454.TW", "2308.TW", "2603.TW"]
US_DEFAULTS = ["NVDA", "TSM", "QQQM", "SMH", "AVGO", "MSFT"]

DEFAULT_CAPITAL = 200000
DEFAULT_MAX_POSITIONS = 2
DEFAULT_SINGLE_POSITION_PCT = 0.30
DEFAULT_STOP_LOSS_PCT = 0.05
DEFAULT_TAKE_PROFIT_PCT = 0.10
DEFAULT_DAILY_LOSS_STOP_PCT = 0.05


# =========================
# 工具函式
# =========================
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_symbols(raw: str) -> List[str]:
    items = []
    for x in raw.replace("\n", ",").split(","):
        s = x.strip().upper()
        if s:
            items.append(s)

    seen = set()
    out = []
    for s in items:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return (100 - (100 / (1 + rs))).fillna(0)


@st.cache_data(ttl=300)
def fetch_price_history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False)

    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
    df = df[keep].copy()

    if "Close" not in df.columns:
        return pd.DataFrame()

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean() if "Volume" in df.columns else 0
    df["RSI14"] = rsi(df["Close"], 14)
    df["Prev20High"] = df["High"].rolling(20).max().shift(1)

    df["Signal_Breakout"] = (df["Close"] > df["Prev20High"]) & (df["Volume"] > df["VOL20"])
    df["Signal_Trend"] = (df["Close"] > df["MA5"]) & (df["MA5"] > df["MA60"])
    df["Signal_Pullback"] = (
        (df["Low"] <= df["MA5"]) &
        (df["Close"] > df["MA5"]) &
        (df["MA5"] > df["MA60"])
    )

    return df.dropna(how="all")


def calc_signal(df: pd.DataFrame, stop_loss_pct: float, take_profit_pct: float) -> Dict[str, object]:
    if df.empty or len(df) < 65:
        return {
            "signal": "資料不足",
            "entry": None,
            "stop": None,
            "tp1": None,
            "score": -999,
            "reason": "資料不足",
            "close": None,
        }

    last = df.iloc[-1]

    close = float(last["Close"])
    ma5 = float(last["MA5"]) if pd.notna(last["MA5"]) else None
    ma60 = float(last["MA60"]) if pd.notna(last["MA60"]) else None
    vol = float(last["Volume"]) if pd.notna(last["Volume"]) else 0
    vol20 = float(last["VOL20"]) if pd.notna(last["VOL20"]) else 0
    prev20h = float(last["Prev20High"]) if pd.notna(last["Prev20High"]) else None
    r = float(last["RSI14"]) if pd.notna(last["RSI14"]) else 0

    trend_ok = ma5 is not None and ma60 is not None and close > ma5 > ma60
    breakout = bool(last["Signal_Breakout"])
    pullback = bool(last["Signal_Pullback"])

    score = 0
    reasons = []

    if trend_ok:
        score += 40
        reasons.append("5MA>60MA 且收盤站上5MA")

    if breakout:
        score += 35
        reasons.append("突破20日高點且量增")

    if pullback:
        score += 15
        reasons.append("回踩5MA承接")

    if vol20 > 0 and vol > vol20:
        score += 5

    if 50 <= r <= 78:
        score += 10
        reasons.append(f"RSI14={r:.1f}")
    elif r > 80:
        score -= 5
        reasons.append(f"RSI14={r:.1f} 偏熱")

    signal = "觀察"
    entry = None

    if breakout and trend_ok:
        signal = "突破買進"
        entry = round(max(close, prev20h or close), 2)
    elif pullback and trend_ok:
        signal = "5MA承接"
        entry = round(ma5, 2) if ma5 else round(close, 2)
    elif trend_ok:
        signal = "趨勢續抱"
        entry = round(close, 2)
    else:
        score -= 15
        reasons.append("未形成多頭趨勢")

    return {
        "signal": signal,
        "entry": entry,
        "stop": round(close * (1 - stop_loss_pct), 2),
        "tp1": round(close * (1 + take_profit_pct), 2),
        "score": score,
        "reason": "；".join(reasons),
        "close": round(close, 2),
    }


def recommend_qty(capital: float, alloc_pct: float, entry: Optional[float], market: str) -> Tuple[float, int]:
    budget = capital * alloc_pct
    if not entry or entry <= 0:
        return budget, 0

    if market == "台股":
        qty = int(budget // (entry * 1000))
        return budget, max(qty, 0)

    qty = int(budget // entry)
    return budget, max(qty, 0)


def make_order_df(top_df: pd.DataFrame, capital: float, alloc_pct: float) -> pd.DataFrame:
    rows = []
    for _, row in top_df.iterrows():
        budget, qty = recommend_qty(capital, alloc_pct, row["建議進場價"], row["市場"])
        rows.append({
            "市場": row["市場"],
            "代碼": row["代碼"],
            "訊號": row["訊號"],
            "委託類型": "現股/限價" if row["市場"] == "台股" else "複委託/限價",
            "建議進場價": row["建議進場價"],
            "停損價": row["停損價"],
            "第一停利價": row["第一停利價"],
            "配置金額": round(budget, 2),
            "建議數量": f"{qty} 張" if row["市場"] == "台股" else f"{qty} 股",
        })
    return pd.DataFrame(rows)


def line_enabled() -> bool:
    return "line_channel_access_token" in st.secrets and "line_to" in st.secrets


def send_line(text: str) -> Tuple[bool, str]:
    if not line_enabled():
        return False, "尚未設定 line_channel_access_token / line_to"

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {st.secrets['line_channel_access_token']}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": st.secrets["line_to"],
        "messages": [{"type": "text", "text": text[:5000]}],
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if 200 <= r.status_code < 300:
            return True, "LINE 推播成功"
        return False, f"推播失敗：HTTP {r.status_code} / {r.text[:180]}"
    except Exception as e:
        return False, f"推播失敗：{e}"


def build_alert_text(top_df: pd.DataFrame) -> str:
    lines = [f"V38.1 手機版訊號 {now_str()}"]
    if top_df.empty:
        lines.append("目前沒有可用訊號。")
        return "\n".join(lines)

    for _, row in top_df.iterrows():
        lines.append(
            f"{row['代碼']}｜{row['訊號']}｜進場 {row['建議進場價']}｜停損 {row['停損價']}｜停利 {row['第一停利價']}"
        )
    return "\n".join(lines)


def draw_chart_no_plotly(df: pd.DataFrame, symbol: str):
    if df.empty:
        st.warning(f"{symbol} 無資料")
        return

    st.markdown(f"**{symbol} 走勢圖**")

    chart_df = pd.DataFrame(index=df.index)
    chart_df["Close"] = df["Close"]
    chart_df["MA5"] = df["MA5"]
    chart_df["MA20"] = df["MA20"]
    chart_df["MA60"] = df["MA60"]

    st.line_chart(chart_df, use_container_width=True)

    latest = df.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("收盤", f"{float(latest['Close']):.2f}")
    c2.metric("MA5", f"{float(latest['MA5']):.2f}" if pd.notna(latest["MA5"]) else "-")
    c3.metric("MA60", f"{float(latest['MA60']):.2f}" if pd.notna(latest["MA60"]) else "-")
    c4.metric("RSI14", f"{float(latest['RSI14']):.2f}" if pd.notna(latest["RSI14"]) else "-")


def init_state():
    st.session_state.setdefault("scan_df", pd.DataFrame())
    st.session_state.setdefault("top3_df", pd.DataFrame())
    st.session_state.setdefault("order_df", pd.DataFrame())
    st.session_state.setdefault("df_map", {})
    st.session_state.setdefault("positions", [])
    st.session_state.setdefault("trade_log", [])


def save_positions(rows: List[Dict]):
    st.session_state["positions"] = rows


def save_trade_log(rows: List[Dict]):
    st.session_state["trade_log"] = rows


def positions_df() -> pd.DataFrame:
    rows = st.session_state.get("positions", [])
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=[
        "市場", "代碼", "持有數量", "成本價", "目前價", "報酬率%", "停損價", "第一停利價", "狀態"
    ])


def trade_log_df() -> pd.DataFrame:
    rows = st.session_state.get("trade_log", [])
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=[
        "日期", "市場", "代碼", "動作", "價格", "數量", "備註"
    ])


def update_position_prices(df_map: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    pos = positions_df()
    if pos.empty:
        return pos

    out = pos.copy()
    current_prices = []
    returns = []
    status = []

    for _, row in out.iterrows():
        symbol = row["代碼"]
        current = None
        if symbol in df_map and not df_map[symbol].empty:
            current = float(df_map[symbol]["Close"].iloc[-1])

        current_prices.append(current)

        try:
            cost = float(row["成本價"]) if row["成本價"] not in [None, ""] else None
        except Exception:
            cost = None

        ret = ((current / cost) - 1) * 100 if current and cost else None
        returns.append(round(ret, 2) if ret is not None else None)

        stop = row["停損價"]
        tp1 = row["第一停利價"]

        s = "持有中"
        try:
            if current and stop not in [None, ""] and current <= float(stop):
                s = "觸發停損"
            elif current and tp1 not in [None, ""] and current >= float(tp1):
                s = "到達停利一"
        except Exception:
            s = "持有中"

        status.append(s)

    out["目前價"] = current_prices
    out["報酬率%"] = returns
    out["狀態"] = status

    save_positions(out.fillna("").to_dict("records"))
    return out


def run_scan(
    symbols_tw: List[str],
    symbols_us: List[str],
    period: str,
    stop_loss_pct: float,
    take_profit_pct: float,
    capital: float,
    alloc_pct: float,
):
    results = []
    df_map = {}

    for symbol in symbols_tw:
        df = fetch_price_history(symbol, period=period)
        df_map[symbol] = df
        if not df.empty:
            sig = calc_signal(df, stop_loss_pct, take_profit_pct)
            results.append({
                "市場": "台股",
                "代碼": symbol,
                "收盤": sig["close"],
                "訊號": sig["signal"],
                "建議進場價": sig["entry"],
                "停損價": sig["stop"],
                "第一停利價": sig["tp1"],
                "評分": sig["score"],
                "理由": sig["reason"],
            })

    for symbol in symbols_us:
        df = fetch_price_history(symbol, period=period)
        df_map[symbol] = df
        if not df.empty:
            sig = calc_signal(df, stop_loss_pct, take_profit_pct)
            results.append({
                "市場": "美股",
                "代碼": symbol,
                "收盤": sig["close"],
                "訊號": sig["signal"],
                "建議進場價": sig["entry"],
                "停損價": sig["stop"],
                "第一停利價": sig["tp1"],
                "評分": sig["score"],
                "理由": sig["reason"],
            })

    scan_df = pd.DataFrame(results)
    if not scan_df.empty:
        scan_df = scan_df.sort_values(["評分", "市場"], ascending=[False, True]).reset_index(drop=True)

    top3_df = scan_df.head(3).copy() if not scan_df.empty else pd.DataFrame()
    order_df = make_order_df(top3_df, capital, alloc_pct) if not top3_df.empty else pd.DataFrame()

    st.session_state["scan_df"] = scan_df
    st.session_state["top3_df"] = top3_df
    st.session_state["order_df"] = order_df
    st.session_state["df_map"] = df_map


# =========================
# 初始化
# =========================
init_state()

# =========================
# 手機優先樣式
# =========================
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1rem;
        padding-bottom: 4rem;
        max-width: 1100px;
    }
    div[data-testid="stMetric"] {
        background: rgba(240,242,246,0.55);
        border-radius: 14px;
        padding: 8px 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================
# Sidebar
# =========================
st.sidebar.title("📱 V38.1 手機版設定")

capital = st.sidebar.number_input("總資金", min_value=10000, value=DEFAULT_CAPITAL, step=10000)
max_positions = st.sidebar.slider("同時持倉上限", 1, 5, DEFAULT_MAX_POSITIONS)
single_position_pct = st.sidebar.slider("單檔上限 %", 10, 50, int(DEFAULT_SINGLE_POSITION_PCT * 100), step=5) / 100
stop_loss_pct = st.sidebar.slider("固定停損 %", 2, 10, int(DEFAULT_STOP_LOSS_PCT * 100)) / 100
take_profit_pct = st.sidebar.slider("第一停利 %", 5, 20, int(DEFAULT_TAKE_PROFIT_PCT * 100)) / 100
daily_loss_stop_pct = st.sidebar.slider("當日停手機制 %", 2, 10, int(DEFAULT_DAILY_LOSS_STOP_PCT * 100)) / 100
period = st.sidebar.selectbox("抓取區間", ["3mo", "6mo", "1y"], index=1)

tw_symbols = parse_symbols(st.sidebar.text_area("台股清單", ",".join(TW_DEFAULTS), height=90))
us_symbols = parse_symbols(st.sidebar.text_area("美股清單", ",".join(US_DEFAULTS), height=90))

# =========================
# Header
# =========================
st.title("📈 V38.1 手機版強化介面（無 Plotly 版）")
st.caption("分頁式操作：狙擊清單 / 下單表 / 持倉 / 紀錄 / 設定與推播")

h1, h2, h3, h4 = st.columns(4)
h1.metric("總資金", f"{capital:,.0f}")
h2.metric("持倉上限", f"{max_positions} 檔")
h3.metric("單檔上限", f"{single_position_pct:.0%}")
h4.metric("當日停手", f"{daily_loss_stop_pct:.0%}")

if st.button("🔍 立即重新掃描", type="primary", use_container_width=True):
    run_scan(tw_symbols, us_symbols, period, stop_loss_pct, take_profit_pct, capital, single_position_pct)

if st.session_state["scan_df"].empty:
    run_scan(tw_symbols, us_symbols, period, stop_loss_pct, take_profit_pct, capital, single_position_pct)

scan_df = st.session_state["scan_df"]
top3_df = st.session_state["top3_df"]
order_df = st.session_state["order_df"]
df_map = st.session_state["df_map"]
positions_live = update_position_prices(df_map)

# =========================
# Tabs
# =========================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🎯 狙擊清單",
    "📋 下單表",
    "💼 持倉追蹤",
    "🧾 交易紀錄",
    "⚙️ 推播 / 設定"
])

with tab1:
    st.subheader("明日實戰 3 檔")

    if top3_df.empty:
        st.warning("尚無可用結果")
    else:
        st.dataframe(top3_df, use_container_width=True, hide_index=True)

        quick_cards = st.columns(min(3, len(top3_df)))
        for i, (_, row) in enumerate(top3_df.iterrows()):
            with quick_cards[i]:
                st.markdown(f"**{row['代碼']}**")
                st.caption(f"{row['市場']}｜{row['訊號']}")
                st.write(f"進場：{row['建議進場價']}")
                st.write(f"停損：{row['停損價']}")
                st.write(f"停利：{row['第一停利價']}")

        symbol = st.selectbox("查看圖表", options=top3_df["代碼"].tolist())
        draw_chart_no_plotly(df_map.get(symbol, pd.DataFrame()), symbol)

        st.subheader("完整排行")
        st.dataframe(scan_df, use_container_width=True, hide_index=True)

with tab2:
    st.subheader("國泰手動下單表")

    if order_df.empty:
        st.warning("尚無下單表")
    else:
        st.dataframe(order_df, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ 下載 CSV",
            order_df.to_csv(index=False).encode("utf-8-sig"),
            "v38_1_cathay_orders.csv",
            "text/csv"
        )

        st.markdown("**快速執行守則**")
        st.markdown(
            "- 不突破不買\n"
            "- 弱於 5MA 不追\n"
            "- 觸價前先確認量能\n"
            "- 跌破停損不凹單"
        )

with tab3:
    st.subheader("持倉追蹤面板")
    pos_df = positions_live

    edited_pos = st.data_editor(
        pos_df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="positions_editor"
    )

    if st.button("💾 儲存持倉", key="save_positions_btn", use_container_width=True):
        save_positions(edited_pos.fillna("").to_dict("records"))
        st.success("持倉已儲存")

    if not edited_pos.empty:
        valid_returns = pd.to_numeric(edited_pos["報酬率%"], errors="coerce")
        avg_ret = valid_returns.mean() if valid_returns.notna().any() else 0

        p1, p2 = st.columns(2)
        p1.metric("持倉檔數", len(edited_pos))
        p2.metric("平均報酬率%", f"{avg_ret:.2f}")

with tab4:
    st.subheader("交易紀錄")
    log_df = trade_log_df()

    edited_log = st.data_editor(
        log_df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="trade_log_editor"
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 儲存交易紀錄", use_container_width=True):
            save_trade_log(edited_log.fillna("").to_dict("records"))
            st.success("交易紀錄已儲存")

    with c2:
        if not edited_log.empty:
            st.download_button(
                "⬇️ 匯出交易紀錄 CSV",
                edited_log.to_csv(index=False).encode("utf-8-sig"),
                "v38_1_trade_log.csv",
                "text/csv",
                use_container_width=True
            )

with tab5:
    st.subheader("LINE 推播 / 風控摘要")
    alert_text = build_alert_text(top3_df)
    st.code(alert_text)

    x1, x2 = st.columns(2)
    with x1:
        if st.button("發送 LINE 訊號", use_container_width=True):
            ok, msg = send_line(alert_text)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    with x2:
        st.info("已設定" if line_enabled() else "尚未設定 LINE secrets")

    st.markdown("**手機版優化內容**")
    st.markdown(
        "- 分頁式操作，減少單頁過長\n"
        "- 快速卡片化 3 檔狙擊清單\n"
        "- 內建持倉追蹤與交易紀錄\n"
        "- 一頁完成推播與下載 CSV"
    )

    st.markdown("**注意**")
    st.markdown(
        "- 本版仍為國泰手動下單流程\n"
        "- 無 Plotly 依賴，部署更穩\n"
        "- 若要盤中自動刷新，可再升級輪詢版"
    )

st.markdown("---")
st.caption("V38.1 無 Plotly 版：研究與決策輔助用途，不保證獲利。")
