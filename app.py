import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime

# 頁面基本設定
st.set_page_config(page_title="美債 ETF 多模式均線與宏觀濾網回測", layout="wide")
st.title("📈 美債 ETF 均線輪動 ＋ 宏觀利差濾網回測系統")

# --- 側邊欄參數設定 ---
st.sidebar.header("⚙️ 策略核心設定")

trade_pair = st.sidebar.radio(
    "1️⃣ 做多標的槓桿倍數",
    options=[
        "1倍組合 (做多 TLT / 預設做空 TBF)",
        "2倍槓桿組合 (做多 UBT / 預設做空 TBT)"
    ],
    index=0,
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
    index=0,
    help="當均線判定美債偏空時，可選擇買入反向做空美債 ETF，或改買高收益公司債 ETF (HYG)。"
)

use_hyg_for_bear = "HYG" in bear_mode
short_ticker = "HYG" if use_hyg_for_bear else default_short_ticker

signal_basis = st.sidebar.selectbox(
    "3️⃣ 訊號判斷基準 (相對於均線)",
    options=["TLT / TBF", "TLT / TBT", "UBT / TBT", "TLT", "TBF", "TBT", "UBT"],
    index=0,
    help="選擇用來計算移動平均線並判斷多空方向的價格或比率序列。"
)

use_macro_filter = st.sidebar.checkbox(
    "4️⃣ 啟用宏觀利率濾網 (|US2Y - EFFR|)",
    value=True,
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
    index=0
)
is_monthly = "月頻" in rebalance_freq

ma_window = st.sidebar.slider("均線週期 (月數 / 約當交易日)", min_value=3, max_value=24, value=12)

