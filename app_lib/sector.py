"""
Sector performance logic (Step 5, approved 2026-11-23). Pure functions - no Streamlit here,
so every number can be tested on its own.

S1  return = close(end) / close(base) - 1, base = last close BEFORE the start date.
    Periods > 1 year also get CAGR = (1+R)^(365/days) - 1.
S2  vs each benchmark: excess return (%-points), rebased-to-100 chart, relative-strength line.
S3  presets: 1W 1M 3M 6M YTD FY(current & past) 1Y 3Y 5Y + custom.
S4  volatility (std of daily returns x sqrt(250)), max drawdown, beta vs Nifty 50.
S5  rotation (RRG-style) - see rrg().
S6  index without data at the base date -> n/a, kept out of rankings.
"""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

BENCHMARKS = ["Nifty 50", "Nifty Smallcap 250", "Nifty Bank"]
SECTORS = [
    # full 5-year history
    "Nifty Auto", "Nifty Private Bank", "Nifty PSU Bank", "Nifty Financial Services",
    "Nifty FMCG", "Nifty IT", "Nifty Pharma", "Nifty Healthcare Index", "Nifty Metal", "Nifty Realty",
    "Nifty Media", "Nifty Oil & Gas", "Nifty Consumer Durables",
    # newer indices (short history)
    "Nifty Chemicals", "Nifty Cement", "Nifty Capital Goods", "Nifty Power", "Nifty Telecommunications",
    "Nifty NBFC", "Nifty Insurance", "Nifty Hospitals", "Nifty Retail", "Nifty Housing Finance",
    "Nifty Construction", "Nifty Consumer Services",
]
# Nifty Bank is a sector index too, but it is shown as a benchmark.


LABELS = {"Nifty 50": "Nifty 50", "Nifty Bank": "Bank Nifty", "Nifty Smallcap 250": "Smallcap 250"}


def short(name):
    return LABELS.get(name) or name.replace("Nifty ", "").replace(" Index", "")


# ------------------------------------------------------------------ data
def load_closes(indices_dir="data/indices"):
    """Wide table: rows = trading dates, columns = index names, values = closing level."""
    files = sorted(Path(indices_dir).glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f, columns=["date", "index", "close"]) for f in files])
    df["index"] = df["index"].str.strip()
    wide = df.pivot_table(index="date", columns="index", values="close", aggfunc="last").sort_index()
    return wide


# ------------------------------------------------------------------ periods (S3)
def fy_start(d):
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def presets(last_date):
    """Ordered dict of preset -> (start, end). end = latest data date."""
    e = pd.Timestamp(last_date)
    p = {
        "1W": e - pd.DateOffset(weeks=1),
        "1M": e - pd.DateOffset(months=1),
        "3M": e - pd.DateOffset(months=3),
        "6M": e - pd.DateOffset(months=6),
        "YTD": pd.Timestamp(date(e.year, 1, 1)),
        "FY to date": pd.Timestamp(fy_start(e.date())),
        "1Y": e - pd.DateOffset(years=1),
        "3Y": e - pd.DateOffset(years=3),
        "5Y": e - pd.DateOffset(years=5),
    }
    # start is the day AFTER the offset date, so 1Y = base close one year before the end date
    out = {k: (v + pd.Timedelta(days=1) if k not in ("YTD", "FY to date") else v, e) for k, v in p.items()}
    return out


def past_fys(first_date, last_date):
    """Completed Indian financial years fully covered by the data: label -> (start, end)."""
    out = {}
    y = fy_start(pd.Timestamp(first_date).date()).year + 1  # first FY whose base close exists
    while date(y + 1, 3, 31) <= pd.Timestamp(last_date).date():
        out[f"FY{str(y)[2:]}-{str(y + 1)[2:]}"] = (pd.Timestamp(date(y, 4, 1)), pd.Timestamp(date(y + 1, 3, 31)))
        y += 1
    return dict(reversed(list(out.items())))


# ------------------------------------------------------------------ core calcs (S1, S4)
def window(s, start, end):
    """Series from the base close (last close before start) to the last close on/before end.
    Returns None if there is no close before start (S6)."""
    s = s.dropna()
    before = s[s.index < pd.Timestamp(start)]
    if before.empty:
        return None
    w = s[(s.index >= before.index[-1]) & (s.index <= pd.Timestamp(end))]
    return w if len(w) >= 2 else None


def period_return(s, start, end):
    w = window(s, start, end)
    if w is None:
        return None
    r = w.iloc[-1] / w.iloc[0] - 1
    days = (w.index[-1] - w.index[0]).days
    cagr = (1 + r) ** (365 / days) - 1 if days > 366 else None
    return {"ret": r, "cagr": cagr, "base_date": w.index[0], "end_date": w.index[-1], "days": days}


def risk(s, bench, start, end):
    w = window(s, start, end)
    if w is None:
        return {}
    dr = w.pct_change().dropna()
    out = {"vol": dr.std() * np.sqrt(250) if len(dr) >= 10 else None,
           "max_dd": (w / w.cummax() - 1).min()}
    wb = window(bench, start, end)
    if wb is not None:
        j = pd.concat([dr.rename("s"), wb.pct_change().rename("b")], axis=1, join="inner").dropna()
        out["beta"] = j["s"].cov(j["b"]) / j["b"].var() if len(j) >= 20 else None
    return out


