import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import hmac
from datetime import datetime

# 頁面基本設定（必須放在所有 st 指令最前面）
st.set_page_config(page_title="自訂 ETF 均線輪動與宏觀濾網回測", layout="wide")

# ==========================================
# 🔐 帳號密碼驗證模組 (讀取 Streamlit Secrets)
# ==========================================
def check_password():
    """驗證使用者輸入的帳號與密碼是否符合 st.secrets['passwords']"""
    if st.session_state.get("authenticated", False):
        return True

    st.title("🔒 策略回測系統登入驗證")
    st.markdown("請輸入授權帳號與密碼以檢視策略數據與圖表。")

    with st.form("login_form"):
        username = st.text_input("帳號 (Username)")
        password = st.text_input("密碼 (Password)", type="password")
        submitted = st.form_submit_button("登入")

        if submitted:
            if "passwords" not in st.secrets:
                st.error("⚠️ 系統尚未設定 Secrets `[passwords]`，請先至 Streamlit Cloud 後台完成設定。")
                return False

            passwords_dict = st.secrets["passwords"]
            if username in passwords_dict and hmac.compare_digest(str(password), str(passwords_dict[username])):
                st.session_state["authenticated"] = True
                st.session_state["current_user"] = username
                st.rerun()
            else:
                st.error("❌ 帳號或密碼錯誤，請重新確認。")

    return False

if not check_password():
    st.stop()

# ==========================================
# 📈 主程式開始（驗證通過後才會執行）
# ==========================================
st.title("📈 自訂 ETF 均線輪動 ＋ 宏觀利差濾網回測系統")

# --- 側邊欄登入狀態與登出按鈕 ---
st.sidebar.success(f"👤 已登入帳號：**{st.session_state.get('current_user', '')}**")
if st.sidebar.button("🚪 登出系統"):
    st.session_state["authenticated"] = False
    st.session_state.pop("current_user", None)
    st.rerun()
st.sidebar.markdown("---")

# --- 側邊欄參數設定 ---
st.sidebar.header("⚙️ 標的與均線設定")

long_ticker = st.sidebar.text_input(
    "1️⃣ 偏多買入標的 (站上均線時買入)",
    value="UBT",
    help="輸入美股代號，例如：UBT、TLT、QQQ、SPY。系統將以此標的相對於其均線來判斷多空。"
).strip().upper()

short_ticker = st.sidebar.text_input(
    "2️⃣ 偏空買入標的 (跌破均線時買入)",
    value="TBT",
    help="輸入美股代號，例如：TBT、TBF、HYG、SH、PSQ。"
).strip().upper()

ma_window = st.sidebar.number_input(
    "3️⃣ 均線週期 (幾個月均線)",
    min_value=1,
    max_value=60,
    value=3,
    step=1,
    help="以「偏多買入標的」計算移動平均線的月數（例如輸入 3 代表 3 個月均線）。"
)

use_macro_filter = st.sidebar.checkbox(
    "4️⃣ 啟用宏觀利率濾網 (|US2Y - EFFR|)",
    value=True,
    help="開啟後，僅在 2 年期美債利率與有效聯邦基金利率差距 >= 門檻時啟動均線輪動；關閉則全天候執行均線策略。"
)

st.sidebar.markdown("---")
st.sidebar.header("📅 回測期間與頻率")
start_date = st.sidebar.date_input(
    "起始日期",
    value=pd.to_datetime("2010-02-01"),
    min_value=pd.to_datetime("2000-01-01"),
    max_value=datetime.today()
)
end_date = st.sidebar.date_input(
    "結束日期",
    value=datetime.today(),
    min_value=pd.to_datetime("2000-02-01"),
    max_value=datetime.today()
)

rebalance_freq = st.sidebar.radio(
    "訊號確認與換倉頻率",
    options=["月頻 (每月最後交易日收盤確認，次月持有)", "日頻 (每日收盤確認，次日持有)"],
    index=0
)
is_monthly = "月頻" in rebalance_freq

# 若開啟宏觀濾網，才顯示濾網細部參數
if use_macro_filter:
    st.sidebar.markdown("---")
    st.sidebar.header("🌐 宏觀濾網參數")
    spread_threshold = st.sidebar.slider(
        "趨勢盤利差門檻 |US2Y - EFFR| (%)",
        min_value=0.10, max_value=1.50,
        value=0.45, step=0.05
    )
    range_mode = st.sidebar.radio(
        "盤整盤 (< 利差門檻) 應對模式",
        options=[
            "空手持有現金 (報酬率 0%)",
            f"單純持有 {long_ticker} (忽略均線訊號)",
            "單純持有 1倍 TLT (忽略均線訊號)"
        ],
        index=2
    )
