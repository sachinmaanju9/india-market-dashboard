"""
Per-stock metrics for the screeners (Steps 7 & 8, approved). One row per stock, latest date.
Technical  (T2-T5): moving averages, RSI, ATR, Bollinger width, 52-week high/low, returns,
                    liquidity, breakout memory, momentum score A.
Fundamental (F0-F7, U1-U3): last-4-quarter P&L, annual balance sheet & cash flow, valuation,
                    growth, Piotroski, Magic Formula inputs, Altman Z'', red flags.
Ownership  (F6):   4-quarter changes in promoter / FII / DII stakes, pledge.
"""
import numpy as np
import pandas as pd

TD = {"1M": 21, "3M": 63, "6M": 126, "9M": 189, "12M": 252}


# ================================================================== technical
def _rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _tech_one(g):
    g = g.sort_values("date")
    c, h, l, v = g["close"], g["high"], g["low"], g["volume"]
    n = len(g)
    out = {"last_date": g["date"].iloc[-1], "close": c.iloc[-1], "history_days": n}
    for k in (20, 50, 200):
        out[f"sma{k}"] = c.rolling(k).mean().iloc[-1] if n >= k else np.nan
    s200 = c.rolling(200).mean()
    out["sma200_1m_ago"] = s200.iloc[-22] if n >= 222 else np.nan
    out["rsi14"] = _rsi(c).iloc[-1] if n >= 15 else np.nan
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1] if n >= 15 else np.nan
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    bbw = (4 * sd) / mid
    out["bb_width"] = bbw.iloc[-1] if n >= 20 else np.nan
    win = bbw.iloc[-252:].dropna()
    out["bb_width_pctile_1y"] = (win < win.iloc[-1]).mean() * 100 if len(win) >= 120 else np.nan
    yr = g.iloc[-252:]
    out["high_52w"], out["low_52w"] = yr["high"].max(), yr["low"].min()
    out["pct_from_52w_high"] = (c.iloc[-1] / out["high_52w"] - 1) * 100
    hi = h.rolling(252, min_periods=20).max()
    new_hi = h >= hi
    out["new_52w_high_today"] = bool(new_hi.iloc[-1])
    out["new_52w_high_5d"] = bool(new_hi.iloc[-5:].any())
    out["high_50d_close"] = c.iloc[-50:].max()
    for k, d in TD.items():
        out[f"ret_{k}"] = (c.iloc[-1] / c.iloc[-1 - d] - 1) * 100 if n > d else np.nan
    out["ret_12_1M"] = (c.iloc[-22] / c.iloc[-253] - 1) * 100 if n > 252 else np.nan
    dr = c.pct_change()
    out["vol_ann"] = dr.iloc[-252:].std() * np.sqrt(250) * 100 if n >= 120 else np.nan
    out["med_value_cr_20d"] = g["value"].iloc[-20:].median() / 1e7
    out["vol_avg50"] = v.iloc[-51:-1].mean() if n > 51 else np.nan
    out["deliv_pct"] = g["deliv_pct"].iloc[-1]
    out["deliv_avg20"] = g["deliv_pct"].iloc[-21:-1].mean()

    # breakout memory: most recent breakout day within the last 5 sessions (T4 + clarification)
    out.update({"breakout_date": pd.NaT, "breakout_level": np.nan, "breakout_vol_x": np.nan,
                "breakout_status": None, "days_since_breakout": np.nan})
    if n >= 60:
        for back in range(1, 6):
            i = n - back
            prior = c.iloc[i - 50:i]
            level = prior.max()
            avgv = v.iloc[i - 50:i].mean()
            avgd = g["deliv_pct"].iloc[i - 20:i].mean()
            dpct = g["deliv_pct"].iloc[i]
            if c.iloc[i] > level and v.iloc[i] >= 1.5 * avgv and (pd.isna(avgd) or pd.isna(dpct) or dpct >= avgd):
                out.update({"breakout_date": g["date"].iloc[i], "breakout_level": level,
                            "breakout_vol_x": v.iloc[i] / avgv if avgv else np.nan,
                            "breakout_status": "Holding" if c.iloc[-1] > level else "Failed",
                            "days_since_breakout": back - 1})
                break
    return pd.Series(out)


