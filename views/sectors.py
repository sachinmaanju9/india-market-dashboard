"""Sector performance page (Step 6)."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from app_lib import sector as S  # noqa: E402


# ------------------------------------------------------------------ palette (dataviz reference instance)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]   # categorical, fixed order
BENCH_STYLE = {"Nifty 50": ("#0b0b0b", "dash"), "Nifty Smallcap 250": ("#52514e", "dot"),
               "Nifty Bank": ("#898781", "dashdot")}
DIVERGING = [[0.0, "#b3262c"], [0.25, "#e88a86"], [0.5, "#f0efec"], [0.75, "#86b6ef"], [1.0, "#1c5cab"]]
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
MAX_SECTORS_ON_CHART = 6


def layout(fig, height=420, y_title=None):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=30, b=8), plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK2, size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=INK2)),
        hovermode="x unified", hoverlabel=dict(bgcolor="white", font_color=INK))
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=AXIS, tickfont=dict(color=MUTED),
                     title=dict(text=y_title, font=dict(color=MUTED)) if y_title else None)
    return fig


@st.cache_data(ttl=3600, show_spinner="Loading index data…")
def data():
    return S.load_closes("data/indices")


wide = data()
last = wide.index.max()

# ------------------------------------------------------------------ controls
st.title("Sector performance")
st.caption(f"NSE price-return indices · data up to **{last:%d %b %Y}**")

periods = S.presets(last)
fys = S.past_fys(wide.index.min(), last)
options = list(periods) + list(fys) + ["Custom"]
c1, c2 = st.columns([2, 3])
with c1:
    choice = st.selectbox("Period", options, index=options.index("1Y"))
if choice == "Custom":
    with c2:
        rng = st.date_input("From – to", value=(last - pd.DateOffset(years=1), last),
                            min_value=wide.index.min() + pd.Timedelta(days=1), max_value=last)
    if not isinstance(rng, tuple) or len(rng) != 2:
        st.info("Pick both a start and an end date.")
        st.stop()
    start, end = pd.Timestamp(rng[0]), pd.Timestamp(rng[1])
else:
    start, end = {**periods, **fys}[choice]

table, bench = S.sector_table(wide, start, end)
b50 = bench.get("Nifty 50")
if b50:
    st.caption(f"Return measured from the close of **{b50['base_date']:%d %b %Y}** "
               f"to the close of **{b50['end_date']:%d %b %Y}** ({b50['days']} days)")

# ------------------------------------------------------------------ benchmark strip
cells = []
for b in S.BENCHMARKS:
    br = bench.get(b)
    val = f"{br['ret'] * 100:+.1f}%" if br else "n/a"
    cagr = f"<span style='color:{MUTED};font-size:0.8rem'> · CAGR {br['cagr'] * 100:.1f}%</span>" \
        if br and br["cagr"] is not None else ""
    cells.append(f"<div style='flex:1 1 150px'><div style='color:{INK2};font-size:0.85rem'>{S.short(b)}</div>"
                 f"<div style='font-size:1.6rem;color:{INK}'>{val}{cagr}</div></div>")
st.markdown(f"<div style='display:flex;flex-wrap:wrap;gap:12px;margin:4px 0 12px'>{''.join(cells)}</div>",
            unsafe_allow_html=True)

tab_t, tab_h, tab_c, tab_rs, tab_rrg = st.tabs(
    ["Ranking", "Heatmap", "Price chart", "Relative strength", "Rotation"])

# ------------------------------------------------------------------ ranking table
with tab_t:
    show = table.drop(columns=["index"]).rename(columns={"note": "Note"}).copy()
    if show["CAGR %"].isna().all():
        show = show.drop(columns=["CAGR %"])
    pts_cols = [c for c in show.columns if c.endswith("(pts)")]

    def tint(v):
        if pd.isna(v):
            return ""
        a = min(abs(v) / 20, 1) * 0.55
        return f"background-color: rgba(28,92,171,{a:.2f})" if v > 0 else f"background-color: rgba(179,38,44,{a:.2f})"

    fmt = {c: "{:+.1f}" for c in ["Return %", "CAGR %", *pts_cols] if c in show}
    fmt.update({"Volatility %": "{:.1f}", "Max fall %": "{:.1f}", "Beta": "{:.2f}"})
    sty = show.style.format(fmt, na_rep="–").map(tint, subset=["Return %", *pts_cols])
    st.dataframe(sty, hide_index=True, width="stretch", height=min(38 * (len(show) + 1), 980))
    st.caption("**vs … (pts)** = sector return minus benchmark return, in percentage points. "
               "Blue = ahead, red = behind. **Max fall** = biggest peak-to-trough drop in the period. "
               "**Beta** vs Nifty 50: 1.2 ≈ moves 1.2% when Nifty moves 1%. "
               "Sectors marked n/a had no index value before the period start.")

# ------------------------------------------------------------------ heatmap
with tab_h:
    mat = S.returns_matrix(wide, S.BENCHMARKS + S.SECTORS, periods)
    mat = mat.loc[[S.short(b) for b in S.BENCHMARKS] + mat.index[3:].tolist()]
    lim = np.nanpercentile(np.abs(mat.values), 95) if np.isfinite(mat.values).any() else 1
    fig = go.Figure(go.Heatmap(
        z=mat.values, x=mat.columns, y=mat.index, colorscale=DIVERGING, zmid=0, zmin=-lim, zmax=lim,
        text=np.where(np.isnan(mat.values), "–", np.vectorize(lambda v: f"{v:+.1f}")(np.nan_to_num(mat.values))),
        texttemplate="%{text}", textfont=dict(size=11, color=INK), xgap=2, ygap=2,
        hovertemplate="%{y} · %{x}: %{text}%<extra></extra>", colorbar=dict(title="%", thickness=10)))
    fig = layout(fig, height=26 * len(mat) + 80)
    fig.update_layout(hovermode="closest")
    fig.update_yaxes(autorange="reversed", gridcolor=SURFACE)
    fig.update_xaxes(side="top")
    st.plotly_chart(fig, width="stretch")
    st.caption("Returns (%) for every preset period, all ending on the latest date. "
               "The first three rows are the benchmarks. – = index did not exist at the period start.")

# ------------------------------------------------------------------ shared sector picker for charts
ranked = table[table["Return %"].notna()]["index"].tolist()
default = (ranked[:2] + ranked[-1:]) if len(ranked) >= 3 else ranked

with tab_c:
    pick = st.multiselect("Sectors (up to 6)", S.SECTORS, default=default, format_func=S.short,
                          max_selections=MAX_SECTORS_ON_CHART, key="pick_price")
    rb = S.rebased(wide, pick + S.BENCHMARKS, start, end)
    fig = go.Figure()
    for i, n in enumerate(pick):
        k = S.short(n)
        if k in rb:
            fig.add_scatter(x=rb.index, y=rb[k], name=k, line=dict(color=SERIES[i], width=2),
                            hovertemplate=f"{k}: %{{y:.1f}}<extra></extra>")
    for b in S.BENCHMARKS:
        k = S.short(b)
        if k in rb:
            col, dash = BENCH_STYLE[b]
            fig.add_scatter(x=rb.index, y=rb[k], name=k, line=dict(color=col, width=1.5, dash=dash),
                            hovertemplate=f"{k}: %{{y:.1f}}<extra></extra>")
    fig.add_hline(y=100, line=dict(color=AXIS, width=1))
    st.plotly_chart(layout(fig, y_title="Value of 100 invested"), width="stretch")
    dropped = [S.short(n) for n in pick if S.short(n) not in rb]
    if dropped:
        st.caption(f"Not shown (no data at period start): {', '.join(dropped)}")
    st.caption("All lines start at 100 on the base date. Dashed/dotted lines are the benchmarks.")

with tab_rs:
    bsel = st.radio("Compare against", S.BENCHMARKS, horizontal=True, format_func=S.short, key="rs_b")
    pick_rs = st.multiselect("Sectors (up to 6)", S.SECTORS, default=default, format_func=S.short,
                             max_selections=MAX_SECTORS_ON_CHART, key="pick_rs")
    fig = go.Figure()
    for i, n in enumerate(pick_rs):
        rs = S.rs_line(wide, n, bsel, start, end)
        if rs is not None:
            fig.add_scatter(x=rs.index, y=rs, name=S.short(n), line=dict(color=SERIES[i], width=2),
                            hovertemplate=f"{S.short(n)}: %{{y:.1f}}<extra></extra>")
    fig.add_hline(y=100, line=dict(color=AXIS, width=1))
    st.plotly_chart(layout(fig, y_title=f"Relative to {S.short(bsel)} (start = 100)"), width="stretch")
    st.caption(f"Line rising = sector doing better than {S.short(bsel)}, even if both are falling. "
               "Above 100 = ahead since the start of the period.")

# ------------------------------------------------------------------ rotation (RRG-style)
with tab_rrg:
    bsel2 = st.radio("Benchmark", S.BENCHMARKS, horizontal=True, format_func=S.short, key="rrg_b")
    rr_all = S.rrg(wide, S.SECTORS, bsel2, end)
    names = sorted(rr_all["sector"].unique()) if not rr_all.empty else []
    top = (rr_all.groupby("sector").tail(1).sort_values("ratio", ascending=False)["sector"].head(3).tolist()
           if not rr_all.empty else [])
    trail_for = st.multiselect("Show 6-week trail for", names, default=top, key="rrg_trail")
    rr = rr_all
    if rr.empty:
        st.info("Not enough weekly history for this view.")
    else:
        latest = rr.groupby("sector").tail(1)
        pad = 1.5
        xr = [min(rr.ratio.min(), 100) - pad, max(rr.ratio.max(), 100) + pad]
        yr = [min(rr.momentum.min(), 100) - pad, max(rr.momentum.max(), 100) + pad]
        fig = go.Figure()
        quads = [("Leading", 100, xr[1], 100, yr[1], "rgba(28,92,171,0.07)"),
                 ("Weakening", 100, xr[1], yr[0], 100, "rgba(237,161,0,0.08)"),
                 ("Lagging", xr[0], 100, yr[0], 100, "rgba(179,38,44,0.07)"),
                 ("Improving", xr[0], 100, 100, yr[1], "rgba(27,175,122,0.08)")]
        for name, x0, x1, y0, y1, fill in quads:
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=fill, line_width=0, layer="below")
            fig.add_annotation(x=x0 if x0 < 100 else x1, y=y1 if y1 > 100 else y0, text=f"<b>{name}</b>",
                               xanchor="left" if x0 < 100 else "right", yanchor="top" if y1 > 100 else "bottom",
                               showarrow=False, font=dict(color=INK2, size=12))
        for i, (sct, g) in enumerate(rr[rr.sector.isin(trail_for)].groupby("sector")):
            fig.add_scatter(x=g.ratio, y=g.momentum, mode="lines+markers", name=sct,
                            line=dict(color=SERIES[i % len(SERIES)], width=2), marker=dict(size=5),
                            hovertemplate=f"{sct} · %{{x:.1f}}, %{{y:.1f}}<extra></extra>", showlegend=False)
        fig.add_scatter(x=latest.ratio, y=latest.momentum, mode="markers+text", text=latest.sector,
                        textposition="top center", textfont=dict(size=10, color=INK2),
                        marker=dict(size=9, color=INK2, line=dict(color=SURFACE, width=2)),
                        customdata=latest[["quadrant"]],
                        hovertemplate="%{text}<br>RS-Ratio %{x:.1f} · RS-Momentum %{y:.1f}"
                                      "<br>%{customdata[0]}<extra></extra>", showlegend=False)
        fig.add_vline(x=100, line=dict(color=AXIS, width=1))
        fig.add_hline(y=100, line=dict(color=AXIS, width=1))
        fig = layout(fig, height=620, y_title="RS-Momentum")
        fig.update_layout(hovermode="closest")
        fig.update_xaxes(range=xr, title=dict(text="RS-Ratio (relative strength)", font=dict(color=MUTED)))
        fig.update_yaxes(range=yr)
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Weekly data up to {latest.week.max():%d %b %Y}. Dot = this week; coloured tails = last "
                   f"{S.RRG_TRAIL} weeks for the sectors you pick. Sectors usually rotate clockwise: Improving → Leading → "
                   "Weakening → Lagging. Uses the end date of the selected period.")
        st.dataframe(latest[["sector", "quadrant", "ratio", "momentum"]].sort_values(["quadrant", "ratio"])
                     .rename(columns={"ratio": "RS-Ratio", "momentum": "RS-Momentum"}).round(1),
                     hide_index=True, width="stretch")
