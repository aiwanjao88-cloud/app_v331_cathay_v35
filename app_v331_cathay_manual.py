import os
import re
import json
import requests
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="V37 自動分組看板版", layout="wide")

WATCHLIST_FILE = "watchlist_memory.json"
HOLDINGS_FILE = "holdings_memory.json"

# =========================
# 樣式
# =========================
st.markdown("""
<style>
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 1rem;
}
.card {
    padding: 16px;
    border-radius: 16px;
    margin-bottom: 12px;
    border: 1px solid rgba(255,255,255,0.08);
}
.card-a { background: rgba(22, 163, 74, 0.12); }
.card-b { background: rgba(234, 179, 8, 0.12); }
.card-c { background: rgba(220, 38, 38, 0.12); }

.panel {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    padding: 14px;
    min-height: 220px;
    margin-bottom: 12px;
}

.tag {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    margin-right: 6px;
    margin-bottom: 6px;
}
.tag-green { background: #166534; color: white; }
.tag-yellow { background: #a16207; color: white; }
.tag-red { background: #991b1b; color: white; }
.tag-blue { background: #1d4ed8; color: white; }

.small-muted {
    color: #9CA3AF;
    font-size: 12px;
}
.big-number {
    font-size: 22px;
    font-weight: 800;
}
</style>
""", unsafe_allow_html=True)

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
# 記憶
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

def load_holdings():
    default_rows = [{"股票": "", "股數": 0, "均價": 0.0} for _ in range(20)]
    try:
        if os.path.exists(HOLDINGS_FILE):
            with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                rows = data.get("holdings", default_rows)
                rows = rows[:20] + [{"股票": "", "股數": 0, "均價": 0.0}] * max(0, 20 - len(rows))
                return rows
    except Exception:
        pass
    return default_rows

def save_holdings(rows):
    try:
        with open(HOLDINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"holdings": rows}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

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

def is_tw_symbol(symbol: str) -> bool:
    symbol = str(symbol).strip().upper()
    return bool(re.fullmatch(r"\d{4,6}[A-Z]{0,2}", symbol))

def fetch_symbol_data(symbol: str, period: str = "1y") -> tuple[pd.DataFrame, str, str]:
    symbol = str(symbol).strip().upper()
    clean_symbol = symbol.replace(".TW", "").replace(".TWO", "")

    if is_tw_symbol(clean_symbol):
        for suffix in [".TW", ".TWO"]:
            real_symbol = clean_symbol + suffix
            try:
                df = yf.download(real_symbol, period=period, progress=False, auto_adjust=True)
                df = normalize_df(df)
                if not df.empty and len(df) >= 120:
                    return df, real_symbol, "台股"
            except Exception:
                pass
        return pd.DataFrame(), clean_symbol, "台股"

    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        df = normalize_df(df)
        if not df.empty and len(df) >= 120:
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
    try:
        tk = yf.Ticker(symbol)
        info = tk.info if tk.info else {}
    except Exception:
        info = {}

    return {
        "pe": info.get("trailingPE", None),
        "market_cap": info.get("marketCap", None),
        "profit_margin": info.get("profitMargins", None),
        "revenue_growth": info.get("revenueGrowth", None),
        "earnings_growth": info.get("earningsGrowth", None),
    }

def get_priority_level(radar_score, price, buy_zone_low, buy_zone_high, latest_vol, avg_vol20, breakout_ok):
    near_buy_zone = buy_zone_low <= price <= buy_zone_high
    vol_ok = latest_vol > avg_vol20 * 1.3

    if radar_score >= 80 and near_buy_zone and (vol_ok or breakout_ok):
        return "A級：可優先建倉"
    elif radar_score >= 65:
        return "B級：觀察等確認"
    return "C級：先避開"

