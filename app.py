import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import hmac
from datetime import datetime

# 頁面基本設定（必須放在所有 st 指令最前面）
st.set_page_config(page_title="美債 ETF 多模式均線與宏觀濾網回測", layout="wide")

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
st.title("📈 美債 ETF 均線輪動 ＋ 宏觀利差濾網回測系統")

# --- 側邊欄登入狀態與登出按鈕 ---
st.sidebar.success(f"👤 已登入帳號：**{st.session_state.get('current_user', '')}**")
if st.sidebar.button("🚪 登出系統"):
    st.session_state["authenticated"] = False
    st.session_state.pop("current_user", None)
    st.rerun()
st.sidebar.markdown("---")

# --- 側邊欄參數設定（已套用最佳參數為預設） ---
st.sidebar.header("⚙️ 策略核心設定")

trade_pair = st.sidebar.radio(
    "1️⃣ 做多標的槓桿倍數",
    options=[
        "1倍組合 (做多 TLT / 預設做空 TBF)",
        "2倍槓桿組合 (做多 UBT / 預設做空 TBT)"
    ],
    index=1,  # 預設：2倍槓桿組合 (UBT / TBT)
    help="選擇偏多訊號時買進的長債 ETF 倍數。"
)

is_2x = "2倍" in trade_pair
long_ticker = "UBT" if is_2x else "TLT"
default_short_ticker = "TBT" if is_2x else "TBF"

bear_mode = st.sidebar.radio(
    "2️⃣ 偏空訊號時的操作方式",
    options=[
        f"買入反向做空 ETF ({default_short_ticker})",
        "買入高收益債 ETF (HYG) 代替放空"
    ],
    index=0,  # 預設：買入反向做空 ETF
    help="當均線判定美債偏空時，可選擇買入反向做空美債 ETF，或改買高收益公司債 ETF (HYG)。"
)

use_hyg_for_bear = "HYG" in bear_mode
short_ticker = "HYG" if use_hyg_for_bear else default_short_ticker

signal_basis = st.sidebar.selectbox(
    "3️⃣ 訊號判斷基準 (相對於均線)",
    options=["TLT / TBF", "TLT / TBT", "UBT / TBT", "TLT", "TBF", "TBT", "UBT"],
    index=3,  # 預設：TLT
    help="選擇用來計算移動平均線並判斷多空方向的價格或比率序列。"
)

use_macro_filter = st.sidebar.checkbox(
    "4️⃣ 啟用宏觀利率濾網 (|US2Y - EFFR|)",
    value=True,  # 預設：開啟
    help="開啟後，僅在 2 年期美債利率與有效聯邦基金利率差距 >= 門檻時啟動均線輪動；關閉則全天候執行均線策略。"
)

st.sidebar.markdown("---")
st.sidebar.header("📅 回測與均線參數")
start_date = st.sidebar.date_input(
    "起始日期",
    value=pd.to_datetime("2010-02-01"),
    min_value=pd.to_datetime("2010-02-01"),
    max_value=datetime.today()
)
end_date = st.sidebar.date_input(
    "結束日期",
    value=datetime.today(),
    min_value=pd.to_datetime("2010-03-01"),
    max_value=datetime.today()
)

rebalance_freq = st.sidebar.radio(
    "訊號確認與換倉頻率",
    options=["月頻 (每月最後交易日收盤確認，次月持有)", "日頻 (每日收盤確認，次日持有)"],
    index=0  # 預設：月頻
)
is_monthly = "月頻" in rebalance_freq

ma_window = st.sidebar.slider(
    "均線週期 (月數 / 約當交易日)",
    min_value=3, max_value=24,
    value=3  # 預設：3 個月均線
)

# 若開啟宏觀濾網，才顯示濾網細部參數
if use_macro_filter:
    st.sidebar.markdown("---")
    st.sidebar.header("🌐 宏觀濾網參數")
    spread_threshold = st.sidebar.slider(
        "趨勢盤利差門檻 |US2Y - EFFR| (%)",
        min_value=0.10, max_value=1.50,
        value=0.45, step=0.05  # 預設：0.45%
    )
    range_mode = st.sidebar.radio(
        "盤整盤 (< 利差門檻) 應對模式",
        options=[
            "空手持有現金 (報酬率 0%)",
            f"單純持有 {long_ticker} (忽略均線訊號)",
            "單純持有 1倍 TLT (忽略均線訊號)"
        ],
        index=2  # 預設：單純持有 1倍 TLT
    )
else:
    spread_threshold = 0.0
    range_mode = "無"

# 動態顯示策略邏輯說明
filter_desc = (
    f"* **宏觀濾網已開啟**：當 `|US2Y - EFFR| ≥ {spread_threshold:.2f}%`（趨勢盤）執行均線輪動；小於門檻（盤整盤）則切換為 **{range_mode.split(' (')[0]}**。"
    if use_macro_filter
    else "* **宏觀濾網已關閉**：全天候執行均線輪動策略（無盤整過濾）。"
)
bull_cond = "跌破均線 (< SMA)" if signal_basis in ["TBF", "TBT"] else "站上均線 (> SMA)"
bear_cond = "站上均線 (> SMA)" if signal_basis in ["TBF", "TBT"] else "跌破均線 (< SMA)"
bear_action_desc = f"買入 **{short_ticker}**（高收益公司債替代）" if use_hyg_for_bear else f"買入 **{short_ticker}**（{'2倍' if is_2x else '1倍'}做空長債）"

