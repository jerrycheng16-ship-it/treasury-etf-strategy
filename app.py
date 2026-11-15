import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

# 頁面基本設定
st.set_page_config(page_title="TLT / TBF 宏觀利率濾網輪動策略", layout="wide")
st.title("📈 TLT / TBF 宏觀利差濾網 ＋ 均線輪動策略")
st.markdown("""
**策略邏輯：** 結合 **2年期美債殖利率 (US2Y)** 與 **有效聯邦基金利率 (EFFR)** 的利差作為市場狀態濾網：
* **趨勢盤（|US2Y - EFFR| ≥ 門檻）**：啟動動能輪動，`TLT/TBF` 比率 **> 均線** 買入 **TLT**（做多長債），**< 均線** 買入 **TBF**（1倍做空長債）。
* **區間盤（|US2Y - EFFR| < 門檻）**：忽略均線訊號，切換為 **空手觀望** 或 **單純持有 TLT**。
""")

# --- 側邊欄參數設定 ---
st.sidebar.header("⚙️ 回測與濾網參數")
start_date = st.sidebar.date_input("起始日期", value=pd.to_datetime("2010-01-01"))
end_date = st.sidebar.date_input("結束日期", value=datetime.today())

rebalance_freq = st.sidebar.radio(
    "訊號確認與換倉頻率",
    options=["月頻 (每月最後交易日收盤確認，次月持有)", "日頻 (每日收盤確認，次日持有)"],
    index=0
)

ma_window = st.sidebar.slider("均線週期 (月數 / 約當交易日)", min_value=3, max_value=24, value=12)
spread_threshold = st.sidebar.slider("趨勢盤利差門檻 |US2Y - EFFR| (%)", min_value=0.10, max_value=1.50, value=0.50, step=0.05)

range_mode = st.sidebar.radio(
    "區間盤 (< 利差門檻) 應對模式",
    options=["空手持有現金 (報酬率 0%)", "單純持有 TLT (忽略均線訊號)"],
    index=0
)

# --- 數據下載與快取 ---
@st.cache_data(ttl=3600)
def load_all_data(start, end):
    buffer_start = pd.to_datetime(start) - pd.Timedelta(days=450)
    
    # 1. 下載 TLT 與 TBF 股價
    etf_raw = yf.download(["TLT", "TBF"], start=buffer_start, end=end, auto_adjust=True)["Close"]
    
    # 2. 從 FRED 下載 DGS2 (2年期美債利率) 與 DFF (Effective Fed Funds Rate)
    dgs2_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
    dff_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    
    dgs2 = pd.read_csv(dgs2_url, index_col=0, parse_dates=True)
    dff = pd.read_csv(dff_url, index_col=0, parse_dates=True)
    
    rates = pd.concat([dgs2, dff], axis=1)
    rates.columns = ["US2Y", "EFFR"]
    # 將 FRED 的 '.' 缺值轉為 NaN 並向前填補（假日對齊）
    rates = rates.replace(".", np.nan).astype(float).ffill()
    
    # 合併數據並對齊交易日
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

# 計算比率、均線與利差
df["Ratio"] = df["TLT"] / df["TBF"]
df["Ratio_MA"] = df["Ratio"].rolling(window=window).mean()
df["Rate_Spread"] = (df["US2Y"] - df["EFFR"]).abs()

# 判定市場狀態：True 為趨勢盤，False 為區間盤
df["Is_Trend"] = df["Rate_Spread"] >= spread_threshold

# 決定當期收盤產生的目標持倉代碼：
# 1 = 持有 TLT, -1 = 持有 TBF, 0 = 空手現金
trend_signal = np.where(df["Ratio"] > df["Ratio_MA"], 1, -1)
range_signal = 0 if "空手" in range_mode else 1

df["Target_Signal"] = np.where(df["Is_Trend"], trend_signal, range_signal)

# 計算各標的單期報酬率
df["TLT_Ret"] = df["TLT"].pct_change()
df["TBF_Ret"] = df["TBF"].pct_change()

# 避免未來函數：用上一期的 Target_Signal 決定這一期的報酬
df["Pos"] = df["Target_Signal"].shift(1)

