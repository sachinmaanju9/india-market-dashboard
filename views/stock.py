"""Stock deep-dive page (Step 10). Open via the search box or ?symbol=XYZ (links from the screeners)."""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
import streamlit as st  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from app_lib import screens as SC  # noqa: E402
from app_lib import sector as SEC  # noqa: E402
from app_lib import intraday as I  # noqa: E402
from app_lib import store  # noqa: E402
from app_lib.prices import load_adjusted  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
BENCH_STYLE = {"Nifty 50": ("#0b0b0b", "dash"), "Nifty Smallcap 250": ("#52514e", "dot"),
               "Nifty Bank": ("#898781", "dashdot")}
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
GOOD, BAD = "#1c5cab", "#b3262c"


def layout(fig, height=380, y_title=None):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=30, b=8), plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
                      font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK2, size=12),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), hovermode="x unified",
                      hoverlabel=dict(bgcolor="white", font_color=INK))
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=AXIS, tickfont=dict(color=MUTED),
                     title=dict(text=y_title, font=dict(color=MUTED)) if y_title else None)
    return fig


# ------------------------------------------------------------------ data (cached)
@st.cache_data(ttl=3600, show_spinner=False)
def base():
    m = pd.read_parquet(store.path("data/screens/metrics_latest.parquet"))
    master = pd.read_csv("data/universe/master.csv")
    bench = pd.read_csv(store.path("data/screens/bench_returns.csv"))
    return m, master, bench


@st.cache_data(ttl=3600, show_spinner=False)
def screens_passed():
    m, _, bench = base()
    res = SC.all_screens(m, bench6m=bench.set_index("benchmark").loc["Nifty 50", "ret_6M"])
    out = {}
    for k, v in res.items():
        for sym, sc in zip(v["symbol"], v["score"]):
            out.setdefault(sym, []).append((k, max(float(sc), 0)))
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def prices(isin):
    return load_adjusted([isin]).sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def statements(symbol):
    try:
        f = store.path("data/fundamentals/statements_inr.parquet")
    except Exception:
        return pd.DataFrame(columns=["symbol", "statement", "freq", "item", "period_end", "value"])
    return pq.read_table(f, filters=[("symbol", "=", symbol)]).to_pandas()


@st.cache_data(ttl=3600, show_spinner=False)
def indices():
    return SEC.load_closes("data/indices")


@st.cache_data(ttl=3600, show_spinner=False)
def ownership(symbol):
    o = pd.read_parquet("data/fundamentals/ownership.parquet")
    o = o[(o["symbol"] == symbol) & (o["parsed_ok"] == True)]  # noqa: E712
    return o.sort_values("quarter_end").drop_duplicates("quarter_end", keep="last")


@st.cache_data(ttl=3600, show_spinner=False)
def actions(isin):
    f = _P("data/prices/corporate_actions.csv")
    if not f.exists():
        return pd.DataFrame()
    ca = pd.read_csv(f, parse_dates=["ex_date"])
    return ca[(ca["isin"] == isin) & ca["status"].isin(["applied", "manual_apply"])].sort_values("ex_date")


m, master, bench = base()
m = m[m["close"].notna()]
labels = (m["symbol"] + " — " + m["company"].fillna("")).tolist()
qp = st.query_params.get("symbol")
default = next((i for i, s in enumerate(m["symbol"]) if s == qp), next(
    (i for i, s in enumerate(m["symbol"]) if s == "RELIANCE"), 0))
store.banner(st)
pick = st.selectbox("Search a stock", labels, index=default)
sym = pick.split(" — ")[0]
st.query_params["symbol"] = sym
r = m[m["symbol"] == sym].iloc[0]
isin = r["isin"]
px = prices(isin)
mrow = master[master["isin"] == isin].iloc[0]

