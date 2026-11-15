import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime

# 頁面基本設定
st.set_page_config(page_title="TLT / TBF 動能輪動策略回測", layout="wide")
st.title("📈 TLT / TBF 比率均線輪動策略回測系統")
st.markdown("""
**策略邏輯：** 計算 `TLT / TBF` 價格比率及其 12 個月移動平均線（12M SMA）。
* 當 **比率 > 12M SMA**：持有 **TLT**（做多長天期美債）
* 當 **比率 < 12M SMA**：持有 **TBF**（做空長天期美債）
""")

# --- 側邊欄參數設定 ---
st.sidebar.header("⚙️ 回測參數設定")
start_date = st.sidebar.date_input("起始日期", value=pd.to_datetime("2010-01-01"))
end_date = st.sidebar.date_input("結束日期", value=datetime.today())
rebalance_freq = st.sidebar.radio(
    "訊號確認與換倉頻率",
    options=["月頻 (每月最後交易日收盤確認，次月持有)", "日頻 (每日收盤確認，次日持有)"],
    index=0
)
ma_window = st.sidebar.slider("均線週期 (月數 / 約當交易日)", min_value=3, max_value=24, value=12)

# --- 數據下載與快取 ---
@st.cache_data(ttl=3600)
def load_data(start, end):
    tickers = ["TLT", "TBF"]
    # 往前多抓 400 天以確保初期均線計算完整
    buffer_start = pd.to_datetime(start) - pd.Timedelta(days=450)
    raw = yf.download(tickers, start=buffer_start, end=end, auto_adjust=True)["Close"]
    raw = raw.dropna()
    return raw

try:
    df_daily = load_data(start_date, end_date)
except Exception as e:
    st.error(f"數據下載失敗：{e}")
    st.stop()

if df_daily.empty or len(df_daily) < 260:
    st.warning("選取的日期區間數據不足，請將起始日期調早（TBF 成立於 2009 年 8 月）。")
    st.stop()

# --- 策略邏輯計算 ---
if "月頻" in rebalance_freq:
    # 轉換為月線（取每月最後一個交易日收盤價）
    df = df_daily.resample("ME").last()
    periods_per_year = 12
    window = ma_window
else:
    # 維持日線（1個月約 21 個交易日）
    df = df_daily.copy()
    periods_per_year = 252
    window = ma_window * 21

# 計算比率與移動平均線
df["Ratio"] = df["TLT"] / df["TBF"]
df["Ratio_MA"] = df["Ratio"].rolling(window=window).mean()

# 產生訊號：1 代表持有 TLT，-1 代表持有 TBF
df["Signal"] = np.where(df["Ratio"] > df["Ratio_MA"], 1, -1)

# 計算各標的單期報酬率
df["TLT_Ret"] = df["TLT"].pct_change()
df["TBF_Ret"] = df["TBF"].pct_change()

# 避免未來函數：用上一期的 Signal 決定這一期的報酬
df["Pos"] = df["Signal"].shift(1)
df["Strategy_Ret"] = np.where(df["Pos"] == 1, df["TLT_Ret"], df["TBF_Ret"])
df["BM_Ret"] = df["TLT_Ret"]

# 過濾回測起始日之後的數據並清除空值
df_bt = df.loc[pd.to_datetime(start_date):].dropna(subset=["Strategy_Ret", "BM_Ret", "Ratio_MA"]).copy()

if df_bt.empty:
    st.warning("有效回測區間為空，請調整起始日期。")
    st.stop()

# 計算累積淨值（起始設為 1）
df_bt["Strategy_Equity"] = (1 + df_bt["Strategy_Ret"]).cumprod()
df_bt["BM_Equity"] = (1 + df_bt["BM_Ret"]).cumprod()

# 計算回撤 (Drawdown)
df_bt["Strategy_DD"] = df_bt["Strategy_Equity"] / df_bt["Strategy_Equity"].cummax() - 1
df_bt["BM_DD"] = df_bt["BM_Equity"] / df_bt["BM_Equity"].cummax() - 1

