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
    "1️⃣ 偏多買入標的 (站上均線時買入 / BM)",
    value="UBT",
    help="輸入美股代號，例如：UBT、TLT、QQQ、SPY。系統將以此標的相對於其均線來判斷多空，並作為 Benchmark 基準。"
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
**當前策略邏輯：** 以 **`{long_ticker}`** 的價格相對於其 **{ma_window} 個月移動平均線** 作為判斷基準（Benchmark 為單純 Buy & Hold **`{long_ticker}`**）：
* 當 `{long_ticker}` **站上 {ma_window} 個月均線 (> SMA)** ➡️ 買入 **`{long_ticker}`**
* 當 `{long_ticker}` **跌破 {ma_window} 個月均線 (< SMA)** ➡️ 買入 **`{short_ticker}`**
{filter_desc}
""")

# --- 數據下載與快取 ---
@st.cache_data(ttl=3600)
def load_market_data(t_long, t_short, need_tlt):
    fetch_start = "2000-01-01"
    tickers_to_fetch = list(set([t_long, t_short] + (["TLT"] if need_tlt else [])))
    
    # 1. 下載 ETF 還原收盤價
    raw = yf.download(tickers_to_fetch, start=fetch_start, auto_adjust=True)["Close"]
    
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
    
    # 合併數據並對齊交易日
    combined = raw[tickers_to_fetch].join(rates, how="left").ffill().dropna()
    return combined, []

if start_date >= end_date:
    st.error("起始日期必須早於結束日期，請重新選擇。")
    st.stop()

need_tlt_data = use_macro_filter and ("1倍 TLT" in range_mode)

try:
    df_daily_all, missing_list = load_market_data(long_ticker, short_ticker, need_tlt_data)
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
df["TLT_Ret"] = df["TLT"].pct_change() if "TLT" in df.columns else df["Long_Ret"]

# 避免未來函數：用上一期的 Target_Signal 決定這一期的報酬
df["Pos"] = df["Target_Signal"].shift(1)

conditions = [df["Pos"] == 1, df["Pos"] == -1, df["Pos"] == 0, df["Pos"] == 2]
choices = [df["Long_Ret"], df["Short_Ret"], 0.0, df["TLT_Ret"]]
df["Strategy_Ret"] = np.select(conditions, choices, default=np.nan)

# Benchmark 設為單純 Buy & Hold 偏多買入標的 (long_ticker)
df["BM_Ret"] = df["Long_Ret"]

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
        "勝率 (持倉期正報酬比例)": f"{win_rate:.2%}",
        "最大回撤 (MDD)": f"{max_dd:.2%}",
        "年化波動度": f"{ann_vol:.2%}",
        "夏普值 (Rf=0%)": f"{sharpe:.2f}",
        "市場曝險時間比例": f"{exposure:.1%}"
    }

strat_title = f"{long_ticker} / {short_ticker} 策略 ({long_ticker} {ma_window}M 均線)"
bm_title = f"Benchmark (Buy & Hold {long_ticker})"
strat_metrics = calc_metrics(df_bt["Strategy_Ret"], df_bt["Strategy_Equity"], df_bt["Strategy_DD"], periods_per_year, df_bt["Pos"])
bm_metrics = calc_metrics(df_bt["BM_Ret"], df_bt["BM_Equity"], df_bt["BM_DD"], periods_per_year)

# --- 1. 最新兩期訊號與實戰執行時間軸（並排對照卡片） ---
st.subheader("🎯 最新兩期訊號與實戰執行時間軸")

def get_ticker_badge(code):
    if code == 1:
        return f"買進並持有 {long_ticker} (偏多標的)"
    elif code == -1:
        return f"買進並持有 {short_ticker} (偏空標的)"
    elif code == 2:
        return "買進並持有 TLT (盤整保守持倉)"
    else:
        return "空手觀望 (持有現金)"

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
    pos_rel = "站上均線 (>)" if ind_val > ma_val else "跌破均線 (<)"

    if use_macro_filter:
        regime_txt = (
            f"🔥 趨勢盤 (`|US2Y-EFFR| = {row_data['Rate_Spread']:.2f}% ≥ {spread_threshold:.2f}%`)"
            if row_data["Is_Trend"]
            else f"💤 盤整盤 (`|US2Y-EFFR| = {row_data['Rate_Spread']:.2f}% < {spread_threshold:.2f}%`)"
        )
    else:
        regime_txt = "🌐 未啟用濾網（全天候均線）"

    with container:
        if badge_type == "latest":
            st.success(
                f"#### 🆕 {title_label}\n"
                f"* **訊號發生／確認日**：`{gen_date.strftime('%Y-%m-%d')}`\n"
                f"* **訊號適用期間**：**`{apply_label}`**\n"
                f"* **建議配置標的**：➡️ **{target_str}**\n"
                f"* **均線數值**：`{long_ticker}` = `{ind_val:.2f}` vs {ma_window}M 均線 = `{ma_val:.2f}`（{pos_rel}）\n"
                f"* **宏觀環境**：{regime_txt}"
            )
        else:
            st.info(
                f"#### 📌 {title_label}\n"
                f"* **訊號發生／確認日**：`{gen_date.strftime('%Y-%m-%d')}`\n"
                f"* **訊號適用期間**：**`{apply_label}`**\n"
                f"* **實際執行標的**：➡️ **{target_str}**\n"
                f"* **均線數值**：`{long_ticker}` = `{ind_val:.2f}` vs {ma_window}M 均線 = `{ma_val:.2f}`（{pos_rel}）\n"
                f"* **宏觀環境**：{regime_txt}"
            )

col_prev, col_curr = st.columns(2)
prev_row = df_bt.iloc[-2]
last_row = df_bt.iloc[-1]

render_signal_card(col_prev, prev_row, "前一期已確認訊號（當期適用中）", badge_type="prev")
render_signal_card(col_curr, last_row, "最新一期訊號（下期即將生效）", badge_type="latest")

# --- 2. 績效總覽表格 ---
st.subheader(f"📊 策略與基準 (Buy & Hold {long_ticker}) 績效對比")
metrics_df = pd.DataFrame({
    strat_title: strat_metrics,
    bm_title: bm_metrics
})
st.table(metrics_df)

# --- 3. 累積淨值走勢圖 ---
eq_subtitle = " (灰色陰影為盤整盤)" if use_macro_filter else ""
st.subheader(f"1️⃣ 累積淨值走勢圖{eq_subtitle}")
fig_eq = go.Figure()
fig_eq.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Strategy_Equity"],
    mode="lines", name=strat_title,
    line=dict(color="#1f77b4", width=2.5)
))
fig_eq.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["BM_Equity"],
    mode="lines", name=bm_title,
    line=dict(color="#7f7f7f", width=2, dash="dash")
))
add_range_shading(fig_eq, df_bt)
fig_eq.update_layout(
    hovermode="x unified",
    yaxis_title="累積淨值",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=420
)
st.plotly_chart(fig_eq, use_container_width=True)

# --- 4. 歷史回撤走勢圖 ---
st.subheader("2️⃣ 歷史回撤走勢圖 (Drawdown)")
fig_dd = go.Figure()
fig_dd.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Strategy_DD"] * 100,
    mode="lines", name="策略回撤 (%)",
    fill="tozeroy", line=dict(color="#d62728", width=1.8)
))
fig_dd.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["BM_DD"] * 100,
    mode="lines", name=f"{long_ticker} (BM) 回撤 (%)",
    line=dict(color="#555555", width=1.5, dash="dot")
))
fig_dd.update_layout(
    hovermode="x unified",
    yaxis_title="回撤幅度 (%)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=380
)
st.plotly_chart(fig_dd, use_container_width=True)

# --- 5. 市場狀態判定圖（僅在開啟宏觀濾網時顯示） ---
if use_macro_filter:
    st.subheader("3️⃣ 市場狀態判定圖：趨勢盤 vs 盤整盤 (|US2Y - EFFR|)")
    fig_regime = go.Figure()

    fig_regime.add_trace(go.Scatter(
        x=df_bt.index, y=df_bt["Rate_Spread"],
        mode="lines", name="|US2Y - EFFR| 利差 (%)",
        line=dict(color="#9467bd", width=2),
        fill="tozeroy", fillcolor="rgba(148, 103, 189, 0.15)"
    ))

    trend_df = df_bt[df_bt["Is_Trend"]]
    fig_regime.add_trace(go.Scatter(
        x=trend_df.index, y=trend_df["Rate_Spread"],
        mode="markers", name="趨勢盤 (啟動均線輪動)",
        marker=dict(color="#2ca02c", size=6)
    ))

    range_df = df_bt[~df_bt["Is_Trend"]]
    fig_regime.add_trace(go.Scatter(
        x=range_df.index, y=range_df["Rate_Spread"],
        mode="markers", name="盤整盤 (暫停輪動)",
        marker=dict(color="#8c8c8c", size=5)
    ))

    fig_regime.add_hline(
        y=spread_threshold, line_dash="dash", line_color="#d62728", line_width=2,
        annotation_text=f"趨勢門檻 {spread_threshold:.2f}%", annotation_position="top left"
    )

    fig_regime.update_layout(
        hovermode="x unified",
        yaxis_title="利差絕對值 (%)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=400
    )
    st.plotly_chart(fig_regime, use_container_width=True)

# --- 6. 買入標的與均線走勢圖 ---
chart_num = "4️⃣" if use_macro_filter else "3️⃣"
st.subheader(f"{chart_num} {long_ticker} 走勢與 {ma_window} 個月移動平均線")
fig_ind = go.Figure()
fig_ind.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Indicator"],
    mode="lines", name=f"{long_ticker} 還原收盤價",
    line=dict(color="#17becf", width=2.2)
))
fig_ind.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Indicator_MA"],
    mode="lines", name=f"{ma_window} 個月均線 ({ma_window}M SMA)",
    line=dict(color="#ff7f0e", width=2)
))

cross_above = df_bt[(df_bt["Indicator"] > df_bt["Indicator_MA"]) & (df_bt["Indicator"].shift(1) <= df_bt["Indicator_MA"].shift(1))]
cross_below = df_bt[(df_bt["Indicator"] < df_bt["Indicator_MA"]) & (df_bt["Indicator"].shift(1) >= df_bt["Indicator_MA"].shift(1))]

fig_ind.add_trace(go.Scatter(
    x=cross_above.index, y=cross_above["Indicator"],
    mode="markers", name=f"突破均線 (買進 {long_ticker})",
    marker=dict(symbol="triangle-up", size=10, color="#2ca02c")
))
fig_ind.add_trace(go.Scatter(
    x=cross_below.index, y=cross_below["Indicator"],
    mode="markers", name=f"跌破均線 (買進 {short_ticker})",
    marker=dict(symbol="triangle-down", size=10, color="#d62728")
))

add_range_shading(fig_ind, df_bt)
fig_ind.update_layout(
    hovermode="x unified",
    yaxis_title=f"{long_ticker} 價格",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=420
)
st.plotly_chart(fig_ind, use_container_width=True)

# --- 7. 逐期計算明細與訊號核對表 ---
st.markdown("---")
st.subheader("📋 逐期計算明細與訊號核對表（由新到舊排序）")
st.caption("說明：「當期實際持有標的」係依據**上一期收盤產生的訊號**決定，並賺取「本期」的漲跌幅；「本期收盤產生訊號」則將於**下一期**生效。")

def short_pos_name(code):
    if code == 1:
        return long_ticker
    elif code == -1:
        return short_ticker
    elif code == 2:
        return "TLT (保守)"
    elif code == 0:
        return "現金 (空手)"
    return "-"

audit_cols = {
    "訊號確認日 (實際交易日)": df_bt["Actual_Trade_Date"],
    f"{long_ticker} 還原收盤 (判斷基準/BM)": df_bt[long_ticker].round(2),
    f"{ma_window}M 均線 ({long_ticker})": df_bt["Indicator_MA"].round(2),
    f"{short_ticker} 還原收盤": df_bt[short_ticker].round(2),
}
if need_tlt_data and long_ticker != "TLT" and short_ticker != "TLT":
    audit_cols["TLT 還原收盤 (盤整保守用)"] = df_bt["TLT"].round(2)

audit_cols.update({
    "US2Y (%)": df_bt["US2Y"].round(2),
    "EFFR (%)": df_bt["EFFR"].round(2),
    "利差 |US2Y-EFFR| (%)": df_bt["Rate_Spread"].round(2),
    "市場狀態": np.where(df_bt["Is_Trend"], "趨勢盤", "盤整盤") if use_macro_filter else "無濾網",
    "本期實際持有標的 (依上期訊號)": df_bt["Pos"].apply(short_pos_name),
    "本期策略報酬 (%)": (df_bt["Strategy_Ret"] * 100).round(2),
    f"本期 {long_ticker} (BM) 報酬 (%)": (df_bt["BM_Ret"] * 100).round(2),
    "策略累積淨值": df_bt["Strategy_Equity"].round(4),
    f"{long_ticker} (BM) 累積淨值": df_bt["BM_Equity"].round(4),
    "本期收盤產生新訊號 (下期適用)": df_bt["Target_Signal"].apply(short_pos_name),
})

audit_df = pd.DataFrame(audit_cols)
audit_df_display = audit_df.iloc[::-1].reset_index(drop=True)
st.dataframe(audit_df_display, use_container_width=True, height=450)

csv_data = audit_df_display.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    label="📥 下載完整計算明細 CSV (可用 Excel 打開驗算)",
    data=csv_data,
    file_name=f"{long_ticker}_{short_ticker}_strategy_audit_{datetime.today().strftime('%Y%m%d')}.csv",
    mime="text/csv"
)