# ------------------------------------------------------------------ header
chg = (px["close"].iloc[-1] / px["close"].iloc[-2] - 1) * 100 if len(px) > 1 else np.nan
st.title(f"{r['company']}")
st.caption(f"**{sym}** · {r['nse_industry']} · {r.get('basic_industry') or '–'} · group **{r['tag']}** · "
           f"prices to {pd.to_datetime(r['last_date']):%d %b %Y}")
tiles = [("Price ₹", f"{r['close']:,.1f}",
          f"<span style='color:{GOOD if chg >= 0 else BAD}'>{chg:+.2f}% today</span>" if pd.notna(chg) else ""),
         ("Market cap ₹ cr", f"{r['mcap_cr']:,.0f}" if pd.notna(r.get("mcap_cr")) else "–", ""),
         ("P/E", f"{r['pe']:.1f}" if pd.notna(r.get("pe")) else "–", ""),
         ("From 52W high", f"{r['pct_from_52w_high']:+.1f}%", "")]
st.markdown("<div style='display:flex;flex-wrap:wrap;gap:10px 24px;margin:2px 0 10px'>" + "".join(
    f"<div style='flex:1 1 130px'><div style='color:{INK2};font-size:0.8rem'>{k}</div>"
    f"<div style='font-size:1.45rem;color:{INK}'>{v}</div><div style='font-size:0.8rem'>{d}</div></div>"
    for k, v, d in tiles) + "</div>", unsafe_allow_html=True)

tabs = st.tabs(["Summary", "Today", "Price", "Relative", "Technicals", "Financials", "Valuation", "Ownership",
                "Peers", "Scores", "Events & notes"])
TODAY_TAB = tabs[1]
tabs = [tabs[0]] + list(tabs[2:])


@st.cache_data(ttl=180, show_spinner=False)
def live(symbol):
    got = I.fetch([f"{symbol}.NS"], interval="5m", period="2d")
    return got.get(f"{symbol}.NS"), I.now_ist()

