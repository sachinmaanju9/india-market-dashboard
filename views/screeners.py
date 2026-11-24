"""Screeners page (Step 9). Reads data/screens/metrics_latest.parquet produced by scripts/step9_screens.py."""
from pathlib import Path

import pandas as pd
import streamlit as st

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from app_lib import screens as SC  # noqa: E402
from app_lib import store  # noqa: E402

SHORT = {"Nifty 50": "Nifty 50", "Nifty Smallcap 250": "Smallcap 250", "Nifty Bank": "Bank Nifty"}


@st.cache_data(ttl=3600, show_spinner="Loading screen data…")
def load():
    m = pd.read_parquet(store.path("data/screens/metrics_latest.parquet"))
    b = pd.read_csv(store.path("data/screens/bench_returns.csv"))
    return m, b


try:
    m, bench = load()
except Exception as e:
    st.warning(f"Screen data not available yet ({e}). Run the daily-refresh workflow on GitHub.")
    st.stop()
asof = pd.to_datetime(m["last_date"]).max()
st.title("Screeners")
store.banner(st)
st.caption(f"Prices up to **{asof:%d %b %Y}** · results from yfinance (latest quarter mostly Jul–Sep 2026) · "
           "ownership from NSE shareholding filings")

# ------------------------------------------------------------------ screen-specific columns & controls
PCT = "%.1f"
COLS = {
    "Momentum": [("momentum_score", "Score", None), ("ret_6M", "6M %", PCT), ("ret_12M", "12M %", PCT),
                 ("vol_ann", "Volatility %", PCT), ("pct_from_52w_high", "From 52W high %", PCT),
                 ("new_52w_high_5d", "New 52W high (5d)", None)],
    "Breakout – setting up": [("bb_width_pctile_1y", "Band width pctile (1y)", "%.0f"),
                              ("pct_to_50d_high", "Below 50D high %", PCT), ("rsi14", "RSI", "%.0f")],
    "Breakout – broke out": [("breakout_date", "Breakout date", None), ("days_since_breakout", "Days since", "%.0f"),
                             ("breakout_status", "Status", None), ("breakout_vol_x", "Volume ×avg", "%.1f"),
                             ("breakout_level", "Breakout level", "%.1f")],
    "Quality mean-reversion": [("pct_from_52w_high", "From 52W high %", PCT), ("rsi14", "RSI", "%.0f"),
                               ("pct_vs_50dma", "vs 50 DMA %", PCT), ("roc", "ROCE / ROE %", PCT), ("de", "Debt/Equity", "%.2f")],
    "Quality + Growth": [("roc", "ROCE / ROE %", PCT), ("rev_cagr_3y", "Revenue CAGR 3Y %", PCT),
                         ("pat_cagr_3y", "Profit CAGR 3Y %", PCT), ("de", "Debt/Equity", "%.2f"),
                         ("opm_std", "Margin swing (pts)", PCT), ("cfo_pat_3y", "Cash/Profit 3Y", "%.2f")],
    "Deep value": [("pe", "P/E", PCT), ("ev_ebitda", "EV/EBITDA", PCT), ("pb", "P/B", "%.2f"),
                   ("fcf_yield", "FCF yield %", PCT), ("pe_pctile_sector", "P/E pctile in sector", "%.0f"),
                   ("piotroski", "Piotroski", "%.0f")],
    "Earnings momentum": [("pat_yoy_q", "Profit YoY (qtr) %", PCT), ("rev_yoy_q", "Revenue YoY (qtr) %", PCT),
                          ("pat_growth_compare", "Earlier growth %", PCT), ("accel_basis", "Compared with", None),
                          ("opm_change_q", "Margin change (pts)", PCT)],
    "Piotroski F-score": [("piotroski", "F-score", "%.0f"), ("roc", "ROCE %", PCT), ("de", "Debt/Equity", "%.2f"),
                          ("pe", "P/E", PCT)],
    "Magic Formula": [("mf_earnings_yield", "Earnings yield %", PCT), ("mf_roc", "Return on capital %", PCT),
                      ("pe", "P/E", PCT)],
    "Promoter & ownership": [("promoter_pct", "Promoter %", PCT), ("d_promoter_4q", "Δ Promoter 4Q", "%+.2f"),
                             ("d_fii_4q", "Δ FII 4Q", "%+.2f"), ("d_dii_4q", "Δ DII 4Q", "%+.2f"),
                             ("buy_quarters", "Buying quarters (of 4)", "%.0f"), ("pledge_pct", "Pledged %", PCT)],
}
PARAMS = {
    "Momentum": [("mom_max_from_high", "Max % below 52-week high")],
    "Breakout – setting up": [("bb_pctile_max", "Band width in lowest … % of year"), ("setup_within_pct", "Within … % of 50-day high")],
    "Breakout – broke out": [("breakout_vol_x", "Breakout volume ≥ … × 50-day avg")],
    "Quality mean-reversion": [("mr_dd_min", "Min % below 52W high"), ("mr_dd_max", "Max % below 52W high"),
                               ("mr_rsi_max", "RSI below"), ("mr_below_50dma", "or ≥ … % below 50 DMA"),
                               ("mr_roce_min", "ROCE / ROE ≥ %"), ("mr_de_max", "Debt/Equity ≤")],
    "Quality + Growth": [("qg_roce_min", "ROCE ≥ %"), ("qg_rev_cagr_min", "Revenue CAGR ≥ %"),
                         ("qg_pat_cagr_min", "Profit CAGR ≥ %"), ("qg_de_max", "Debt/Equity ≤"),
                         ("qg_opm_std_max", "Margin swing ≤ pts"), ("qg_cfo_pat_min", "Cash/Profit ≥"),
                         ("qg_roe_fin_min", "Financials: ROE ≥ %"), ("qg_roa_fin_min", "Financials: ROA ≥ %")],
    "Deep value": [("dv_cheapest_pct", "Cheapest … % of sector"), ("dv_fcf_yield_min", "FCF yield ≥ %"),
                   ("dv_de_max", "Debt/Equity ≤"), ("dv_piotroski_min", "Piotroski ≥")],
    "Earnings momentum": [("em_pat_yoy_min", "Profit YoY ≥ %"), ("em_rev_yoy_min", "Revenue YoY ≥ %")],
    "Piotroski F-score": [("piotroski_min", "F-score ≥")],
    "Magic Formula": [("magic_top_n", "Show top N")],
    "Promoter & ownership": [("own_promoter_up", "Promoter up ≥ pts"), ("own_fii_up", "FII up ≥ pts"),
                             ("own_dii_up", "DII up ≥ pts"), ("own_max_sell", "No group down > pts")],
}
HELP = {
    "Momentum": "Strong, steady uptrends: above 200 DMA, 50 DMA above 200 DMA, near the 52-week high, beating Nifty 50 "
                "over 6M. Score = NSE-style momentum (6M & 12M returns divided by volatility).",
    "Breakout – setting up": "Price swings at their tightest of the year, close to the 50-day high, above 200 DMA.",
    "Breakout – broke out": "Closed above the prior 50-day high in the last 5 sessions on ≥1.5× volume with above-normal "
                            "delivery (judged on the breakout day). Failed = back below the breakout level.",
    "Quality mean-reversion": "Good businesses in a temporary dip: long-term trend intact, 15–35% off the high, oversold.",
    "Quality + Growth": "High returns on capital, 3-year growth, low debt, steady margins, profits backed by cash. "
                        "Financials judged on ROE / ROA.",
    "Deep value": "Cheap versus its own sector (P/E and EV/EBITDA; P/B for financials), with value-trap guards.",
    "Earnings momentum": "Latest quarter profit and revenue growth, accelerating, with margins up. Acceleration is versus "
                         "the previous quarter where available, otherwise versus last full year's growth.",
    "Piotroski F-score": "9 yes/no checks of improving profitability, leverage, liquidity and efficiency (non-financials).",
    "Magic Formula": "Greenblatt: rank by earnings yield + return on capital (excl. financials & utilities).",
    "Promoter & ownership": "Any of promoters, FIIs or DIIs buying over 4 quarters, with no group selling heavily. "
                            "Score weights promoters ×2, DIIs ×1.5, FIIs ×1, plus consistency.",
}