conditions = [
    df["Pos"] == 1,
    df["Pos"] == -1,
    df["Pos"] == 0
]
choices = [
    df["TLT_Ret"],
    df["TBF_Ret"],
    0.0
]
df["Strategy_Ret"] = np.select(conditions, choices, default=np.nan)
df["BM_Ret"] = df["TLT_Ret"]

# 過濾回測起始日之後的數據並清除空值
df_bt = df.loc[pd.to_datetime(start_date):].dropna(subset=["Strategy_Ret", "BM_Ret", "Ratio_MA"]).copy()

if df_bt.empty:
    st.warning("有效回測區間為空，請調整起始日期。")
    st.stop()

# 計算累積淨值與回撤
df_bt["Strategy_Equity"] = (1 + df_bt["Strategy_Ret"]).cumprod()
df_bt["BM_Equity"] = (1 + df_bt["BM_Ret"]).cumprod()

df_bt["Strategy_DD"] = df_bt["Strategy_Equity"] / df_bt["Strategy_Equity"].cummax() - 1
df_bt["BM_DD"] = df_bt["BM_Equity"] / df_bt["BM_Equity"].cummax() - 1

# --- 績效指標計算函數 ---
def calc_metrics(returns, equity, dd, ppy, pos_series=None):
    total_ret = equity.iloc[-1] - 1
    n_periods = len(returns)
    cagr = (equity.iloc[-1]) ** (ppy / n_periods) - 1
    
    # 勝率計算：若有空手期，僅計算實際有進場交易的期數勝率
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
metrics_df = pd.DataFrame({
    "利差濾網＋TLT/TBF 策略": strat_metrics,
    "Benchmark (買入持有 TLT)": bm_metrics
})
st.table(metrics_df)

# 最新訊號狀態看板
last_row = df_bt.iloc[-1]
regime_str = f"🔥 **趨勢盤** (|US2Y - EFFR| = `{last_row['Rate_Spread']:.2f}%` ≥ `{spread_threshold:.2f}%`)" if last_row["Is_Trend"] else f"💤 **區間盤** (|US2Y - EFFR| = `{last_row['Rate_Spread']:.2f}%` < `{spread_threshold:.2f}%`)"

if last_row["Target_Signal"] == 1:
    action_str = "買入並持有 **TLT** (做多長債)"
elif last_row["Target_Signal"] == -1:
    action_str = "買入並持有 **TBF** (1倍做空長債)"
else:
    action_str = "**空手觀望** (持有現金)"

st.info(
    f"💡 **最新訊號狀態 ({df_bt.index[-1].strftime('%Y-%m-%d')})**：\n\n"
    f"* **利率環境**：US2Y `{last_row['US2Y']:.2f}%` / EFFR `{last_row['EFFR']:.2f}%` ➡️ {regime_str}\n"
    f"* **動能指標**：`TLT/TBF` 比率 `{last_row['Ratio']:.4f}` vs 均線 `{last_row['Ratio_MA']:.4f}`\n"
    f"* **建議配置**：➡️ {action_str}"
)

# --- 2. 累積淨值走勢圖（附帶區間盤底色標示） ---
st.subheader("📈 累積淨值走勢圖 (灰色陰影為區間盤)")
fig_eq = go.Figure()
fig_eq.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["Strategy_Equity"],
    mode="lines", name="利差濾網＋TLT/TBF 策略",
    line=dict(color="#1f77b4", width=2.5)
))
fig_eq.add_trace(go.Scatter(
    x=df_bt.index, y=df_bt["BM_Equity"],
    mode="lines", name="Benchmark (Buy & Hold TLT)",
    line=dict(color="#7f7f7f", width=2, dash="dash")
))

# 標示區間盤背景陰影
in_range = False
range_start = None
for idx, row in df_bt.iterrows():
    if not row["Is_Trend"] and not in_range:
        in_range = True
        range_start = idx
    elif row["Is_Trend"] and in_range:
        in_range = False
        fig_eq.add_vrect(x0=range_start, x1=idx, fillcolor="gray", opacity=0.12, line_width=0)
if in_range:
    fig_eq.add_vrect(x0=range_start, x1=df_bt.index[-1], fillcolor="gray", opacity=0.12, line_width=0)

fig_eq.update_layout(
    hovermode="x unified",
    yaxis_title="累積淨值",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=460
)
st.plotly_chart(fig_eq, use_container_width=True)
