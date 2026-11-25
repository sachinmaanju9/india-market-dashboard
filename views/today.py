"""Today page (Step 13): live intraday view from yfinance - benchmarks, sectors, last-hour movers."""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app_lib import intraday as I  # noqa: E402
from app_lib import sector as SEC  # noqa: E402
from app_lib import store  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
BENCH_STYLE = {"Nifty 50": ("#0b0b0b", "dash"), "Nifty Smallcap 250": ("#52514e", "dot"),
               "Nifty Bank": ("#898781", "dashdot")}
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
GOOD, BAD = "#1c5cab", "#b3262c"


@st.cache_data(ttl=180, show_spinner="Fetching live index prices…")
def index_bars():
    sym = I.index_symbols()
    got = I.fetch(list(sym.values()), interval="5m", period="2d")
    return {name: got[y] for name, y in sym.items() if y in got}, I.now_ist()


@st.cache_data(ttl=300, show_spinner="Fetching 5-minute prices for ~835 stocks (about a minute)…")
def stock_bars(symbols):
    got = I.fetch([f"{s}.NS" for s in symbols], interval="5m", period="2d")
    return {k[:-3]: v for k, v in got.items()}, I.now_ist()


@st.cache_data(ttl=3600, show_spinner=False)
def metrics():
    return pd.read_parquet(store.path("data/screens/metrics_latest.parquet"))


def pct(v):
    return f"{v:+.2f}%" if pd.notna(v) else "–"


st.title("Today")
status = I.market_status()
try:
    bars, fetched = index_bars()
except Exception as e:
    bars, fetched = {}, I.now_ist()
    st.error(f"Couldn't reach Yahoo Finance ({e}).")
if not bars:
    st.warning("No intraday index data available from Yahoo Finance right now. Try again in a few minutes.")
    st.stop()

summ = {}
for name, b in bars.items():
    today, prev = I.split_days(b)
    if len(today):
        summ[name] = I.summarise(today, prev)
last_bar = max(v["last_bar"] for v in summ.values())
delay = I.delay_minutes(last_bar)
st.caption(f"Market **{status}** · latest 5-minute bar **{last_bar:%a %d %b, %H:%M}** IST"
           + I.delay_text(delay)
           + f" · fetched {fetched:%H:%M} · source: Yahoo Finance (free, delayed, unofficial)")

# ------------------------------------------------------------------ benchmark strip
cells = []
for b in SEC.BENCHMARKS:
    s = summ.get(b)
    if not s:
        continue
    col = GOOD if s["day_pct"] >= 0 else BAD
    hcol = GOOD if s["hour_pct"] >= 0 else BAD
    cells.append(f"<div style='flex:1 1 150px'><div style='color:{INK2};font-size:0.85rem'>{SEC.short(b)}</div>"
                 f"<div style='font-size:1.4rem;color:{INK}'>{s['last']:,.1f}</div>"
                 f"<div style='font-size:0.85rem'><span style='color:{col}'>{pct(s['day_pct'])} today</span> · "
                 f"<span style='color:{hcol}'>{pct(s['hour_pct'])} last hour</span></div></div>")
st.markdown(f"<div style='display:flex;flex-wrap:wrap;gap:12px;margin:4px 0 12px'>{''.join(cells)}</div>",
            unsafe_allow_html=True)

tab_s, tab_m = st.tabs(["Sectors", "Last-hour movers"])