def calc_radar_signal(df: pd.DataFrame, symbol: str, market: str, capital: float, risk_percent: float):
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

    flagpole_return = (float(close.iloc[-1]) / float(close.iloc[-20]) - 1) * 100 if len(close) >= 20 else 0
    recent_10_high = float(high.rolling(10).max().iloc[-2]) if len(high) >= 11 else float(high.max())
    breakout_10 = price >= recent_10_high * 1.00
    volume_breakout = latest_vol > avg_vol20 * 1.5
    consolidation_ok = price >= ma20 * 0.98

    f = get_fundamental_data(symbol)

    fundamental_score = 0
    notes = []
    strategy_tags = []

    if f["profit_margin"] is not None and f["profit_margin"] > 0:
        fundamental_score += 15
        notes.append("獲利能力正向")
        strategy_tags.append("2益-獲利")

    if f["revenue_growth"] is not None and f["revenue_growth"] > 0.10:
        fundamental_score += 15
        notes.append("營收雙位數成長")
        strategy_tags.append("2益-高成長")

    if f["earnings_growth"] is not None and f["earnings_growth"] > 0:
        fundamental_score += 10
        notes.append("EPS成長正向")
        strategy_tags.append("2益-EPS")

    if f["pe"] is not None and f["pe"] > 0 and f["pe"] < 15:
        fundamental_score += 10
        notes.append("本益比相對合理")

    if f["market_cap"] is not None and f["market_cap"] > 3e9:
        fundamental_score += 10
        notes.append("規模/流動性較佳")

    technical_score = 0

    if latest_vol > avg_vol20 * 1.5:
        technical_score += 20
        notes.append("近期爆量")
        strategy_tags.append("3新-新買盤")

    if price > ma20 and ma20 > ma60:
        technical_score += 20
        notes.append("站上中長均線")

    if k > d and k > 50:
        technical_score += 10
        notes.append("KD 黃金交叉偏強")

    if k > 80:
        technical_score += 10
        notes.append("KD 高檔鈍化")

    if breakout_10 and volume_breakout and consolidation_ok:
        technical_score += 20
        notes.append("帶量突破整理區")
        strategy_tags.append("旗竿整理突破")

    if flagpole_return > 15:
        technical_score += 10
        notes.append("旗竿動能明顯")
        strategy_tags.append("旗竿原理")

    radar_score = fundamental_score + technical_score

    if radar_score >= 75:
        signal = "🟢 可打"
    elif radar_score >= 50:
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

    priority = get_priority_level(
        radar_score=radar_score,
        price=price,
        buy_zone_low=buy_zone_low,
        buy_zone_high=buy_zone_high,
        latest_vol=latest_vol,
        avg_vol20=avg_vol20,
        breakout_ok=breakout_10
    )

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
        "雷達分數": radar_score,
        "基本面分": fundamental_score,
        "技術面分": technical_score,
        "訊號": signal,
        "提醒": alert,
        "建倉優先級": priority,
        "買進區下緣": buy_zone_low,
        "買進區上緣": buy_zone_high,
        "停損價": stop_loss,
        "停利價": take_profit,
        "建議股數": suggested_shares,
        "策略標記": " / ".join(strategy_tags[:4]) if strategy_tags else "一般強勢",
        "備註": " / ".join(notes[:5]) if notes else "資料有限"
    }

def monitor_holdings(holdings_rows, capital, risk_percent):
    results = []

    for row in holdings_rows:
        symbol = str(row.get("股票", "")).strip().upper()
        qty = float(row.get("股數", 0) or 0)
        avg_cost = float(row.get("均價", 0) or 0)

        if not symbol or qty <= 0 or avg_cost <= 0:
            continue

        df, real_symbol, market = fetch_symbol_data(symbol)

        if df.empty:
            results.append({
                "股票": symbol,
                "市場": "未知",
                "股數": qty,
                "均價": avg_cost,
                "現價": None,
                "損益%": None,
                "訊號": "查無資料",
                "提醒": "無法抓取行情",
                "建議": "請確認代碼",
                "停損價": None,
                "停利價": None,
                "建倉優先級": "-"
            })
            continue

        try:
            signal_row = calc_radar_signal(df, real_symbol, market, capital, risk_percent)
            current_price = float(signal_row["現價"])
            pnl_pct = round((current_price / avg_cost - 1) * 100, 2)

            if current_price <= signal_row["停損價"]:
                advice = "🚨 停損警戒"
            elif pnl_pct >= 15:
                advice = "🎯 停利觀察"
            elif signal_row["建倉優先級"].startswith("A"):
                advice = "✅ 可續抱 / 強勢"
            elif signal_row["訊號"] == "🟡 觀望":
                advice = "⏳ 續抱觀察"
            else:
                advice = "⚠️ 考慮減碼"

            results.append({
                "股票": real_symbol,
                "市場": market,
                "股數": qty,
                "均價": round(avg_cost, 2),
                "現價": round(current_price, 2),
                "損益%": pnl_pct,
                "訊號": signal_row["訊號"],
                "提醒": signal_row["提醒"],
                "建議": advice,
                "停損價": signal_row["停損價"],
                "停利價": signal_row["停利價"],
                "建倉優先級": signal_row["建倉優先級"]
            })
        except Exception:
            results.append({
                "股票": symbol,
                "市場": market,
                "股數": qty,
                "均價": avg_cost,
                "現價": None,
                "損益%": None,
                "訊號": "錯誤",
                "提醒": "計算失敗",
                "建議": "請稍後再試",
                "停損價": None,
                "停利價": None,
                "建倉優先級": "-"
            })

    return pd.DataFrame(results)

# =========================
# 候選池
# =========================
TW_CANDIDATES = ["2330", "2317", "2454", "2382", "2303", "3037", "2376", "3017", "2603", "2609", "00631L", "00637L"]
US_CANDIDATES = ["NVDA", "AMD", "TSLA", "PLTR", "SMCI", "META", "AMZN", "AVGO", "MSFT", "QQQ"]