# ------------------------------------------------------------------ 1 Summary
with tabs[0]:
    lo, hi, cl = r["low_52w"], r["high_52w"], r["close"]
    pos = (cl - lo) / (hi - lo) * 100 if hi > lo else 50
    st.markdown(f"**52-week range** ₹{lo:,.1f} – ₹{hi:,.1f}")
    st.progress(min(max(pos / 100, 0), 1.0), text=f"Now ₹{cl:,.1f} · {pos:.0f}% of the way from low to high")
    sp = screens_passed().get(sym, [])
    st.markdown("**Screens passed**")
    if sp:
        st.dataframe(pd.DataFrame(sp, columns=["Screen", "Score"]).sort_values("Score", ascending=False),
                     hide_index=True, width="stretch",
                     column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")})
    else:
        st.caption("Not in any screen with default settings.")
    st.markdown("**Red flags**")
    detail = {
        "Cash flow < half of profit (3y)": lambda: f"cash/profit over 3 years = {r['cfo_pat_3y']:.2f}",
        "Receivables: days >= 30 and rising > 1.2x revenue growth":
            lambda: f"receivable days {r['receivable_days_prev']:.0f} → {r['receivable_days']:.0f} "
                    f"({r['receivable_days_growth']:+.1f}%) vs revenue {r['revenue_growth_1y']:+.1f}%",
        "Interest cover < 1.5": lambda: f"operating profit / interest = {r['interest_cover']:.2f}",
        "Altman Z'' < 1.1 (distress zone)": lambda: f"Z'' = {r['altman_z']:.2f}",
        "Promoter stake down > 5 pts in a year": lambda: f"promoter change over 4 quarters = {r['d_promoter_4q']:+.2f} pts",
        "New shares > 10% in a year": lambda: f"share count up {r['share_growth_1y']:.1f}%",
        "Promoter pledge > 25%": lambda: f"{r['pledge_pct']:.1f}% of promoter shares pledged",
    }
    flags = [f for f in str(r.get("red_flags") or "").split("; ") if f]
    if flags:
        for f in flags:
            try:
                st.markdown(f"- ⚠️ **{f}** — {detail[f]()}")
            except Exception:
                st.markdown(f"- ⚠️ **{f}**")
    else:
        st.caption("✓ No red flags.")

# ------------------------------------------------------------------ Today (Step 13)
with TODAY_TAB:
    if st.button("Load today's 5-minute chart", key="live_btn") or st.session_state.get(f"live_{sym}"):
        st.session_state[f"live_{sym}"] = True
        b, fetched = live(sym)
        if b is None or b.empty:
            st.info("No intraday data from Yahoo Finance for this stock right now.")
        else:
            td, pv = I.split_days(b)
            s_ = I.summarise(td, pv)
            d = I.delay_minutes(s_["last_bar"])
            c1, c2, c3 = st.columns(3)
            c1.metric("Last", f"₹{s_['last']:,.1f}")
            c2.metric("Today", f"{s_['day_pct']:+.2f}%")
            c3.metric("Last hour", f"{s_['hour_pct']:+.2f}%")
            fig = go.Figure()
            fig.add_scatter(x=td.index, y=td["Close"], name="Price", line=dict(color=SERIES[0], width=2))
            if pd.notna(pv):
                fig.add_hline(y=pv, line=dict(color=MUTED, dash="dash", width=1),
                              annotation_text=f"prev close {pv:,.1f}", annotation_font_color=MUTED)
            st.plotly_chart(layout(fig, height=320, y_title="₹"), width="stretch")
            st.caption(f"Market {I.market_status()} · latest bar {s_['last_bar']:%a %d %b %H:%M} IST"
                       + (f" · about {d:.0f} min behind live" if d is not None else "")
                       + f" · fetched {fetched:%H:%M} · Yahoo Finance (free, delayed)")
    else:
        st.caption("Fetches live 5-minute prices from Yahoo Finance (a few seconds).")

# ------------------------------------------------------------------ 2 Price
with tabs[1]:
    a, b2 = st.columns([3, 2])
    rng = a.radio("Period", ["3M", "6M", "1Y", "3Y", "5Y"], index=2, horizontal=True, key="px_rng")
    raw = b2.toggle("Raw NSE prices (not adjusted for splits/bonuses)", value=False)
    days = {"3M": 92, "6M": 183, "1Y": 366, "3Y": 1096, "5Y": 1830}[rng]
    p = px.copy()
    p["c"] = p["raw_close"] if raw else p["close"]
    for k in (20, 50, 200):
        p[f"sma{k}"] = p["c"].rolling(k).mean()
    p = p[p["date"] >= p["date"].max() - pd.Timedelta(days=days)]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.62, 0.2, 0.18], vertical_spacing=0.04,
                        subplot_titles=("", "Volume", "Delivery %"))
    fig.add_scatter(x=p["date"], y=p["c"], name="Price", line=dict(color=SERIES[0], width=2), row=1, col=1)
    for k, col in zip((20, 50, 200), (SERIES[1], SERIES[2], SERIES[3])):
        fig.add_scatter(x=p["date"], y=p[f"sma{k}"], name=f"{k} DMA", line=dict(color=col, width=1.3), row=1, col=1)
    fig.add_bar(x=p["date"], y=p["volume"], name="Volume", marker_color=AXIS, showlegend=False, row=2, col=1)
    fig.add_scatter(x=p["date"], y=p["deliv_pct"], name="Delivery %", line=dict(color=INK2, width=1),
                    showlegend=False, row=3, col=1)
    for _, ev in actions(isin).iterrows():
        if ev["ex_date"] >= p["date"].min():
            fig.add_vline(x=ev["ex_date"], line=dict(color=MUTED, width=1, dash="dot"))
            fig.add_annotation(x=ev["ex_date"], y=1, yref="paper", text=f"×{ev['factor']:g}", showarrow=False,
                               font=dict(size=10, color=MUTED), yanchor="bottom")
    if pd.notna(r.get("breakout_date")) and pd.to_datetime(r["breakout_date"]) >= p["date"].min():
        bd = pd.to_datetime(r["breakout_date"])
        fig.add_scatter(x=[bd], y=[p.set_index("date")["c"].get(bd)], mode="markers", name="Breakout",
                        marker=dict(size=11, color=SERIES[4], symbol="triangle-up",
                                    line=dict(color=SURFACE, width=2)), row=1, col=1)
    fig = layout(fig, height=560)
    st.plotly_chart(fig, width="stretch")
    st.caption("Dotted lines = split / bonus / demerger dates (×factor). Prices before them are divided by the "
               "factor so the chart is continuous, unless you switch to raw prices.")