else:
    spread_threshold = 0.0
    range_mode = "無"

if not long_ticker or not short_ticker:
    st.warning("⚠️ 請在左側輸入完整的「偏多買入標的」與「偏空買入標的」代號。")
    st.stop()

# 動態顯示策略邏輯說明
filter_desc = (
    f"* **宏觀濾網已開啟**：當 `|US2Y - EFFR| ≥ {spread_threshold:.2f}%`（趨勢盤）執行均線輪動；小於門檻（盤整盤）則切換為 **{range_mode.split(' (')[0]}**。"
    if use_macro_filter
    else "* **宏觀濾網已關閉**：全天候執行均線輪動策略（無盤整過濾）。"
)

st.markdown(f"""
**當前策略邏輯：** 以 **`{long_ticker}`** 的價格相對於其 **{ma_window} 個月移動平均線** 作為判斷基準：
* 當 `{long_ticker}` **站上 {ma_window} 個月均線 (> SMA)** ➡️ 買入 **`{long_ticker}`**
* 當 `{long_ticker}` **跌破 {ma_window} 個月均線 (< SMA)** ➡️ 買入 **`{short_ticker}`**
{filter_desc}
""")

# --- 數據下載與快取 ---
@st.cache_data(ttl=3600)
def load_market_data(t_long, t_short):
    fetch_start = "2000-01-01"
    tickers_to_fetch = list(set([t_long, t_short, "TLT"]))
    
    # 1. 下載 ETF 還原收盤價
    raw = yf.download(tickers_to_fetch, start=fetch_start, auto_adjust=True)["Close"]
    
    # 若只抓到單一欄位或 MultiIndex 處理
    if isinstance(raw, pd.Series):
        raw = raw.to_frame(name=tickers_to_fetch[0])
    
    # 檢查代號是否有效
    missing_tickers = [t for t in tickers_to_fetch if t not in raw.columns or raw[t].dropna().empty]
    if missing_tickers:
        return None, missing_tickers

    # 2. 從 FRED 下載 DGS2 (2年期美債利率) 與 DFF (Effective Fed Funds Rate)
    dgs2_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
    dff_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    
    dgs2 = pd.read_csv(dgs2_url, index_col=0, parse_dates=True)
    dff = pd.read_csv(dff_url, index_col=0, parse_dates=True)
    
    rates = pd.concat([dgs2, dff], axis=1)
    rates.columns = ["US2Y", "EFFR"]
    rates = rates.replace(".", np.nan).astype(float).ffill()
    
    # 合併數據並對齊交易日（取所有輸入標的皆已上市的共同區間）
    combined = raw[tickers_to_fetch].join(rates, how="left").ffill().dropna()
    return combined, []

if start_date >= end_date:
    st.error("起始日期必須早於結束日期，請重新選擇。")
    st.stop()

try:
    df_daily_all, missing_list = load_market_data(long_ticker, short_ticker)
except Exception as e:
    st.error(f"數據下載失敗（請檢查網路連線或確認美股代號是否正確）：{e}")
    st.stop()

if missing_list:
    st.error(f"❌ 找不到以下標的的歷史數據，請確認美股代號是否輸入正確：`{', '.join(missing_list)}`")
    st.stop()

# 提示共同數據最早起始日
earliest_common_date = df_daily_all.index.min().date()
if start_date < earliest_common_date:
    st.info(f"ℹ️ 提醒：所選標的（`{long_ticker}`、`{short_ticker}`）共同上市的最早日期為 **`{earliest_common_date}`**，回測將自動從該日期之後的均線完成日開始計算。")

df_daily = df_daily_all.loc[:pd.to_datetime(end_date)].copy()

# --- 策略邏輯計算 ---
if is_monthly:
    df = df_daily.groupby(df_daily.index.to_period("M")).apply(lambda x: x.iloc[-1])
    df.index = pd.to_datetime(df.index.to_timestamp(how="end").date)
    actual_last_dates = df_daily.groupby(df_daily.index.to_period("M")).apply(lambda x: x.index[-1].strftime("%Y-%m-%d"))
    df["Actual_Trade_Date"] = actual_last_dates.values
    periods_per_year = 12
    window = int(ma_window)