# =========================
# 預設狀態
# =========================
DEFAULT_WATCHLIST = "2330, 2317, 00631L, NVDA, TSLA, AMD"
if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist_memory(DEFAULT_WATCHLIST)

if "capital" not in st.session_state:
    st.session_state.capital = 100000

if "risk_percent" not in st.session_state:
    st.session_state.risk_percent = 1.0

if "only_tradeable" not in st.session_state:
    st.session_state.only_tradeable = False

if "holdings" not in st.session_state:
    st.session_state.holdings = load_holdings()

# =========================
# UI
# =========================
st.title("🛡️ V37 自動分組看板版")
st.caption("A/B/C 分組看板｜台美股自動篩選｜庫存 + 候選股雙面板｜00631L 可查詢")

st.subheader("📌 監控清單")
watchlist = st.text_input("輸入股票（逗號分隔）", value=st.session_state.watchlist)
save_watchlist_memory(watchlist)
st.session_state.watchlist = watchlist

st.subheader("📦 20檔現有庫存列表")
holdings_df = pd.DataFrame(st.session_state.holdings)
edited_holdings = st.data_editor(
    holdings_df,
    num_rows="fixed",
    use_container_width=True,
    column_config={
        "股票": st.column_config.TextColumn("股票代碼"),
        "股數": st.column_config.NumberColumn("股數", min_value=0, step=1),
        "均價": st.column_config.NumberColumn("均價", min_value=0.0, step=0.1),
    },
    key="holdings_editor"
)

save_col1, save_col2 = st.columns([1, 5])
if save_col1.button("💾 儲存庫存列表"):
    rows = edited_holdings.to_dict(orient="records")
    st.session_state.holdings = rows
    save_holdings(rows)
    st.success("已儲存 20 檔庫存列表")

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
m1, m2, m3, m4, m5 = st.columns(5)
scan_watchlist = m1.button("掃描監控清單")
scan_tw_auto = m2.button("台股自動篩選")
scan_us_auto = m3.button("美股自動篩選")
scan_holdings_btn = m4.button("監控現有庫存")
test_line_clicked = m5.button("測試 LINE 推播")

if test_line_clicked:
    ok, msg = push_line("V37 測試推播成功")
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
            row = calc_radar_signal(df, real_symbol, market, capital, risk_percent)
            results.append(row)
        except Exception:
            failed_symbols.append(s)

holdings_result_df = pd.DataFrame()
if scan_holdings_btn or (st.session_state.holdings and len(st.session_state.holdings) > 0):
    holdings_result_df = monitor_holdings(st.session_state.holdings, capital, risk_percent)

candidate_df = pd.DataFrame(results) if results else pd.DataFrame()
if not candidate_df.empty and only_tradeable:
    candidate_df = candidate_df[candidate_df["訊號"] == "🟢 可打"]

a_df = pd.DataFrame()
b_df = pd.DataFrame()
c_df = pd.DataFrame()

if not candidate_df.empty:
    a_df = candidate_df[candidate_df["建倉優先級"] == "A級：可優先建倉"].copy()
    b_df = candidate_df[candidate_df["建倉優先級"] == "B級：觀察等確認"].copy()
    c_df = candidate_df[candidate_df["建倉優先級"] == "C級：先避開"].copy()

# =========================
# 看板區
# =========================
st.markdown("---")
st.subheader("📊 自動分組看板")

dashboard_col1, dashboard_col2 = st.columns(2)

with dashboard_col1:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown("## 🟢 A級優先建倉")
    if not a_df.empty:
        st.dataframe(a_df, use_container_width=True)
    else:
        st.info("目前沒有 A級標的")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown("## 🔴 C級避開")
    if not c_df.empty:
        st.dataframe(c_df, use_container_width=True)
    else:
        st.info("目前沒有 C級標的")
    st.markdown('</div>', unsafe_allow_html=True)

with dashboard_col2:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown("## 🟡 B級觀察")
    if not b_df.empty:
        st.dataframe(b_df, use_container_width=True)
    else:
        st.info("目前沒有 B級標的")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown("## 📦 現有庫存警戒")
    if not holdings_result_df.empty:
        warning_df = holdings_result_df[
            holdings_result_df["建議"].isin(["🚨 停損警戒", "🎯 停利觀察", "⚠️ 考慮減碼"])
        ]
        if not warning_df.empty:
            st.dataframe(warning_df, use_container_width=True)
        else:
            st.dataframe(holdings_result_df, use_container_width=True)
    else:
        st.info("目前沒有庫存資料")
    st.markdown('</div>', unsafe_allow_html=True)