# ------------------------------------------------------------------ 3 Relative
wide = indices()
sec_list = [s.strip() for s in str(mrow.get("in_indices") or "").split(";") if s.strip() in SEC.SECTORS]
size = {s: sum(str(x).count(s) for x in master["in_indices"].fillna("")) for s in sec_list}
own_sector = min(sec_list, key=lambda s: size[s]) if sec_list else None   # most specific = fewest members
with tabs[2]:
    rng2 = st.radio("Period", ["3M", "6M", "1Y", "3Y", "5Y"], index=2, horizontal=True, key="rel_rng")
    days = {"3M": 92, "6M": 183, "1Y": 366, "3Y": 1096, "5Y": 1830}[rng2]
    end = px["date"].max()
    start = end - pd.Timedelta(days=days)
    s_close = px.set_index("date")["close"]
    w = SEC.window(s_close, start, end)
    fig = go.Figure()
    if w is not None:
        fig.add_scatter(x=w.index, y=w / w.iloc[0] * 100, name=sym, line=dict(color=SERIES[0], width=2.5))
    if own_sector:
        ws = SEC.window(wide[own_sector], start, end)
        if ws is not None:
            fig.add_scatter(x=ws.index, y=ws / ws.iloc[0] * 100, name=SEC.short(own_sector),
                            line=dict(color=SERIES[1], width=2))
    for bname in SEC.BENCHMARKS:
        wb = SEC.window(wide[bname], start, end)
        if wb is not None:
            col, dash = BENCH_STYLE[bname]
            fig.add_scatter(x=wb.index, y=wb / wb.iloc[0] * 100, name=SEC.short(bname),
                            line=dict(color=col, width=1.4, dash=dash))
    fig.add_hline(y=100, line=dict(color=AXIS, width=1))
    st.plotly_chart(layout(fig, y_title="Value of 100 invested"), width="stretch")
    st.caption(f"Sector index used: **{SEC.short(own_sector) if own_sector else 'none (stock is in no NSE sector index)'}**"
               + (f" — most specific of {', '.join(SEC.short(s) for s in sec_list)}" if len(sec_list) > 1 else ""))
    rows = []
    comp = [("Stock", s_close)] + ([(SEC.short(own_sector), wide[own_sector])] if own_sector else []) + \
           [(SEC.short(b), wide[b]) for b in SEC.BENCHMARKS]
    for lab, d in [("1M", 31), ("3M", 92), ("6M", 183), ("1Y", 366), ("3Y", 1096), ("5Y", 1827)]:
        row = {"Period": lab}
        st_r = SEC.period_return(s_close, end - pd.Timedelta(days=d - 1), end)
        for name, ser in comp:
            pr = SEC.period_return(ser, end - pd.Timedelta(days=d - 1), end)
            row[f"{name} %"] = pr["ret"] * 100 if pr else np.nan
            if name != "Stock":
                row[f"vs {name} (pts)"] = (st_r["ret"] - pr["ret"]) * 100 if (pr and st_r) else np.nan
        rows.append(row)
    tbl = pd.DataFrame(rows)
    st.dataframe(tbl, hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%+.1f") for c in tbl.columns if c != "Period"})

# ------------------------------------------------------------------ 4 Technicals
with tabs[3]:
    one = px[px["date"] >= px["date"].max() - pd.Timedelta(days=366)].set_index("date")["close"].pct_change()
    nb = wide["Nifty 50"].pct_change().reindex(one.index)
    j = pd.concat([one, nb], axis=1).dropna()
    beta = j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var() if len(j) > 30 else np.nan
    t = [("RSI (14)", f"{r['rsi14']:.0f}", "below 30 oversold · above 70 overbought"),
         ("vs 20 DMA", f"{(r['close'] / r['sma20'] - 1) * 100:+.1f}%", None),
         ("vs 50 DMA", f"{(r['close'] / r['sma50'] - 1) * 100:+.1f}%", None),
         ("vs 200 DMA", f"{(r['close'] / r['sma200'] - 1) * 100:+.1f}%" if pd.notna(r["sma200"]) else "–", None),
         ("Volatility (1Y)", f"{r['vol_ann']:.1f}%" if pd.notna(r["vol_ann"]) else "–", "annualised"),
         ("Beta vs Nifty 50 (1Y)", f"{beta:.2f}" if pd.notna(beta) else "–", None),
         ("ATR (14)", f"₹{r['atr14']:.1f} ({r['atr14'] / r['close'] * 100:.1f}%)", "average daily range"),
         ("Traded value (median 20d)", f"₹{r['med_value_cr_20d']:.1f} cr", None),
         ("Delivery % (today vs 20d avg)", f"{r['deliv_pct']:.0f}% vs {r['deliv_avg20']:.0f}%"
          if pd.notna(r["deliv_pct"]) else "–", None),
         ("Band width percentile (1Y)", f"{r['bb_width_pctile_1y']:.0f}" if pd.notna(r["bb_width_pctile_1y"]) else "–",
          "low = unusually tight price swings")]
    cols = st.columns(2)
    for i, (k, v, h) in enumerate(t):
        cols[i % 2].metric(k, v, help=h)

# ------------------------------------------------------------------ 5 Financials
fs = statements(sym)
ITEMS = {"Revenue": ["Total Revenue", "Operating Revenue"], "Operating profit (EBIT)": ["Operating Income", "EBIT"],
         "Net profit (PAT)": ["Net Income", "Net Income Common Stockholders"], "EPS ₹": ["Diluted EPS", "Basic EPS"],
         "Operating cash flow": ["Operating Cash Flow"], "Free cash flow": ["Free Cash Flow"],
         "Total debt": ["Total Debt"], "Equity": ["Stockholders Equity", "Common Stock Equity"]}


def pivot(freq):
    out = {}
    for lab, names in ITEMS.items():
        for n in names:
            x = fs[(fs["freq"] == freq) & (fs["item"] == n)]
            if len(x):
                out[lab] = x.set_index("period_end")["value"]
                break
    t = pd.DataFrame(out).sort_index().astype(float)
    t = t.dropna(how="all")          # yfinance sometimes adds an empty older period
    for c in t.columns:
        if c != "EPS ₹":
            t[c] = t[c] / 1e7   # ₹ crore
    if "Revenue" in t and "Operating profit (EBIT)" in t:
        t["Operating margin %"] = t["Operating profit (EBIT)"] / t["Revenue"] * 100
    if "Net profit (PAT)" in t and "Equity" in t:
        t["ROE %"] = t["Net profit (PAT)"] / t["Equity"] * 100
    if "Total debt" in t and "Equity" in t:
        t["Debt / Equity"] = t["Total debt"] / t["Equity"]
    return t


with tabs[4]:
    if fs.empty:
        st.info("No financial statements available for this stock.")
    else:
        ann = pivot("annual")
        q = pivot("quarterly")
        if bool(r.get("usd_converted")):
            st.caption("Reported in US dollars on yfinance; converted to ₹ at each period-end exchange rate.")
        st.markdown("**Annual (₹ crore)**")
        if len(ann):
            show = ann.T
            show.columns = [f"FY{d:%y}" if d.month == 3 else f"{d:%b %y}" for d in show.columns]
            st.dataframe(show.style.format("{:,.1f}", na_rep="–"), width="stretch")
            fig = go.Figure()
            for i, k in enumerate(["Revenue", "Net profit (PAT)"]):
                if k in ann:
                    fig.add_bar(x=show.columns, y=ann[k].values, name=k, marker_color=SERIES[i])
            fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
            st.plotly_chart(layout(fig, height=300, y_title="₹ crore"), width="stretch")
        st.markdown("**Quarterly (₹ crore)**")
        if len(q):
            qq = q.copy()
            if "Revenue" in qq:
                qq["Revenue YoY %"] = (qq["Revenue"] / qq["Revenue"].shift(4) - 1) * 100
            if "Net profit (PAT)" in qq:
                qq["Profit YoY %"] = (qq["Net profit (PAT)"] / qq["Net profit (PAT)"].shift(4) - 1) * 100
            qs = qq.drop(columns=[c for c in ["Total debt", "Equity", "ROE %", "Debt / Equity", "Free cash flow",
                                              "Operating cash flow"] if c in qq]).T
            qs.columns = [f"{d:%b %y}" for d in qs.columns]
            st.dataframe(qs.style.format("{:,.1f}", na_rep="–"), width="stretch")
        st.caption("Source: yfinance (consolidated). Can differ from exchange filings; check the annual report for "
                   "decisions.")

# ------------------------------------------------------------------ 6 Valuation
with tabs[5]:
    peers_ind = m[m["nse_industry"] == r["nse_industry"]]
    vals = [("P/E", "pe", False), ("P/B", "pb", False), ("EV/EBITDA", "ev_ebitda", False),
            ("FCF yield %", "fcf_yield", True)]
    rows = []
    for lab, col, higher_is_cheap in vals:
        v = r.get(col)
        s = peers_ind[col].dropna()
        pct = (s < v).mean() * 100 if pd.notna(v) and len(s) >= 5 else np.nan
        rows.append({"Measure": lab, "Value": v, "Sector median": s.median() if len(s) else np.nan,
                     "Cheaper than … % of sector": (pct if higher_is_cheap else 100 - pct) if pd.notna(pct) else np.nan})
    if "div_yield" in m:
        dv = peers_ind["div_yield"].dropna()
        v = r.get("div_yield")
        rows.append({"Measure": "Dividend yield %", "Value": v, "Sector median": dv.median() if len(dv) else np.nan,
                     "Cheaper than … % of sector": (dv < v).mean() * 100 if pd.notna(v) and len(dv) >= 5 else np.nan})
    if "payout_pct" in m:
        rows.append({"Measure": "Dividend payout % of profit", "Value": r.get("payout_pct"),
                     "Sector median": peers_ind["payout_pct"].median(), "Cheaper than … % of sector": np.nan})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Value": st.column_config.NumberColumn(format="%.1f"),
                                "Sector median": st.column_config.NumberColumn(format="%.1f"),
                                "Cheaper than … % of sector": st.column_config.NumberColumn(format="%.0f")})
    st.caption(f"Sector = NSE industry **{r['nse_industry']}** ({len(peers_ind)} stocks). "
               "Dividend yield = indicated annual dividend per share (yfinance) ÷ NSE price, refreshed weekly; "
               "can include special dividends.")
    # P/E history band (DD3): price / EPS of the latest fiscal year already reported (60-day lag)
    eps = fs[(fs["freq"] == "annual") & fs["item"].isin(["Diluted EPS", "Basic EPS"])]
    eps = eps.sort_values("item").drop_duplicates("period_end").set_index("period_end")["value"].sort_index()
    if len(eps) >= 2 and (eps > 0).all():
        avail = pd.Series(eps.values, index=eps.index + pd.Timedelta(days=60))
        pe_s = px.set_index("date")["close"]
        e = avail.reindex(pe_s.index, method="ffill")
        pe_hist = (pe_s / e).dropna()
        if len(pe_hist) > 100:
            med, sd = pe_hist.median(), pe_hist.std()
            fig = go.Figure()
            fig.add_scatter(x=pe_hist.index, y=[med + sd] * len(pe_hist), line=dict(width=0), showlegend=False,
                            hoverinfo="skip")
            fig.add_scatter(x=pe_hist.index, y=[max(med - sd, 0)] * len(pe_hist), fill="tonexty",
                            fillcolor="rgba(42,120,214,0.10)", line=dict(width=0), name="Median ± 1 std",
                            hoverinfo="skip")
            fig.add_scatter(x=pe_hist.index, y=pe_hist, name="P/E", line=dict(color=SERIES[0], width=2))
            fig.add_hline(y=med, line=dict(color=MUTED, dash="dash", width=1))
            st.plotly_chart(layout(fig, height=320, y_title="P/E"), width="stretch")
            st.caption(f"P/E now {pe_hist.iloc[-1]:.1f} vs median {med:.1f} since {pe_hist.index[0]:%b %Y}. "
                       "Approximate: uses annual EPS (about 4 years), applied 60 days after each year end.")
    else:
        st.caption("P/E history not shown (needs at least 2 years of positive annual EPS).")