# --- 績效指標計算函數 ---
def calc_metrics(returns, equity, dd, ppy):
    total_ret = equity.iloc[-1] - 1
    n_periods = len(returns)
    cagr = (equity.iloc[-1]) ** (ppy / n_periods) - 1
    win_rate = (returns > 0).sum() / n_periods
    max_dd = dd.min()
    ann_vol = returns.std() * np.sqrt(ppy)
    sharpe = (cagr / ann_vol) if ann_vol != 0 else 0
    return {
        "總報酬率": f"{total_ret:.2%}",
        "年化報酬率 (CAGR)": f"{cagr:.2%}",
        "勝率 (單期正報酬比例)": f"{win_rate:.2%}",
        "最大回撤 (MDD)": f"{max_dd:.2%}",
        "年化波動度": f"{ann_vol:.2%}",
        "夏普值 (無風險利率=0%)": f"{sharpe:.2f}"
    }

strat_metrics = calc_metrics(df_bt["Strategy_Ret"], df_bt["Strategy_Equity"], df_bt["Strategy_DD"], periods_per_year)
bm_metrics = calc_metrics(df_bt["BM_Ret"], df_bt["BM_Equity"], df_bt["BM_DD"], periods_per_year)

# --- 1. 績效總覽表格 ---
st.subheader("📊 策略與基準 (Buy & Hold TLT) 績效對比")
metrics_df = pd.DataFrame({
    "TLT/TBF 輪動策略": strat_metrics,
    "Benchmark (買入持有 TLT)": bm_metrics
})
st.table(metrics_df)

# 顯示目前最新狀態
latest_signal = "買入並持有 TLT (做多美債)" if df_bt["Signal"].iloc[-1] == 1 else "買入並持有 TBF (做空美債)"
st.info(f"💡 **最新訊號狀態 ({df_bt.index[-1].strftime('%Y-%m-%d')})**：比率為 `{df_bt['Ratio'].iloc[-1]:.4f}`，均線為 `{df_bt['Ratio_MA'].iloc[-1]:.4f}` ➡️ 建議配置：**{latest_signal}**")

# --- 2. 累積淨值走勢圖 ---
st.subheader("📈 累積淨值走勢圖 (Initial = 1.0)")
fig_eq = go.Figure()
fig_eq.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Strategy_Equity"],
    mode="lines", name="TLT/TBF 輪動策略",
    line=dict(color="#1f77b4", width=2.5)
))
fig_eq.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["BM_Equity"],
    mode="lines", name="Benchmark (Buy & Hold TLT)",
    line=dict(color="#7f7f7f", width=2, dash="dash")
))
fig_eq.update_layout(
    hovermode="x unified",
    yaxis_title="累積淨值",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=450
)
st.plotly_chart(fig_eq, use_container_width=True)

# --- 3. 回撤走勢圖 (Underwater Plot) ---
st.subheader("📉 歷史回撤走勢圖 (Drawdown)")
fig_dd = go.Figure()
fig_dd.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Strategy_DD"] * 100,
    mode="lines", name="策略回撤 (%)",
    fill="tozeroy", line=dict(color="#d62728", width=1.5)
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
    height=400
)
st.plotly_chart(fig_dd, use_container_width=True)

# --- 4. TLT/TBF 比率與 12M 均線走勢圖 ---
st.subheader("🔍 TLT / TBF 價格比率與移動平均線")
fig_ratio = go.Figure()
fig_ratio.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Ratio"],
    mode="lines", name="TLT / TBF 比率",
    line=dict(color="#2ca02c", width=2)
))
fig_ratio.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Ratio_MA"],
    mode="lines", name=f"{ma_window} 個月移動平均線",
    line=dict(color="#ff7f0e", width=2)
))
fig_ratio.update_layout(
    hovermode="x unified",
    yaxis_title="價格比率",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=400
)
st.plotly_chart(fig_ratio, use_container_width=True)
