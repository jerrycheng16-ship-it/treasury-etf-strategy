import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime

# 頁面基本設定
st.set_page_config(page_title="美債 ETF 動能與宏觀濾網回測系統", layout="wide")
st.title("📈 美債 ETF 均線輪動 ＋ 宏觀利差濾網回測系統")

# --- 側邊欄參數設定 ---
st.sidebar.header("⚙️ 1. 策略核心設定")
signal_source = st.sidebar.selectbox(
    "選擇訊號判斷指標 (相對於均線)",
    options=["TLT / TBF", "TLT / TBT", "TLT", "TBF", "TBT"],
    index=0,
    help="若選用 TBF 或 TBT 單一標的判斷，當其站上均線代表利率上行(空債)，將買入 TBF；跌破均線則買入 TLT。"
)

use_macro_filter = st.sidebar.checkbox("啟用宏觀利差濾網 (|US2Y - EFFR|)", value=True)

if use_macro_filter:
    spread_threshold = st.sidebar.slider("趨勢盤利差門檻 (%)", min_value=0.10, max_value=1.50, value=0.50, step=0.05)
    range_mode = st.sidebar.radio(
        "盤整盤 (< 利差門檻) 應對模式",
        options=["空手持有現金 (報酬率 0%)", "單純持有 TLT (忽略均線訊號)"],
        index=0
    )
else:
    spread_threshold = 0.50
    range_mode = "不適用"

st.sidebar.header("📅 2. 回測週期與區間")
start_date = st.sidebar.date_input("起始日期", value=pd.to_datetime("2010-01-01"))
end_date = st.sidebar.date_input("結束日期", value=datetime.today())

rebalance_freq = st.sidebar.radio(
    "訊號確認與換倉頻率",
    options=["月頻 (每月最後交易日收盤確認，次月持有)", "日頻 (每日收盤確認，次日持有)"],
    index=0
)
ma_window = st.sidebar.slider("均線週期 (月數 / 約當交易日)", min_value=3, max_value=24, value=12)

# 動態顯示目前策略邏輯說明
is_inverse_indicator = signal_source in ["TBF", "TBT"]
bull_condition = f"`{signal_source}` **< 均線**" if is_inverse_indicator else f"`{signal_source}` **> 均線**"
bear_condition = f"`{signal_source}` **> 均線**" if is_inverse_indicator else f"`{signal_source}` **< 均線**"

filter_desc = (
    f"* **宏觀濾網（已啟用）**：當 `|US2Y - EFFR| ≥ {spread_threshold:.2f}%`（趨勢盤）才啟動均線輪動；否則進入盤整盤（**{range_mode.split(' ')[0]}**）。"
    if use_macro_filter
    else "* **宏觀濾網（已關閉）**：全時段執行均線輪動策略，不區分趨勢與盤整。"
)

st.markdown(f"""
**目前執行邏輯：**
* **均線輪動**：當 {bull_condition} 時買入 **TLT**（做多長債）；當 {bear_condition} 時買入 **TBF**（1倍做空長債）。
{filter_desc}
""")

# --- 數據下載與快取 ---
@st.cache_data(ttl=3600)
def load_all_data(start, end):
    buffer_start = pd.to_datetime(start) - pd.Timedelta(days=450)
    
    # 1. 同時下載 TLT, TBF, TBT 股價
    etf_raw = yf.download(["TLT", "TBF", "TBT"], start=buffer_start, end=end, auto_adjust=True)["Close"]
    
    # 2. 從 FRED 下載 DGS2 (2年期美債利率) 與 DFF (Effective Fed Funds Rate)
    dgs2_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
    dff_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    
    dgs2 = pd.read_csv(dgs2_url, index_col=0, parse_dates=True)
    dff = pd.read_csv(dff_url, index_col=0, parse_dates=True)
    
    rates = pd.concat([dgs2, dff], axis=1)
    rates.columns = ["US2Y", "EFFR"]
    rates = rates.replace(".", np.nan).astype(float).ffill()
    
    combined = etf_raw.join(rates, how="left").ffill().dropna()
    return combined

try:
    df_daily = load_all_data(start_date, end_date)
except Exception as e:
    st.error(f"數據下載失敗（請檢查網路或 FRED 連線）：{e}")
    st.stop()

if df_daily.empty or len(df_daily) < 260:
    st.warning("選取的日期區間數據不足，請將起始日期調早（TBF 成立於 2009 年 8 月）。")
    st.stop()

# --- 策略邏輯計算 ---
if "月頻" in rebalance_freq:
    df = df_daily.resample("ME").last()
    periods_per_year = 12
    window = ma_window
else:
    df = df_daily.copy()
    periods_per_year = 252
    window = ma_window * 21

# 根據選擇建立判斷指標序列
if signal_source == "TLT / TBF":
    df["Indicator"] = df["TLT"] / df["TBF"]
elif signal_source == "TLT / TBT":
    df["Indicator"] = df["TLT"] / df["TBT"]