def first_date(s):
    s = s.dropna()
    return s.index[0] if len(s) else None


# ------------------------------------------------------------------ tables
def sector_table(wide, start, end, sectors=SECTORS, benchmarks=BENCHMARKS):
    """One row per sector: return, CAGR, excess vs each benchmark (%-pts), vol, max DD, beta.
    Sectors without a base close -> n/a rows (S6), listed last."""
    bench_ret = {b: period_return(wide[b], start, end) for b in benchmarks if b in wide}
    rows = []
    for sct in sectors:
        if sct not in wide:
            continue
        pr = period_return(wide[sct], start, end)
        row = {"Sector": short(sct), "index": sct}
        if pr is None:
            row["note"] = f"n/a (data from {first_date(wide[sct]):%d-%b-%Y})"
            rows.append(row)
            continue
        row.update({"Return %": pr["ret"] * 100,
                    "CAGR %": pr["cagr"] * 100 if pr["cagr"] is not None else None})
        for b, br in bench_ret.items():
            row[f"vs {short(b)} (pts)"] = (pr["ret"] - br["ret"]) * 100 if br else None
        rk = risk(wide[sct], wide["Nifty 50"], start, end)
        row.update({"Volatility %": rk.get("vol") * 100 if rk.get("vol") is not None else None,
                    "Max fall %": rk.get("max_dd") * 100 if rk.get("max_dd") is not None else None,
                    "Beta": rk.get("beta")})
        row["note"] = ""
        rows.append(row)
    t = pd.DataFrame(rows)
    has = t["Return %"].notna() if "Return %" in t else pd.Series(False, index=t.index)
    t = pd.concat([t[has].sort_values("Return %", ascending=False), t[~has]])
    t.insert(0, "Rank", [str(i + 1) for i in range(has.sum())] + ["-"] * (~has).sum())
    return t.reset_index(drop=True), bench_ret


def returns_matrix(wide, names, periods):
    """Rows = names, columns = period labels, values = return % (NaN if n/a). For the heatmap."""
    data = {}
    for label, (s, e) in periods.items():
        col = {}
        for n in names:
            pr = period_return(wide[n], s, e) if n in wide else None
            col[short(n)] = pr["ret"] * 100 if pr else np.nan
        data[label] = col
    return pd.DataFrame(data)


# ------------------------------------------------------------------ charts (S2)
def rebased(wide, names, start, end):
    """Each series = 100 at the base close (S2b). Series without a base close are dropped."""
    out = {}
    for n in names:
        w = window(wide[n], start, end) if n in wide else None
        if w is not None:
            out[short(n)] = w / w.iloc[0] * 100
    return pd.DataFrame(out)


def rs_line(wide, sector, bench, start, end):
    """Relative strength = (sector / benchmark), rebased to 100 at the base (S2c).
    Rising = sector beating the benchmark, even if both fall."""
    w = window(wide[sector], start, end)
    b = wide[bench]
    if w is None:
        return None
    rs = (w / b.reindex(w.index)).dropna()
    return rs / rs.iloc[0] * 100 if len(rs) else None


# ------------------------------------------------------------------ rotation (S5)
RRG_SMOOTH = 10      # weeks: RS-Ratio = RS vs its 10-week average
RRG_MOMENTUM = 4     # weeks: RS-Momentum = change in RS-Ratio over 4 weeks
RRG_TRAIL = 6        # weeks of trail shown


def rrg(wide, sectors, bench, end, trail=RRG_TRAIL):
    """Weekly (Friday) closes. RS = sector / benchmark.
    RS-Ratio    = 100 x RS / 10-week average of RS        (>100 = stronger than its recent norm)
    RS-Momentum = 100 x RS-Ratio / RS-Ratio 4 weeks ago   (>100 = relative strength improving)
    Quadrants (100/100 cross): Leading (R+,M+), Weakening (R+,M-), Lagging (R-,M-), Improving (R-,M+).
    Returns long table: sector, week, ratio, momentum (last `trail` weeks)."""
    wk = wide[wide.index <= pd.Timestamp(end)].resample("W-FRI").last()
    rows = []
    for sct in sectors:
        if sct not in wk or bench not in wk:
            continue
        rs = (wk[sct] / wk[bench]).dropna()
        if len(rs) < RRG_SMOOTH + RRG_MOMENTUM + trail:
            continue
        ratio = 100 * rs / rs.rolling(RRG_SMOOTH).mean()
        mom = 100 * ratio / ratio.shift(RRG_MOMENTUM)
        d = pd.DataFrame({"ratio": ratio, "momentum": mom}).dropna().tail(trail)
        d["sector"] = short(sct)
        d["week"] = d.index
        rows.append(d)
    if not rows:
        return pd.DataFrame(columns=["sector", "week", "ratio", "momentum", "quadrant"])
    out = pd.concat(rows, ignore_index=True)
    out["quadrant"] = np.select(
        [(out.ratio >= 100) & (out.momentum >= 100), (out.ratio >= 100) & (out.momentum < 100),
         (out.ratio < 100) & (out.momentum < 100)],
        ["Leading", "Weakening", "Lagging"], default="Improving")
    return out