# 若開啟宏觀濾網，才顯示濾網細部參數
if use_macro_filter:
    st.sidebar.markdown("---")
    st.sidebar.header("🌐 宏觀濾網參數")
    spread_threshold = st.sidebar.slider(
        "趨勢盤利差門檻 |US2Y - EFFR| (%)",
        min_value=0.10, max_value=1.50, value=0.50, step=0.05
    )
    range_mode = st.sidebar.radio(
        "盤整盤 (< 利差門檻) 應對模式",
        options=[
            "空手持有現金 (報酬率 0%)",
            f"單純持有 {long_ticker} (忽略均線訊號)",
            "單純持有 1倍 TLT (忽略均線訊號)"
        ],
        index=0
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
    
    # 1. 下載 TLT, TBF, TBT, UBT, HYG 股價
    etf_raw = yf.download(["TLT", "TBF", "TBT", "UBT", "HYG"], start=fetch_start, auto_adjust=True)["Close"]
    
    # 2. 從 FRED 下載 DGS2 (2年期美債利率) 與 DFF (Effective Fed Funds Rate)
    dgs2_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
    dff_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    
    dgs2 = pd.read_csv(dgs2_url, index_col=0, parse_dates=True)
    dff = pd.read_csv(dff_url, index_col=0, parse_dates=True)
    
    rates = pd.concat([dgs2, dff], axis=1)
    rates.columns = ["US2Y", "EFFR"]
    rates = rates.replace(".", np.nan).astype(float).ffill()
    
    # 合併數據並對齊交易日
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

# 篩選至結束日期
df_daily = df_daily_all.loc[:pd.to_datetime(end_date)].copy()

# --- 策略邏輯計算 ---
if is_monthly:
    # 保留每月最後一個實際交易日的日期作為索引（而非月末日曆日），方便核對真實交易日
    df = df_daily.groupby(df_daily.index.to_period("M")).apply(lambda x: x.iloc[-1])
    df.index = pd.to_datetime(df.index.to_timestamp(how="end").date)
    # 取得每月實際最後交易日字串供顯示
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
# 持倉代碼：1 = long_ticker (TLT/UBT), -1 = short_ticker (TBF/TBT/HYG), 0 = 現金, 2 = 1倍 TLT
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

# --- 1. 最新訊號與執行時間軸看板（最醒目呈現） ---
st.subheader("🎯 最新一期訊號與實戰執行時間軸")

last_row = df_bt.iloc[-1]
signal_gen_date = pd.to_datetime(last_row["Actual_Trade_Date"])

# 計算訊號適用日期區間
if is_monthly:
    next_month_period = signal_gen_date.to_period("M") + 1
    # 判斷最後一筆數據是否為當月尚未結束的盤中/月中數據，或已收盤的月底數據
    next_bday = (signal_gen_date + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
    apply_period_str = f"{next_month_period.strftime('%Y 年 %m 月')}（自次一交易日 {next_bday} 起持有整月）"
else:
    next_bday = (signal_gen_date + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
    apply_period_str = f"{next_bday}（次一交易日開盤生效至收盤）"

def code_to_label(code):
    if code == 1:
        return f"買進並持有 {long_ticker} ({'2倍' if is_2x else '1倍'}做多美債)"
    elif code == -1:
        return f"買進並持有 {short_ticker} ({'高收益債替代' if use_hyg_for_bear else ('2倍' if is_2x else '1倍') + '做空美債'})"
    elif code == 2:
        return "買進並持有 TLT (盤整保守持倉)"
    else:
        return "空手觀望 (持有現金)"

latest_action = code_to_label(last_row["Target_Signal"])

col1, col2, col3 = st.columns(3)
with col1:
    st.metric(
        label="📅 訊號發生／確認日期 (收盤)",
        value=signal_gen_date.strftime("%Y-%m-%d"),
        help="依據此交易日收盤後的價格與利率數據計算出最新訊號。"
    )
with col2:
    st.metric(
        label="🚀 訊號適用／進場時間",
        value=next_month_period.strftime("%Y-%m 月份") if is_monthly else next_bday,
        help="為避免未來函數，訊號於發生日的「次一交易日」起正式適用。"
    )
with col3:
    st.metric(
        label="💼 最新建議持有標的",
        value=long_ticker if last_row["Target_Signal"] == 1 else (short_ticker if last_row["Target_Signal"] == -1 else ("TLT" if last_row["Target_Signal"] == 2 else "現金 (Cash)"))
    )

regime_detail = (
    f"🔥 **趨勢盤**（`|US2Y {last_row['US2Y']:.2f}% - EFFR {last_row['EFFR']:.2f}%| = {last_row['Rate_Spread']:.2f}% ≥ {spread_threshold:.2f}%`）"
    if last_row["Is_Trend"]
    else f"💤 **盤整盤**（`|US2Y {last_row['US2Y']:.2f}% - EFFR {last_row['EFFR']:.2f}%| = {last_row['Rate_Spread']:.2f}% < {spread_threshold:.2f}%`）"
) if use_macro_filter else "🌐 **未啟用宏觀濾網**（全天候均線模式）"

st.info(
    f"* **訊號發生日（{signal_gen_date.strftime('%Y-%m-%d')} 收盤數據）**：`{signal_basis}` = **`{last_row['Indicator']:.4f}`**，{ma_window}M 均線 = **`{last_row['Indicator_MA']:.4f}`**（{'大於均線' if last_row['Indicator'] > last_row['Indicator_MA'] else '小於均線'}）\n"
    f"* **宏觀環境判定**：{regime_detail}\n"
    f"* **訊號適用期間**：**{apply_period_str}** ➡️ 執行動作：**{latest_action}**"
)

# --- 2. 績效總覽表格 ---
st.subheader("📊 策略與基準 (Buy & Hold TLT) 績效對比")
metrics_df = pd.DataFrame({
    strat_title: strat_metrics,
    "Benchmark (買入持有 TLT)": bm_metrics
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
    mode="lines", name="Benchmark (Buy & Hold TLT)",
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
    mode="lines", name="TLT 回撤 (%)",
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

# --- 6. 訊號指標與均線走勢圖 ---
chart_num = "4️⃣" if use_macro_filter else "3️⃣"
st.subheader(f"{chart_num} {signal_basis} 走勢與 {ma_window} 個月移動平均線")
fig_ind = go.Figure()
fig_ind.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Indicator"],
    mode="lines", name=signal_basis,
    line=dict(color="#17becf", width=2.2)
))
fig_ind.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Indicator_MA"],
    mode="lines", name=f"{ma_window} 個月均線 ({ma_window}M SMA)",
    line=dict(color="#ff7f0e", width=2)
))

cross_above = df_bt[(df_bt["Indicator"] > df_bt["Indicator_MA"]) & (df_bt["Indicator"].shift(1) <= df_bt["Indicator_MA"].shift(1))]
cross_below = df_bt[(df_bt["Indicator"] < df_bt["Indicator_MA"]) & (df_bt["Indicator"].shift(1) >= df_bt["Indicator_MA"].shift(1))]

if signal_basis in ["TBF", "TBT"]:
    bull_pts, bear_pts = cross_below, cross_above
    bull_label, bear_label = f"跌破均線 (偏多 {long_ticker})", f"突破均線 (偏空 {short_ticker})"
else:
    bull_pts, bear_pts = cross_above, cross_below
    bull_label, bear_label = f"突破均線 (偏多 {long_ticker})", f"跌破均線 (偏空 {short_ticker})"

fig_ind.add_trace(go.Scatter(
    x=bull_pts.index, y=bull_pts["Indicator"],
    mode="markers", name=bull_label,
    marker=dict(symbol="triangle-up", size=10, color="#2ca02c")
))
fig_ind.add_trace(go.Scatter(
    x=bear_pts.index, y=bear_pts["Indicator"],
    mode="markers", name=bear_label,
    marker=dict(symbol="triangle-down", size=10, color="#d62728")
))

add_range_shading(fig_ind, df_bt)
fig_ind.update_layout(
    hovermode="x unified",
    yaxis_title=f"{signal_basis} 數值",
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

audit_df = pd.DataFrame({
    "訊號確認日 (實際交易日)": df_bt["Actual_Trade_Date"],
    f"{long_ticker} 還原收盤": df_bt[long_ticker].round(2),
    f"{short_ticker} 還原收盤": df_bt[short_ticker].round(2),
    f"指標 ({signal_basis})": df_bt["Indicator"].round(4),
    f"{ma_window}M 均線": df_bt["Indicator_MA"].round(4),
    "US2Y (%)": df_bt["US2Y"].round(2),
    "EFFR (%)": df_bt["EFFR"].round(2),
    "利差 |US2Y-EFFR| (%)": df_bt["Rate_Spread"].round(2),
    "市場狀態": np.where(df_bt["Is_Trend"], "趨勢盤", "盤整盤") if use_macro_filter else "無濾網",
    "本期實際持有標的 (依上期訊號)": df_bt["Pos"].apply(short_pos_name),
    "本期策略報酬 (%)": (df_bt["Strategy_Ret"] * 100).round(2),
    "本期 TLT 報酬 (%)": (df_bt["BM_Ret"] * 100).round(2),
    "策略累積淨值": df_bt["Strategy_Equity"].round(4),
    "TLT 累積淨值": df_bt["BM_Equity"].round(4),
    "本期收盤產生新訊號 (下期適用)": df_bt["Target_Signal"].apply(short_pos_name),
})

# 依日期由新到舊排列，方便直接核對近期數字
audit_df_display = audit_df.iloc[::-1].reset_index(drop=True)

st.dataframe(audit_df_display, use_container_width=True, height=450)

# 提供 CSV 下載按鈕方便在 Excel 驗算
csv_data = audit_df_display.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    label="📥 下載完整計算明細 CSV (可用 Excel 打開驗算)",
    data=csv_data,
    file_name=f"tlt_strategy_audit_{datetime.today().strftime('%Y%m%d')}.csv",
    mime="text/csv"
)