elif signal_source == "TLT":
    df["Indicator"] = df["TLT"]
elif signal_source == "TBF":
    df["Indicator"] = df["TBF"]
elif signal_source == "TBT":
    df["Indicator"] = df["TBT"]

df["Indicator_MA"] = df["Indicator"].rolling(window=window).mean()
df["Rate_Spread"] = (df["US2Y"] - df["EFFR"]).abs()

# 判定市場狀態：若未啟用濾網，則全時段視為 True (趨勢盤)
if use_macro_filter:
    df["Is_Trend"] = df["Rate_Spread"] >= spread_threshold
else:
    df["Is_Trend"] = True

# 產生純均線輪動訊號：1 = 買入 TLT, -1 = 買入 TBF
if is_inverse_indicator:
    # TBF 或 TBT 為反向 ETF：價格低於均線代表美債走多(買 TLT)，高於均線代表美債走空(買 TBF)
    trend_signal = np.where(df["Indicator"] < df["Indicator_MA"], 1, -1)
else:
    # TLT/TBF, TLT/TBT, TLT 為正向指標：大於均線買 TLT，小於均線買 TBF
    trend_signal = np.where(df["Indicator"] > df["Indicator_MA"], 1, -1)

# 結合濾網狀態決定最終持倉代碼 (1=TLT, -1=TBF, 0=現金)
if use_macro_filter:
    range_signal = 0 if "空手" in range_mode else 1
    df["Target_Signal"] = np.where(df["Is_Trend"], trend_signal, range_signal)
else:
    df["Target_Signal"] = trend_signal

# 計算各標的單期報酬率
df["TLT_Ret"] = df["TLT"].pct_change()
df["TBF_Ret"] = df["TBF"].pct_change()

# 避免未來函數：用上一期的 Target_Signal 決定這一期的報酬
df["Pos"] = df["Target_Signal"].shift(1)

conditions = [df["Pos"] == 1, df["Pos"] == -1, df["Pos"] == 0]
choices = [df["TLT_Ret"], df["TBF_Ret"], 0.0]
df["Strategy_Ret"] = np.select(conditions, choices, default=np.nan)
df["BM_Ret"] = df["TLT_Ret"]

# 過濾回測起始日之後的數據並清除空值
df_bt = df.loc[pd.to_datetime(start_date):].dropna(subset=["Strategy_Ret", "BM_Ret", "Indicator_MA"]).copy()

if df_bt.empty:
    st.warning("有效回測區間為空，請調整起始日期。")
    st.stop()

# 計算累積淨值與回撤
df_bt["Strategy_Equity"] = (1 + df_bt["Strategy_Ret"]).cumprod()
df_bt["BM_Equity"] = (1 + df_bt["BM_Ret"]).cumprod()

df_bt["Strategy_DD"] = df_bt["Strategy_Equity"] / df_bt["Strategy_Equity"].cummax() - 1
df_bt["BM_DD"] = df_bt["BM_Equity"] / df_bt["BM_Equity"].cummax() - 1

# 輔助函數：在指定圖表加上盤整盤灰色陰影（僅在啟用濾網時繪製）
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

strat_metrics = calc_metrics(df_bt["Strategy_Ret"], df_bt["Strategy_Equity"], df_bt["Strategy_DD"], periods_per_year, df_bt["Pos"])
bm_metrics = calc_metrics(df_bt["BM_Ret"], df_bt["BM_Equity"], df_bt["BM_DD"], periods_per_year)

# --- 1. 績效總覽與最新訊號 ---
st.subheader("📊 策略與基準 (Buy & Hold TLT) 績效對比")
strat_col_name = f"策略 ({signal_source} + {'利差濾網' if use_macro_filter else '無濾網'})"
metrics_df = pd.DataFrame({
    strat_col_name: strat_metrics,
    "Benchmark (買入持有 TLT)": bm_metrics
})
st.table(metrics_df)

# 最新訊號狀態看板
last_row = df_bt.iloc[-1]
trend_pct = df_bt["Is_Trend"].mean()

if last_row["Target_Signal"] == 1:
    action_str = "買入並持有 **TLT** (做多長債)"
elif last_row["Target_Signal"] == -1:
    action_str = "買入並持有 **TBF** (1倍做空長債)"
else:
    action_str = "**空手觀望** (持有現金)"

if use_macro_filter:
    regime_str = (
        f"🔥 **趨勢盤** (|US2Y - EFFR| = `{last_row['Rate_Spread']:.2f}%` ≥ `{spread_threshold:.2f}%`)"
        if last_row["Is_Trend"]
        else f"💤 **盤整盤** (|US2Y - EFFR| = `{last_row['Rate_Spread']:.2f}%` < `{spread_threshold:.2f}%`)"
    )
    macro_line = f"* **市場狀態判定**：US2Y `{last_row['US2Y']:.2f}%` / EFFR `{last_row['EFFR']:.2f}%` ➡️ {regime_str}（回測期間趨勢盤佔比 `{trend_pct:.1%}`）\n"