# ------------------------------------------------------------------ 7 Ownership
with tabs[6]:
    o = ownership(sym)
    if o.empty:
        st.info("No shareholding data for this stock.")
    else:
        fig = go.Figure()
        for i, (k, lab) in enumerate([("promoter", "Promoter"), ("fii", "FII"), ("dii", "DII"), ("mf", "Mutual funds")]):
            if o[k].notna().any():
                fig.add_scatter(x=o["quarter_end"], y=o[k], name=lab, mode="lines+markers",
                                line=dict(color=SERIES[i], width=2), marker=dict(size=8))
        st.plotly_chart(layout(fig, height=320, y_title="% of shares"), width="stretch")
        tb = o[["quarter_end", "promoter", "fii", "dii", "mf", "public", "pledge_pct_of_promoter"]].copy()
        tb.columns = ["Quarter", "Promoter %", "FII %", "DII %", "MF %", "Public %", "Promoter shares pledged %"]
        tb["Quarter"] = pd.to_datetime(tb["Quarter"]).dt.strftime("%b %Y")
        st.dataframe(tb.iloc[::-1], hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f") for c in tb.columns[1:]})
        st.caption("DII includes mutual funds, insurers, banks and pension funds. Source: NSE shareholding filings.")

# ------------------------------------------------------------------ 8 Peers (DD4)
with tabs[7]:
    pool = m[(m["basic_industry"] == r["basic_industry"])]
    basis = f"same industry label ({r['basic_industry']})"
    if len(pool) < 4:
        pool = m[m["nse_industry"] == r["nse_industry"]]
        basis = f"same NSE industry ({r['nse_industry']}) — fewer than 3 peers with the same finer label"
    pool = pool[pool["mcap_cr"].notna()].copy()
    if pd.notna(r.get("mcap_cr")) and len(pool):
        pool["dist"] = (np.log(pool["mcap_cr"]) - np.log(r["mcap_cr"])).abs()
        pool = pool.nsmallest(11, "dist")
    sp_all = screens_passed()
    pool["Screens"] = pool["symbol"].map(lambda s: len(sp_all.get(s, [])))
    pool["ROCE / ROE %"] = pool["roe_3y"].where(pool["is_financial"], pool["roce_3y"])
    show = pool[["symbol", "company", "mcap_cr", "pe", "ROCE / ROE %", "rev_cagr_3y", "ret_12M", "Screens",
                 "red_flag_count"]].sort_values("mcap_cr", ascending=False)
    show.columns = ["Symbol", "Company", "Mcap ₹ cr", "P/E", "ROCE / ROE %", "Revenue CAGR 3Y %", "1Y return %",
                    "Screens passed", "Red flags"]
    st.dataframe(show.style.format({"Mcap ₹ cr": "{:,.0f}", "P/E": "{:.1f}", "ROCE / ROE %": "{:.1f}",
                                    "Revenue CAGR 3Y %": "{:.1f}", "1Y return %": "{:+.1f}"}, na_rep="–")
                 .apply(lambda row: ["font-weight:700" if row["Symbol"] == sym else "" for _ in row], axis=1),
                 hide_index=True, width="stretch")
    st.caption(f"Peers: {basis}, closest 10 by market cap. The selected stock is in bold.")

# ------------------------------------------------------------------ 9 Scores
with tabs[8]:
    names = ["Return on assets > 0", "Operating cash flow > 0", "Return on assets improved",
             "Cash flow > profit", "Debt / assets fell", "Current ratio improved", "No new shares issued",
             "Gross margin improved" if r.get("piotroski_gm_basis") == "gross" else "Operating margin improved*",
             "Asset turnover improved"]
    chk = str(r.get("piotroski_checks") or "")
    if r["is_financial"]:
        st.info("Piotroski, Altman Z and Magic Formula don't apply to financial companies.")
    elif len(chk) == 9:
        st.markdown(f"**Piotroski F-score: {int(r['piotroski'])} / 9** (latest year vs previous year)")
        st.dataframe(pd.DataFrame({"Check": names, "Result": ["✓" if c == "1" else "✗" for c in chk]}),
                     hide_index=True, width="stretch")
        if r.get("piotroski_gm_basis") != "gross":
            st.caption("*Gross profit not reported, so operating margin is used.")
    c1, c2 = st.columns(2)
    if pd.notna(r.get("altman_z")):
        zone = "safe" if r["altman_z"] > 2.6 else "grey" if r["altman_z"] >= 1.1 else "distress"
        c1.metric("Altman Z″ (emerging markets)", f"{r['altman_z']:.2f}", f"{zone} zone", delta_color="off",
                  help="> 2.6 safe · 1.1–2.6 grey · < 1.1 distress")
    mf = m[(~m["is_financial"]) & (m["mf_earnings_yield"] > 0) & (m["mf_roc"] > 0)
           & ~m["nse_industry"].isin(SC.UTILITIES)].copy()
    if sym in set(mf["symbol"]):
        mf["rank"] = mf["mf_earnings_yield"].rank(ascending=False) + mf["mf_roc"].rank(ascending=False)
        pos = int(mf["rank"].rank(method="min").loc[mf["symbol"] == sym].iloc[0])
        c2.metric("Magic Formula rank", f"{pos} of {len(mf)}",
                  f"earnings yield {r['mf_earnings_yield']:.1f}% · ROC {r['mf_roc']:.0f}%", delta_color="off")

# ------------------------------------------------------------------ 10 Events & notes
with tabs[9]:
    ca = actions(isin)
    if len(ca):
        ev = ca[["ex_date", "factor", "event_type"]].copy()
        ev["ex_date"] = ev["ex_date"].dt.strftime("%d %b %Y")
        ev.columns = ["Date", "Adjustment factor", "Type"]
        st.dataframe(ev, hide_index=True, width="stretch")
    else:
        st.caption("No splits, bonuses or demergers in the last 5 years.")
    st.markdown(f"""
- Prices: NSE daily files, up to **{pd.to_datetime(r['last_date']):%d %b %Y}** ({int(r['history_days'])} trading days loaded)
- Latest quarterly results: **{pd.to_datetime(r['latest_quarter']):%b %Y}** (yfinance)
- Ownership: quarter ending **{pd.to_datetime(r['own_quarter']):%b %Y}** (NSE filings)
""" if pd.notna(r.get("latest_quarter")) and pd.notna(r.get("own_quarter")) else
                "- Some data dates unavailable for this stock.")
    if bool(r.get("usd_converted")):
        st.caption("Financials reported in USD on yfinance; converted to ₹ at period-end exchange rates.")