else:
    df = df_daily.copy()
    df["Actual_Trade_Date"] = df.index.strftime("%Y-%m-%d")
    periods_per_year = 252
    window = int(ma_window) * 21

# 1. 統一以「偏多買入標的 (long_ticker)」計算價格與均線
df["Indicator"] = df[long_ticker]
df["Indicator_MA"] = df["Indicator"].rolling(window=window).mean()

# 2. 產生原始均線多空訊號（1 = 買入 long_ticker, -1 = 買入 short_ticker）
trend_signal = np.where(df["Indicator"] > df["Indicator_MA"], 1, -1)

# 3. 宏觀利差濾網判定
df["Rate_Spread"] = (df["US2Y"] - df["EFFR"]).abs()

if use_macro_filter:
    df["Is_Trend"] = df["Rate_Spread"] >= spread_threshold
    if "空手" in range_mode:
        range_signal = 0
    elif "1倍 TLT" in range_mode:
        range_signal = 2
    else:
        range_signal = 1
    df["Target_Signal"] = np.where(df["Is_Trend"], trend_signal, range_signal)
else:
    df["Is_Trend"] = True
    df["Target_Signal"] = trend_signal

# 4. 計算各標的單期報酬率
df["Long_Ret"] = df[long_ticker].pct_change()
df["Short_Ret"] = df[short_ticker].pct_change()
df["TLT_Ret"] = df["TLT"].pct_change()

# 避免未來函數：用上一期的 Target_Signal 決定這一期的報酬
df["Pos"] = df["Target_Signal"].shift(1)

conditions = [df["Pos"] == 1, df["Pos"] == -1, df["Pos"] == 0, df["Pos"] == 2]
choices = [df["Long_Ret"], df["Short_Ret"], 0.0, df["TLT_Ret"]]
df["Strategy_Ret"] = np.select(conditions, choices, default=np.nan)
df["BM_Ret"] = df["TLT_Ret"]

# 過濾回測起始日之後的數據並清除空值
df_bt = df.loc[pd.to_datetime(start_date):].dropna(subset=["Strategy_Ret", "BM_Ret", "Indicator_MA"]).copy()

if len(df_bt) < 2:
    st.warning("選取的回測區間有效交易期數不足（請拉長起始與結束日期的間隔，或確認所選 ETF 上市時間是否足夠）。")
    st.stop()

# 計算累積淨值與回撤
df_bt["Strategy_Equity"] = (1 + df_bt["Strategy_Ret"]).cumprod()
df_bt["BM_Equity"] = (1 + df_bt["BM_Ret"]).cumprod()

df_bt["Strategy_DD"] = df_bt["Strategy_Equity"] / df_bt["Strategy_Equity"].cummax() - 1
df_bt["BM_DD"] = df_bt["BM_Equity"] / df_bt["BM_Equity"].cummax() - 1

# 輔助函數：在指定圖表加上盤整盤灰色陰影
def add_range_shading(fig, df_data):
    if not use_macro_filter:
        return
    in_range = False
    range_start = None
    for idx, row in df_data.iterrows():
        if not row["Is_Trend"] and not in_range:
            in_range = True
            range_start = idx
        elif row["Is_Trend"] and in_range:
            in_range = False
            fig.add_vrect(x0=range_start, x1=idx, fillcolor="gray", opacity=0.14, line_width=0)
    if in_range:
        fig.add_vrect(x0=range_start, x1=df_data.index[-1], fillcolor="gray", opacity=0.14, line_width=0)

# --- 績效指標計算函數 ---
def calc_metrics(returns, equity, dd, ppy, pos_series=None):
    total_ret = equity.iloc[-1] - 1
    n_periods = len(returns)
    cagr = (equity.iloc[-1]) ** (ppy / n_periods) - 1
    
    if pos_series is not None:
        active_rets = returns[pos_series != 0]
        win_rate = (active_rets > 0).sum() / len(active_rets) if len(active_rets) > 0 else 0
        exposure = (pos_series != 0).sum() / n_periods
    else:
        win_rate = (returns > 0).sum() / n_periods
        exposure = 1.0
        
    max_dd = dd.min()
    ann_vol = returns.std() * np.sqrt(ppy)
    sharpe = (cagr / ann_vol) if ann_vol != 0 else 0
    return {
        "總報酬率": f"{total_ret:.2%}",
        "年化報酬率 (CAGR)": f"{cagr:.2%}",
        "勝率 (持倉期正報酬比例)": f"{