with tab_s:
    rows = [{"Sector": SEC.short(n), "index": n, "Today %": s["day_pct"], "Last hour %": s["hour_pct"],
             "Last bar": f"{s['last_bar']:%H:%M}"} for n, s in summ.items() if n in SEC.SECTORS]
    missing = [SEC.short(n) for n in SEC.SECTORS if n not in summ]
    if rows:
        t = pd.DataFrame(rows).sort_values("Last hour %", ascending=False)

        def tint(v):
            if pd.isna(v):
                return ""
            a = min(abs(v) / 2, 1) * 0.5
            return f"background-color: rgba(28,92,171,{a:.2f})" if v > 0 else f"background-color: rgba(179,38,44,{a:.2f})"
        st.dataframe(t.drop(columns="index").style.format({"Today %": "{:+.2f}", "Last hour %": "{:+.2f}"})
                     .map(tint, subset=["Today %", "Last hour %"]), hide_index=True, width="stretch")
        default = t["index"].head(2).tolist() + t["index"].tail(1).tolist()
        pick = st.multiselect("Chart (up to 6)", t["index"].tolist(), default=default, format_func=SEC.short,
                              max_selections=6)
        fig = go.Figure()
        for i, n in enumerate(pick):
            td, pv = I.split_days(bars[n])
            fig.add_scatter(x=td.index, y=(td["Close"] / pv - 1) * 100, name=SEC.short(n),
                            line=dict(color=SERIES[i], width=2))
        for bname in SEC.BENCHMARKS:
            if bname in bars:
                td, pv = I.split_days(bars[bname])
                colr, dash = BENCH_STYLE[bname]
                fig.add_scatter(x=td.index, y=(td["Close"] / pv - 1) * 100, name=SEC.short(bname),
                                line=dict(color=colr, width=1.4, dash=dash))
        fig.add_hline(y=0, line=dict(color=AXIS, width=1))
        fig.update_layout(height=380, margin=dict(l=8, r=8, t=30, b=8), plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
                          legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0), hovermode="x unified",
                          font=dict(color=INK2, size=12))
        fig.update_yaxes(gridcolor=GRID, title=dict(text="% vs previous close", font=dict(color=MUTED)))
        fig.update_xaxes(showgrid=False)
        st.plotly_chart(fig, width="stretch")
    if missing:
        st.caption("Not available on Yahoo intraday: " + ", ".join(missing))

with tab_m:
    m = metrics()
    liquid = m[(m["med_value_cr_20d"] >= 3) & m["close"].notna()]
    st.caption(f"{len(liquid)} stocks trading ≥ ₹3 cr a day. Fetching them all takes about a minute, so it runs "
               "only when you ask.")
    if st.button("Load last-hour movers", type="primary") or st.session_state.get("movers_loaded"):
        st.session_state["movers_loaded"] = True
        sb, fetched2 = stock_bars(tuple(liquid["symbol"]))
        rows = []
        for sym, b in sb.items():
            td, pv = I.split_days(b)
            if len(td) < 3:
                continue
            s = I.summarise(td, pv)
            rows.append({"symbol": sym, **s})
        if not rows:
            st.warning("No intraday stock data came back from Yahoo Finance.")
        else:
            r = pd.DataFrame(rows).merge(liquid[["symbol", "company", "nse_industry", "vol_avg50"]], on="symbol")
            r["Volume × normal"] = r["hour_volume"] / (r["vol_avg50"] / 6.25)   # normal hourly volume
            r["Open"] = "/stock?symbol=" + r["symbol"]
            cols = {"Open": "Open", "symbol": "Symbol", "company": "Company", "nse_industry": "Sector",
                    "last": "Price ₹", "hour_pct": "Last hour %", "day_pct": "Today %", "Volume × normal": "Volume × normal"}
            cfg = {"Open": st.column_config.LinkColumn("Open", display_text="🔬", width="small"),
                   "Price ₹": st.column_config.NumberColumn(format="%.1f"),
                   "Last hour %": st.column_config.NumberColumn(format="%+.2f"),
                   "Today %": st.column_config.NumberColumn(format="%+.2f"),
                   "Volume × normal": st.column_config.NumberColumn(format="%.1f")}
            st.caption(f"{len(r)} stocks with data · latest bar {r['last_bar'].max():%H:%M} IST · fetched {fetched2:%H:%M}")
            n = st.slider("Rows", 5, 30, 10)
            st.markdown("**Biggest gainers, last hour**")
            st.dataframe(r.nlargest(n, "hour_pct")[list(cols)].rename(columns=cols), hide_index=True,
                         width="stretch", column_config=cfg)
            st.markdown("**Biggest losers, last hour**")
            st.dataframe(r.nsmallest(n, "hour_pct")[list(cols)].rename(columns=cols), hide_index=True,
                         width="stretch", column_config=cfg)
            st.markdown("**Unusual volume, last hour** (vs a normal hour over the last 50 days)")
            st.dataframe(r.nlargest(n, "Volume × normal")[list(cols)].rename(columns=cols), hide_index=True,
                         width="stretch", column_config=cfg)