c1, c2, c3 = st.columns([3, 2, 2])
with c1:
    choice = st.selectbox("Screen", ["Overview – all screens"] + list(COLS))
with c2:
    period = st.selectbox("Compare returns over", ["1M", "3M", "6M", "12M"], index=2)
with c3:
    hide_flags = st.toggle("Hide red-flagged stocks", value=False)

f1, f2 = st.columns(2)
with f1:
    sectors = st.multiselect("Sector", sorted(m["nse_industry"].dropna().unique()))
with f2:
    tags = st.multiselect("Group", ["N500", "NEXT250", "IPO", "NEWLIST"])

params = dict(SC.DEFAULTS)
if choice in PARAMS:
    with st.expander("Adjust thresholds"):
        cols = st.columns(2)
        for i, (k, label) in enumerate(PARAMS[choice]):
            d = SC.DEFAULTS[k]
            params[k] = cols[i % 2].number_input(label, value=float(d) if isinstance(d, float) else int(d),
                                                 key=f"p_{choice}_{k}")
        params["min_value_cr"] = st.number_input("Liquidity: median daily traded value ≥ ₹ crore",
                                                 value=float(SC.DEFAULTS["min_value_cr"]), key=f"liq_{choice}")

x = m.copy()
if sectors:
    x = x[x["nse_industry"].isin(sectors)]