def technical(px):
    t = px.groupby("isin", group_keys=True).apply(_tech_one, include_groups=False)
    # momentum score A (NSE style): z(6M ret / vol) and z(12M ret / vol), averaged -> percentile
    elig = t["history_days"] >= 253
    a = t.loc[elig, "ret_6M"] / t.loc[elig, "vol_ann"]
    b = t.loc[elig, "ret_12M"] / t.loc[elig, "vol_ann"]
    z = ((a - a.mean()) / a.std() + (b - b.mean()) / b.std()) / 2
    t["momentum_score"] = z.rank(pct=True) * 100
    return t


# ================================================================== fundamentals
ITEMS = {
    "revenue": ["Total Revenue", "Operating Revenue"], "ebit": ["Operating Income", "EBIT"],
    "ebitda": ["EBITDA", "Normalized EBITDA"], "pat": ["Net Income", "Net Income Common Stockholders"],
    "interest": ["Interest Expense", "Interest Expense Non Operating"], "gross": ["Gross Profit"],
    "assets": ["Total Assets"], "cur_assets": ["Current Assets"], "cur_liab": ["Current Liabilities"],
    "debt": ["Total Debt"], "equity": ["Stockholders Equity", "Common Stock Equity"],
    "receivables": ["Accounts Receivable", "Receivables"], "ppe": ["Net PPE"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "retained": ["Retained Earnings"], "shares": ["Ordinary Shares Number", "Share Issued"],
    "cfo": ["Operating Cash Flow"], "capex": ["Capital Expenditure"], "fcf": ["Free Cash Flow"],
}
NON_CURRENCY = {"Ordinary Shares Number", "Share Issued", "Diluted Average Shares", "Basic Average Shares"}


def usd_statement_symbols(st, info, last_close):
    """U3: statements treated as USD only if yfinance says USD AND rupee-assumed numbers are implausible."""
    out = []
    for _, r in info[info["financialCurrency"] == "USD"].iterrows():
        s = r["symbol"]
        rev = st[(st.symbol == s) & (st.freq == "annual") & st.item.isin(ITEMS["revenue"])]
        rev = rev.sort_values("period_end")["value"].iloc[-1] if len(rev) else np.nan
        mcap = last_close.get(s, np.nan) * (r.get("sharesOutstanding") or np.nan)
        if pd.notna(rev) and rev > 0 and pd.notna(mcap) and mcap / rev > 100:
            out.append(s)
    return out


def convert_usd(st, symbols, usdinr):
    """Multiply currency items of these symbols by USD/INR on each period end."""
    if not symbols:
        return st
    st = st.copy()
    m = st["symbol"].isin(symbols) & ~st["item"].isin(NON_CURRENCY)
    rate = usdinr.reindex(pd.to_datetime(st.loc[m, "period_end"]), method="ffill").values
    st.loc[m, "value"] = st.loc[m, "value"].values * rate
    return st


def _series(st, sym_rows, key, freq):
    s = sym_rows[(sym_rows["freq"] == freq) & sym_rows["item"].isin(ITEMS[key])]
    if s.empty:
        return pd.Series(dtype=float)
    # first matching item name in priority order
    for name in ITEMS[key]:
        x = s[s["item"] == name]
        if len(x):
            return x.set_index("period_end")["value"].sort_index().dropna()
    return pd.Series(dtype=float)


def _ratio(a, b):
    return a / b if (b is not None and pd.notna(b) and b != 0 and pd.notna(a)) else np.nan


def _cagr(s, years=3):
    if len(s) < years + 1:
        return np.nan
    a, b = s.iloc[-1 - years], s.iloc[-1]
    return ((b / a) ** (1 / years) - 1) * 100 if a > 0 and b > 0 else np.nan


def _fund_one(sym, rows, close, shares_info, is_fin):
    A = {k: _series(None, rows, k, "annual") for k in ITEMS}
    Q = {k: _series(None, rows, k, "quarterly") for k in ("revenue", "pat", "ebit")}
    o = {"symbol": sym}
    last = lambda s, i=1: s.iloc[-i] if len(s) >= i else np.nan  # noqa: E731

    # U1: P&L on last 4 quarters when 4 recent quarters exist, else latest annual
    def ttm(k):
        q = Q.get(k, pd.Series(dtype=float))
        if len(q) >= 4 and (q.index[-1] - q.index[-4]).days < 300:
            return q.iloc[-4:].sum()
        return last(A[k])
    o["revenue_ttm"], o["pat_ttm"], o["ebit_ttm"] = ttm("revenue"), ttm("pat"), ttm("ebit")
    o["annual_years"] = len(A["revenue"])
    o["latest_quarter"] = Q["revenue"].index[-1] if len(Q["revenue"]) else pd.NaT

    shares = last(A["shares"]) if len(A["shares"]) else shares_info
    shares = shares_info if pd.notna(shares_info) and shares_info > 0 else shares
    mcap = close * shares if pd.notna(close) and pd.notna(shares) else np.nan
    o["mcap_cr"] = mcap / 1e7
    debt, cash, eq = last(A["debt"]), last(A["cash"]), last(A["equity"])
    ev = mcap + (debt if pd.notna(debt) else 0) - (cash if pd.notna(cash) else 0)
    o["pe"] = _ratio(mcap, o["pat_ttm"]) if o["pat_ttm"] and o["pat_ttm"] > 0 else np.nan
    o["pb"] = _ratio(mcap, eq) if pd.notna(eq) and eq > 0 else np.nan
    ebitda = last(A["ebitda"])
    o["ev_ebitda"] = _ratio(ev, ebitda) if pd.notna(ebitda) and ebitda > 0 else np.nan
    o["fcf_yield"] = _ratio(last(A["fcf"]), mcap) * 100

    # returns on capital (3-yr averages of available latest 3 years)
    yrs = A["revenue"].index[-3:]
    roce, roe, roa, opm = [], [], [], []
    for y in yrs:
        e, d, eb, p, ta, r = (A["equity"].get(y), A["debt"].get(y), A["ebit"].get(y),
                              A["pat"].get(y), A["assets"].get(y), A["revenue"].get(y))
        cap = (e or 0) + (d or 0)
        if pd.notna(eb) and cap > 0:
            roce.append(eb / cap * 100)
        if pd.notna(p) and e and e > 0:
            roe.append(p / e * 100)
        if pd.notna(p) and ta:
            roa.append(p / ta * 100)
        if pd.notna(eb) and r:
            opm.append(eb / r * 100)
    o["roce_3y"] = np.mean(roce) if roce else np.nan
    o["roe_3y"] = np.mean(roe) if roe else np.nan
    o["roa_3y"] = np.mean(roa) if roa else np.nan
    o["opm_latest"] = opm[-1] if opm else np.nan
    o["opm_std"] = np.std(opm) if len(opm) >= 3 else np.nan
    o["rev_cagr_3y"], o["pat_cagr_3y"] = _cagr(A["revenue"]), _cagr(A["pat"])
    o["de"] = _ratio(debt, eq) if pd.notna(eq) and eq > 0 else np.nan
    p3, c3 = A["pat"].iloc[-3:], A["cfo"].iloc[-3:]
    o["pat_positive_3y"] = bool(len(p3) == 3 and (p3 > 0).all())
    o["cfo_pat_3y"] = _ratio(c3.sum(), p3.sum()) if len(p3) == 3 and len(c3) == 3 and p3.sum() > 0 else np.nan
    ie = last(A["interest"])
    o["interest_cover"] = _ratio(last(A["ebit"]), abs(ie)) if pd.notna(ie) and ie != 0 else np.nan

    # earnings momentum (quarterly YoY)
    qp, qr, qe = Q["pat"], Q["revenue"], Q["ebit"]

    def yoy(s, i=0):
        if len(s) < 5 + i:
            return np.nan
        a, b = s.iloc[-5 - i], s.iloc[-1 - i]
        return (b / a - 1) * 100 if a > 0 else np.nan
    o["pat_yoy_q"], o["rev_yoy_q"] = yoy(qp), yoy(qr)
    o["pat_yoy_prev_q"] = yoy(qp, 1)
    # yfinance usually has only 5 quarters, so the previous quarter's YoY is rarely available.
    # Fallback for "acceleration": compare with the latest full-year profit growth.
    pa = A["pat"]
    o["pat_growth_last_fy"] = (pa.iloc[-1] / pa.iloc[-2] - 1) * 100 if len(pa) >= 2 and pa.iloc[-2] > 0 else np.nan
    o["accel_basis"] = ("previous quarter" if pd.notna(o["pat_yoy_prev_q"])
                        else "last full year" if pd.notna(o["pat_growth_last_fy"]) else None)
    o["pat_growth_compare"] = o["pat_yoy_prev_q"] if pd.notna(o["pat_yoy_prev_q"]) else o["pat_growth_last_fy"]
    o["opm_q"] = _ratio(last(qe), last(qr)) * 100
    o["opm_q_yago"] = _ratio(qe.iloc[-5], qr.iloc[-5]) * 100 if len(qe) >= 5 and len(qr) >= 5 else np.nan

    # Piotroski (non-fin): latest year vs previous year
    if not is_fin and len(A["assets"]) >= 2 and len(A["pat"]) >= 2:
        y0, y1 = A["assets"].index[-1], A["assets"].index[-2]
        g = lambda k, y: A[k].get(y, np.nan)  # noqa: E731
        roa0, roa1 = _ratio(g("pat", y0), g("assets", y0)), _ratio(g("pat", y1), g("assets", y1))
        cfo0 = g("cfo", y0)
        lev0, lev1 = _ratio(g("debt", y0), g("assets", y0)), _ratio(g("debt", y1), g("assets", y1))
        cr0, cr1 = _ratio(g("cur_assets", y0), g("cur_liab", y0)), _ratio(g("cur_assets", y1), g("cur_liab", y1))
        gm = "gross" if pd.notna(g("gross", y0)) and pd.notna(g("gross", y1)) else "ebit"   # U1 fallback
        gm0, gm1 = _ratio(g(gm, y0), g("revenue", y0)), _ratio(g(gm, y1), g("revenue", y1))
        at0, at1 = _ratio(g("revenue", y0), g("assets", y0)), _ratio(g("revenue", y1), g("assets", y1))
        sh0, sh1 = g("shares", y0), g("shares", y1)
        checks = [roa0 > 0, cfo0 > 0, roa0 > roa1, cfo0 > g("pat", y0),
                  (lev0 < lev1) if pd.notna(lev0) and pd.notna(lev1) else (pd.isna(lev0) or lev0 == 0),
                  cr0 > cr1, (sh0 <= sh1 * 1.001) if pd.notna(sh0) and pd.notna(sh1) else False,
                  gm0 > gm1, at0 > at1]
        o["piotroski"] = int(sum(bool(x) for x in checks if not pd.isna(x)))
        o["piotroski_gm_basis"] = gm
        # Magic Formula inputs
        nwc = (g("cur_assets", y0) - (g("cash", y0) if pd.notna(g("cash", y0)) else 0)) - g("cur_liab", y0)
        cap = nwc + g("ppe", y0)
        eb = g("ebit", y0)
        o["mf_earnings_yield"] = _ratio(eb, ev) * 100
        o["mf_roc"] = _ratio(eb, cap) * 100 if pd.notna(cap) and cap > 0 else np.nan
        # Altman Z'' (emerging markets)
        ta = g("assets", y0)
        tl = ta - g("equity", y0) if pd.notna(ta) and pd.notna(g("equity", y0)) else np.nan
        wc = g("cur_assets", y0) - g("cur_liab", y0)
        if all(pd.notna(x) for x in (ta, tl, wc, g("retained", y0), eb)) and ta > 0 and tl > 0:
            o["altman_z"] = (3.25 + 6.56 * wc / ta + 3.26 * g("retained", y0) / ta + 6.72 * eb / ta
                             + 1.05 * g("equity", y0) / tl)
        # receivable days (receivables / revenue x 365), this year vs last year
        o["receivable_days"] = _ratio(g("receivables", y0), g("revenue", y0)) * 365
        o["receivable_days_prev"] = _ratio(g("receivables", y1), g("revenue", y1)) * 365
        o["receivable_days_growth"] = (_ratio(o["receivable_days"], o["receivable_days_prev"]) - 1) * 100
        o["revenue_growth_1y"] = (_ratio(g("revenue", y0), g("revenue", y1)) - 1) * 100
    if len(A["shares"]) >= 2:
        o["share_growth_1y"] = (_ratio(A["shares"].iloc[-1], A["shares"].iloc[-2]) - 1) * 100
    return o


def fundamentals(st, info, master, last_close, usdinr=None):
    usd = usd_statement_symbols(st, info, last_close)
    if usd and usdinr is not None and len(usdinr):
        st = convert_usd(st, usd, usdinr)
    elif usd:   # no exchange rate available: drop these stocks' statements rather than show wrong numbers
        st = st[~st["symbol"].isin(usd)]
        usd = []
    fin = set(master.loc[master["nse_industry"] == "Financial Services", "symbol"])
    sh_info = info.set_index("symbol")["sharesOutstanding"].to_dict() if "sharesOutstanding" in info else {}
    rows = []
    for sym, g in st.groupby("symbol"):
        rows.append(_fund_one(sym, g, last_close.get(sym, np.nan), sh_info.get(sym, np.nan), sym in fin))
    f = pd.DataFrame(rows)
    f["is_financial"] = f["symbol"].isin(fin)
    f["usd_converted"] = f["symbol"].isin(usd)
    return f


# ================================================================== ownership (F6)
W_PROMOTER, W_DII, W_FII = 2.0, 1.5, 1.0


def ownership(own):
    ok = own[own["parsed_ok"] == True].sort_values("quarter_end")  # noqa: E712
    rows = []
    for sym, g in ok.groupby("symbol"):
        g = g.drop_duplicates("quarter_end", keep="last")
        r = {"symbol": sym, "own_quarter": g["quarter_end"].iloc[-1], "own_quarters": len(g),
             "promoter_pct": g["promoter"].iloc[-1], "fii_pct": g["fii"].iloc[-1],
             "dii_pct": g["dii"].iloc[-1], "mf_pct": g["mf"].iloc[-1],
             "pledge_pct": g["pledge_pct_of_promoter"].iloc[-1]}
        if len(g) >= 5:
            b = g.iloc[-5]
            for k, col in (("promoter", "promoter"), ("fii", "fii"), ("dii", "dii")):
                r[f"d_{k}_4q"] = g[col].iloc[-1] - b[col]
            r["d_promoter_1q"] = g["promoter"].iloc[-1] - g["promoter"].iloc[-2]
            steps = g[["promoter", "fii", "dii"]].diff().iloc[-4:]
            r["buy_quarters"] = int(((W_PROMOTER * steps["promoter"] + W_DII * steps["dii"]
                                      + W_FII * steps["fii"]) > 0).sum())
            r["own_score_raw"] = (W_PROMOTER * r["d_promoter_4q"] + W_DII * r["d_dii_4q"]
                                  + W_FII * r["d_fii_4q"]) + 0.5 * r["buy_quarters"]
        rows.append(r)
    o = pd.DataFrame(rows)
    if "own_score_raw" in o:
        o["ownership_score"] = o["own_score_raw"].rank(pct=True) * 100
    return o