# =========================
# 候選股操作中心
# =========================
st.markdown("---")
st.subheader("💰 今日建倉操作中心")

tradable_df = pd.DataFrame()
if not a_df.empty:
    tradable_df = a_df.copy()
elif not b_df.empty:
    tradable_df = b_df.copy()

if not tradable_df.empty:
    top3 = tradable_df.sort_values(["雷達分數", "現價"], ascending=[False, True]).head(3)

    st.markdown("### 🏆 今日候選 Top 3")
    cols = st.columns(min(3, len(top3)))

    for i, (_, row) in enumerate(top3.iterrows()):
        with cols[i]:
            priority = row["建倉優先級"]
            card_class = "card-a" if priority.startswith("A") else ("card-b" if priority.startswith("B") else "card-c")
            tag_class = "tag-green" if priority.startswith("A") else ("tag-yellow" if priority.startswith("B") else "tag-red")

            st.markdown(
                f"""
                <div class="card {card_class}">
                    <div class="tag {tag_class}">{priority}</div>
                    <div class="tag tag-blue">{row['市場']}</div>
                    <div class="big-number">{row['股票']}</div>
                    <p><b>訊號：</b>{row['訊號']}</p>
                    <p><b>提醒：</b>{row['提醒']}</p>
                    <p><b>現價：</b>{row['現價']}</p>
                    <p><b>買進區：</b>{row['買進區下緣']} ~ {row['買進區上緣']}</p>
                    <p><b>停損：</b>{row['停損價']}</p>
                    <p><b>停利：</b>{row['停利價']}</p>
                    <p><b>建議股數：</b>{row['建議股數']}</p>
                    <p class="small-muted">{row['策略標記']}</p>
                </div>
                """,
                unsafe_allow_html=True
            )

    if ENABLE_LINE and st.button("推播 A級 / 優先名單到 LINE"):
        a_list = tradable_df[tradable_df["建倉優先級"] == "A級：可優先建倉"]
        source_df = a_list if not a_list.empty else top3
        msg_lines = ["🚦 V37 今日優先建倉名單"]
        for _, row in source_df.iterrows():
            msg_lines.append(
                f"{row['股票']} | {row['建倉優先級']} | {row['訊號']} | 現價:{row['現價']} | 停損:{row['停損價']} | 停利:{row['停利價']}"
            )
        ok, msg = push_line("\n".join(msg_lines))
        if ok:
            st.success("已推播到 LINE")
        else:
            st.warning(msg)

    st.markdown("### 🧾 國泰手動下單摘要")
    selected_symbol = st.selectbox("選擇候選標的", top3["股票"].tolist(), key="candidate_select")
    selected_row = top3[top3["股票"] == selected_symbol].iloc[0]

    order_price = st.number_input("下單價格", value=float(selected_row["現價"]), step=0.1, key="candidate_price")
    order_qty = st.number_input("下單股數", value=max(int(selected_row["建議股數"]), 1), step=1, min_value=1, key="candidate_qty")
    order_action = st.selectbox("操作", ["BUY 買進", "SELL 賣出", "REDUCE 減碼"], key="candidate_action")

    st.write(f"市場：{selected_row['市場']}")
    st.write(f"標的：{selected_symbol}")
    st.write(f"建倉優先級：{selected_row['建倉優先級']}")
    st.write(f"訊號：{selected_row['訊號']}")
    st.write(f"提醒：{selected_row['提醒']}")
    st.write(f"策略標記：{selected_row['策略標記']}")
    st.write(f"操作：{order_action}")
    st.write(f"價格：{order_price}")
    st.write(f"股數：{order_qty}")
    st.write(f"停損：{selected_row['停損價']}")
    st.write(f"停利：{selected_row['停利價']}")

    if ENABLE_LINE and st.button("推播 V37 候選股摘要到 LINE", key="candidate_line_push"):
        msg = (
            f"🏛️ V37 候選股下單摘要\n"
            f"市場：{selected_row['市場']}\n"
            f"標的：{selected_symbol}\n"
            f"建倉優先級：{selected_row['建倉優先級']}\n"
            f"訊號：{selected_row['訊號']}\n"
            f"提醒：{selected_row['提醒']}\n"
            f"策略標記：{selected_row['策略標記']}\n"
            f"操作：{order_action}\n"
            f"價格：{order_price}\n"
            f"股數：{order_qty}\n"
            f"停損：{selected_row['停損價']}\n"
            f"停利：{selected_row['停利價']}"
        )
        ok, line_msg = push_line(msg)
        if ok:
            st.success("已推播候選股摘要")
        else:
            st.warning(line_msg)
else:
    st.info("目前沒有可操作標的")

# =========================
# 底部錯誤區
# =========================
if failed_symbols:
    st.markdown("---")
    st.subheader("⚠️ 無法取得資料的股票")
    st.write(", ".join(failed_symbols))