st.markdown(f"""
**當前策略邏輯：** 觀察 **`{signal_basis}`** 相對於 **{ma_window} 個月移動平均線** 的位置：
* 當 `{signal_basis}` **{bull_cond}** ➡️ 買入 **{long_ticker}**（{'2倍' if is_2x else '1倍'}做多長債）
* 當 `{signal_basis}` **{bear_cond}** ➡️ {bear_action_desc}
{filter_desc}
""")

# --- 數據下載與快取 ---
@st.cache_data(ttl=3600)
def load_all_data():
    fetch_start = "2009-01-01"
    etf_raw = yf.download(["TLT", "TBF", "TBT", "UBT", "HYG"], start=fetch_start, auto_adjust=True)["Close"]
    
    dgs2_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
    dff_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    
    dgs2 = pd.read_csv(dgs2_url, index_col=0, parse_dates=True)
    dff = pd.read_csv(dff_url, index_col=0, parse_dates=True)
    
    rates = pd.concat([dgs2, dff], axis=1)
    rates.columns = ["US2Y", "EFFR"]
    rates = rates.replace(".", np.nan).astype(float).ffill()
    
    combined = etf_raw.join(rates, how="left").ffill().dropna()
    return combined

if start_date >= end_date:
    st.error("起始日期必須早於結束日期，請重新選擇。")
    st.stop()

try:
    df_daily_all = load_all_data()
except Exception as e:
    st.error(f"數據下載失敗（請檢查網路或 FRED 連線）：{e}")
    st.stop()

df_daily = df_daily_all.loc[:pd.to_datetime(end_date)].copy()

# --- 策略邏輯計算 ---
if is_monthly:
    df = df_daily.groupby(df_daily.index.to_period("M")).apply(lambda x: x.iloc[-1])
    df.index = pd.to_datetime(df.index.to_timestamp(how="end").date)
    actual_last_dates = df_daily.groupby(df_daily.index.to_period("M")).apply(lambda x: x.index[-1].strftime("%Y-%m-%d"))
    df["Actual_Trade_Date"] = actual_last_dates.values
    periods_per_year = 12
    window = ma_window
else:
    df = df_daily.copy()
    df["Actual_Trade_Date"] = df.index.strftime("%Y-%m-%d")
    periods_per_year = 252
    window = ma_window * 21

# 1. 根據選擇的訊號判斷基準計算指標序列
if signal_basis == "TLT / TBF":
    df["Indicator"] = df["TLT"] / df["TBF"]
elif signal_basis == "TLT / TBT":
    df["Indicator"] = df["TLT"] / df["TBT"]
elif signal_basis == "UBT / TBT":
    df["Indicator"] = df["UBT"] / df["TBT"]
else:
    df["Indicator"] = df[signal_basis]

df["Indicator_MA"] = df["Indicator"].rolling(window=window).mean()

# 2. 產生原始均線多空訊號（1 = 偏多標的, -1 = 偏空標的）
if signal_basis in ["TBF", "TBT"]:
    trend_signal = np.where(df["Indicator"] > df["Indicator_MA"], -1, 1)
else:
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
    st.warning("選取的回測區間有效交易期數不足（請拉長起始與結束日期的間隔，或調早起始日期）。")
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
        "勝率 (持倉期正報酬比例)": f"{win_rate:.2%}",
        "最大回撤 (MDD)": f"{max_dd:.2%}",
        "年化波動度": f"{ann_vol:.2%}",
        "夏普值 (Rf=0%)": f"{sharpe:.2f}",
        "市場曝險時間比例": f"{exposure:.1%}"
    }

strat_title = f"{long_ticker} / {short_ticker} 策略 ({signal_basis} 均線)"
strat_metrics = calc_metrics(df_bt["Strategy_Ret"], df_bt["Strategy_Equity"], df_bt["Strategy_DD"], periods_per_year, df_bt["Pos"])
bm_metrics = calc_metrics(df_bt["BM_Ret"], df_bt["BM_Equity"], df_bt["BM_DD"], periods_per_year)

# --- 1. 最新兩期訊號與實戰執行時間軸（並排對照卡片） ---
st.subheader("🎯 最新兩期訊號與實戰執行時間軸")

def get_ticker_badge(code):
    if code == 1:
        return f"{long_ticker} ({'2倍' if is_2x else '1倍'}做多)"
    elif code == -1:
        return f"{short_ticker} ({'高收益債' if use_hyg_for_bear else ('2倍' if is_2x else '1倍') + '做空'})"
    elif code == 2:
        return "TLT (1倍保守持倉)"
    else:
        return "現金 (空手觀望)"

def render_signal_card(container, row_data, title_label, badge_type="current"):
    gen_date = pd.to_datetime(row_data["Actual_Trade_Date"])
    if is_monthly:
        next_period = gen_date.to_period("M") + 1
        next_bday = (gen_date + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
        apply_label = f"{next_period.strftime('%Y-%m')} 月份（{next_bday} 起持有整月）"
    else:
        next_bday = (gen_date + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
        apply_label = f"{next_bday}（次一交易日全天）"

    target_str = get_ticker_badge(row_data["Target_Signal"])
    ind_val = row_data["Indicator"]
    ma_val = row_data["Indicator_MA"]
    pos_rel = "大於均線 (>)" if ind_val > ma_