else:
    macro_line = f"* **市場狀態判定**：已關閉宏觀濾網（目前 US2Y `{last_row['US2Y']:.2f}%` / EFFR `{last_row['EFFR']:.2f}%`，利差 `{last_row['Rate_Spread']:.2f}%`）\n"

st.info(
    f"💡 **最新訊號狀態 ({df_bt.index[-1].strftime('%Y-%m-%d')})**：\n\n"
    f"{macro_line}"
    f"* **均線動能指標**：`{signal_source}` 數值 `{last_row['Indicator']:.4f}` vs {ma_window}M 均線 `{last_row['Indicator_MA']:.4f}`\n"
    f"* **最終建議配置**：➡️ {action_str}"
)

# --- 2. 累積淨值走勢圖 ---
eq_title = "1️⃣ 累積淨值走勢圖 (灰色陰影為盤整盤)" if use_macro_filter else "1️⃣ 累積淨值走勢圖"
st.subheader(eq_title)
fig_eq = go.Figure()
fig_eq.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Strategy_Equity"],
    mode="lines", name=strat_col_name,
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

# --- 3. 歷史回撤走勢圖 ---
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

# --- 4. 市場狀態判定圖：趨勢盤 vs 盤整盤 ---
st.subheader("3️⃣ 市場狀態判定圖：趨勢盤 vs 盤整盤 (|US2Y - EFFR|)")
fig_regime = go.Figure()

fig_regime.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Rate_Spread"],
    mode="lines", name="|US2Y - EFFR| 利差 (%)",
    line=dict(color="#9467bd", width=2),
    fill="tozeroy", fillcolor="rgba(148, 103, 189, 0.15)"
))

# 依門檻線標示趨勢與盤整區間點位（供參考）
above_thresh = df_bt[df_bt["Rate_Spread"] >= spread_threshold]
below_thresh = df_bt[df_bt["Rate_Spread"] < spread_threshold]

fig_regime.add_trace(go.Scatter(
    x=above_thresh.index, y=above_thresh["Rate_Spread"],
    mode="markers", name=f"趨勢盤區間 (≥ {spread_threshold:.2f}%)",
    marker=dict(color="#2ca02c", size=6)
))
fig_regime.add_trace(go.Scatter(
    x=below_thresh.index, y=below_thresh["Rate_Spread"],
    mode="markers", name=f"盤整盤區間 (< {spread_threshold:.2f}%)",
    marker=dict(color="#8c8c8c", size=5)
))

fig_regime.add_hline(
    y=spread_threshold, line_dash="dash", line_color="#d62728", line_width=2,
    annotation_text=f"門檻 {spread_threshold:.2f}% ({'啟用中' if use_macro_filter else '未啟用，僅供參考'})",
    annotation_position="top left"
)

fig_regime.update_layout(
    hovermode="x unified",
    yaxis_title="利差絕對值 (%)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=400
)
st.plotly_chart(fig_regime, use_container_width=True)

# --- 5. 判斷指標與均線走勢圖 ---
st.subheader(f"4️⃣ {signal_source} 與 {ma_window} 個月移動平均線走勢圖")
fig_ratio = go.Figure()
fig_ratio.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Indicator"],
    mode="lines", name=f"{signal_source}",
    line=dict(color="#17becf", width=2.2)
))
fig_ratio.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Indicator_MA"],
    mode="lines", name=f"{ma_window} 個月均線 ({ma_window}M SMA)",
    line=dict(color="#ff7f0e", width=2)
))

# 標示向上突破與向下跌破的轉折點
cross_up = df_bt[(df_bt["Indicator"] > df_bt["Indicator_MA"]) & (df_bt["Indicator"].shift(1) <= df_bt["Indicator_MA"].shift(1))]
cross_down = df_bt[(df_bt["Indicator"] < df_bt["Indicator_MA"]) & (df_bt["Indicator"].shift(1) >= df_bt["Indicator_MA"].shift(1))]

up_label = "突破均線 (偏空 TBF)" if is_inverse_indicator else "突破均線 (偏多 TLT)"
down_label = "跌破均線 (偏多 TLT)" if is_inverse_indicator else "跌破均線 (偏空 TBF)"

fig_ratio.add_trace(go.Scatter(
    x=cross_up.index, y=cross_up["Indicator"],
    mode="markers", name=up_label,
    marker=dict(symbol="triangle-up", size=10, color="#d62728" if is_inverse_indicator else "#2ca02c")
))
fig_ratio.add_trace(go.Scatter(
    x=cross_down.index, y=cross_down["Indicator"],
    mode="markers", name=down_label,
    marker=dict(symbol="triangle-down", size=10, color="#2ca02c" if is_inverse_indicator else "#d62728")
))

add_range_shading(fig_ratio, df_bt)
fig_ratio.update_layout(
    hovermode="x unified",
    yaxis_title=f"{signal_source} 數值",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=420
)
st.plotly_chart(fig_ratio, use_container_width=True)