if tags:
    x = x[x["tag"].isin(tags)]
if hide_flags:
    x = x[x["red_flag_count"] == 0]
x["roc"] = x["roe_3y"].where(x["is_financial"], x["roce_3y"])
x["pct_vs_50dma"] = (x["close"] / x["sma50"] - 1) * 100
x["pct_to_50d_high"] = (x["close"] / x["high_50d_close"] - 1) * 100
x["opm_change_q"] = x["opm_q"] - x["opm_q_yago"]

b = bench.set_index("benchmark")[f"ret_{period}"]
bench6 = bench.set_index("benchmark").loc["Nifty 50", "ret_6M"]
results = SC.all_screens(x, params, bench6m=bench6)

st.markdown("  ·  ".join(f"**{SHORT[k]}** {period}: {v:+.1f}%" for k, v in b.items()))


def with_bench(df):
    df = df.copy()
    df["ret_p"] = df[f"ret_{period}"]
    for k, v in b.items():
        df[f"vs_{k}"] = df["ret_p"] - v
    return df


def show_table(df, key_cols):
    df = with_bench(df).sort_values("score", ascending=False)
    base = [("symbol", "Symbol", None), ("company", "Company", None), ("nse_industry", "Sector", None),
            ("tag", "Group", None), ("close", "Price ₹", "%.1f"), ("score", "Score", None)]
    tail = [("ret_p", f"Return {period} %", "%+.1f")] + \
           [(f"vs_{k}", f"vs {SHORT[k]} ({v:+.1f}%)", "%+.1f") for k, v in b.items()] + \
           [("red_flags", "Red flags", None)]
    spec = base + [c for c in key_cols if c[0] != "momentum_score"] + tail
    spec = [c for c in spec if c[0] in df]
    out = df[[c[0] for c in spec]].rename(columns={c[0]: c[1] for c in spec})
    out.insert(0, "Open", "/stock?symbol=" + df["symbol"].values)
    cfg = {"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
           "Open": st.column_config.LinkColumn("Open", display_text="🔬", width="small",
                                               help="Open the stock deep-dive")}
    for col, label, fmt in spec:
        if fmt and label not in cfg:
            cfg[label] = st.column_config.NumberColumn(label, format=fmt)
    if "Breakout date" in out:
        out["Breakout date"] = pd.to_datetime(out["Breakout date"]).dt.strftime("%d %b")
    out["Score"] = out["Score"].clip(lower=0)
    st.dataframe(out, hide_index=True, width="stretch", column_config=cfg, height=min(36 * (len(out) + 1), 720))
    st.download_button("Download CSV", out.drop(columns="Open").to_csv(index=False).encode(),
                       file_name=f"{choice}.csv", mime="text/csv")


if choice == "Overview – all screens":
    counts = pd.DataFrame({"Screen": list(results), "Stocks": [len(v) for v in results.values()]})
    st.dataframe(counts, hide_index=True, width="stretch")
    hits = pd.concat([v[["symbol"]].assign(screen=k) for k, v in results.items() if len(v)])
    agg = hits.groupby("symbol")["screen"].agg(["count", lambda s: ", ".join(s)]).reset_index()
    agg.columns = ["symbol", "Screens passed", "Which"]
    agg = agg[agg["Screens passed"] >= 3].sort_values("Screens passed", ascending=False)
    st.subheader("Stocks in 3 or more screens")
    if agg.empty:
        st.info("No stock passes 3 or more screens with the current filters.")
    else:
        top = agg.merge(with_bench(x)[["symbol", "company", "nse_industry", "tag", "close", "ret_p", "red_flags"]],
                        on="symbol")
        st.dataframe(top.rename(columns={"company": "Company", "nse_industry": "Sector", "tag": "Group",
                                         "close": "Price ₹", "ret_p": f"Return {period} %", "red_flags": "Red flags"}),
                     hide_index=True, width="stretch",
                     column_config={f"Return {period} %": st.column_config.NumberColumn(format="%+.1f")})
else:
    st.caption(HELP[choice])
    r = results[choice]
    st.markdown(f"**{len(r)} stocks pass**")
    if len(r):
        show_table(r, COLS[choice])
    else:
        st.info("No stock passes with these settings. Try loosening a threshold.")

with st.expander("Red-flag rules"):
    st.markdown("\n".join(f"- {k}" for k in SC.RED_FLAG_RULES) +
                "\n\nFlags are warnings, not exclusions; use the toggle above to hide flagged stocks.")
