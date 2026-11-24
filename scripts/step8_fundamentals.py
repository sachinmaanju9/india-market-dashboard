"""
Step 8 / F8 - Load fundamentals and measure coverage (Indian Stock Market Dashboard)

Part A  yfinance: annual + quarterly income statement, balance sheet, cash flow, and key info
        for every active stock -> data/fundamentals/yf_statements.parquet, yf_info.parquet
Part B  NSE shareholding master (promoter vs public, quarterly) via www.nseindia.in
        -> data/fundamentals/nse_shareholding.parquet
Part C  NSE shareholding XBRL sample (10 stocks): which FII / DII / MF / pledge fields exist
Report  step8_coverage.xlsx: for every field the screens need, how many stocks have it and how many periods.

Resumable: stocks fetched in the last 7 days are skipped. Stops a source after 15 failures in a row.
Usage (repo root):
    python scripts/step8_fundamentals.py                  # everything
    python scripts/step8_fundamentals.py --limit 20       # quick trial
    python scripts/step8_fundamentals.py --skip-yf        # NSE parts only
    python scripts/step8_fundamentals.py --skip-nse       # yfinance only
"""
import argparse
import io
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

MASTER = Path("data/universe/master.csv")
OUT = Path("data/fundamentals")
YF_ST = OUT / "yf_statements.parquet"
YF_INFO = OUT / "yf_info.parquet"
NSE_SH = OUT / "nse_shareholding.parquet"
REPORT = "step8_coverage.xlsx"
NSE_IN = "https://www.nseindia.in"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MAX_FAILS = 15

# Fields each screen needs: (statement, [yfinance line-item names to try in order])
NEEDED = {
    "Revenue": ("income", ["Total Revenue", "Operating Revenue"]),
    "Operating income / EBIT": ("income", ["Operating Income", "EBIT"]),
    "EBITDA": ("income", ["EBITDA", "Normalized EBITDA"]),
    "Net income (PAT)": ("income", ["Net Income", "Net Income Common Stockholders"]),
    "Interest expense": ("income", ["Interest Expense", "Interest Expense Non Operating"]),
    "Gross profit": ("income", ["Gross Profit"]),
    "Diluted EPS": ("income", ["Diluted EPS", "Basic EPS"]),
    "Total assets": ("balance", ["Total Assets"]),
    "Current assets": ("balance", ["Current Assets"]),
    "Current liabilities": ("balance", ["Current Liabilities"]),
    "Total debt": ("balance", ["Total Debt"]),
    "Equity": ("balance", ["Stockholders Equity", "Common Stock Equity"]),
    "Receivables": ("balance", ["Accounts Receivable", "Receivables"]),
    "Net fixed assets": ("balance", ["Net PPE"]),
    "Cash": ("balance", ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]),
    "Retained earnings": ("balance", ["Retained Earnings"]),
    "Shares outstanding": ("balance", ["Ordinary Shares Number", "Share Issued"]),
    "Operating cash flow": ("cashflow", ["Operating Cash Flow"]),
    "Capex": ("cashflow", ["Capital Expenditure"]),
    "Free cash flow": ("cashflow", ["Free Cash Flow"]),
}
INFO_KEYS = ["marketCap", "enterpriseValue", "trailingPE", "priceToBook", "sharesOutstanding",
             "returnOnEquity", "returnOnAssets", "debtToEquity", "sector", "industry", "currency",
             "financialCurrency", "mostRecentQuarter", "lastFiscalYearEnd",
             "dividendYield", "trailingAnnualDividendYield", "dividendRate", "payoutRatio"]   # DD5 dividends
LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append({"time": datetime.now().strftime("%H:%M:%S"), "message": msg})


# ------------------------------------------------------------------ Part A: yfinance
def _long(df, symbol, statement, freq):
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    d.columns = pd.to_datetime(d.columns, errors="coerce")
    d = d.loc[:, d.columns.notna()]
    out = d.stack().rename("value").reset_index()
    out.columns = ["item", "period_end", "value"]
    out.insert(0, "freq", freq)
    out.insert(0, "statement", statement)
    out.insert(0, "symbol", symbol)
    return out


def fetch_yf(symbols):
    import yfinance as yf
    old_st = pd.read_parquet(YF_ST) if YF_ST.exists() else pd.DataFrame()
    old_info = pd.read_parquet(YF_INFO) if YF_INFO.exists() else pd.DataFrame()
    fresh = set()
    if not old_info.empty and all(k in old_info for k in INFO_KEYS):   # new fields (e.g. dividends) -> refetch all
        recent = old_info[pd.to_datetime(old_info["fetched"]) >= pd.Timestamp(date.today() - timedelta(days=5))]
        fresh = set(recent["symbol"])
    todo = [s for s in symbols if s not in fresh]
    log(f"A. yfinance: {len(fresh)} fetched in last 5 days, {len(todo)} to fetch")
    st_rows, info_rows, fails = [], [], 0
    for i, sym in enumerate(todo, 1):
        try:
            t = yf.Ticker(f"{sym}.NS")
            parts = [_long(t.income_stmt, sym, "income", "annual"),
                     _long(t.quarterly_income_stmt, sym, "income", "quarterly"),
                     _long(t.balance_sheet, sym, "balance", "annual"),
                     _long(t.quarterly_balance_sheet, sym, "balance", "quarterly"),
                     _long(t.cashflow, sym, "cashflow", "annual"),
                     _long(t.quarterly_cashflow, sym, "cashflow", "quarterly")]
            info = t.info or {}
            got = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(
                not p.empty for p in parts) else pd.DataFrame()
            if got.empty and not info.get("marketCap"):
                raise RuntimeError("no data")
            st_rows.append(got)
            info_rows.append({"symbol": sym, "fetched": date.today().isoformat(),
                              **{k: info.get(k) for k in INFO_KEYS}})
            fails = 0
        except Exception as e:
            fails += 1
            info_rows.append({"symbol": sym, "fetched": None, "error": str(e)[:120]})
            if fails >= MAX_FAILS:
                log(f"   {MAX_FAILS} failures in a row - stopping yfinance at {i}/{len(todo)} (rerun to resume)")
                break
        time.sleep(0.6)
        if i % 50 == 0 or i == len(todo):
            log(f"   {i}/{len(todo)}")
            old_st, old_info = _save_yf(old_st, old_info, st_rows, info_rows)
            st_rows, info_rows = [], []
    _save_yf(old_st, old_info, st_rows, info_rows)


def _save_yf(old_st, old_info, st_rows, info_rows):
    OUT.mkdir(parents=True, exist_ok=True)
    new_st = pd.concat([x for x in st_rows if len(x)], ignore_index=True) if any(len(x) for x in st_rows) else pd.DataFrame()
    if len(new_st):
        if len(old_st):
            old_st = old_st[~old_st["symbol"].isin(new_st["symbol"].unique())]
        old_st = pd.concat([old_st, new_st], ignore_index=True)
        old_st["value"] = pd.to_numeric(old_st["value"], errors="coerce")
        old_st.to_parquet(YF_ST, index=False)
    if info_rows:
        ni = pd.DataFrame(info_rows)
        ok = ni[ni["fetched"].notna()]
        if len(old_info):
            old_info = old_info[~old_info["symbol"].isin(ok["symbol"])]
        old_info = pd.concat([old_info, ni], ignore_index=True).drop_duplicates("symbol", keep="last")
        for c in old_info.columns:
            if old_info[c].dtype == object and c not in ("symbol", "fetched", "error", "sector", "industry",
                                                         "currency", "financialCurrency"):
                old_info[c] = pd.to_numeric(old_info[c], errors="coerce")
        old_info.to_parquet(YF_INFO, index=False)
    return old_st, old_info


# ------------------------------------------------------------------ Part B/C: NSE shareholding
def nse_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "en-US,en;q=0.9", "Referer": f"{NSE_IN}/"})
    try:
        s.get(NSE_IN, timeout=20)
    except Exception:
        pass
    return s


def fetch_shareholding(symbols):
    s = nse_session()
    rows, fails = [], 0
    log(f"B. NSE shareholding master for {len(symbols)} stocks")
    for i, sym in enumerate(symbols, 1):
        try:
            r = s.get(f"{NSE_IN}/api/corporate-share-holdings-master?index=equities&symbol={requests.utils.quote(sym)}",
                      timeout=20)
            time.sleep(1.0)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            recs = r.json()
            recs = recs if isinstance(recs, list) else recs.get("data", [])
            for rec in recs:
                rows.append({"symbol": sym, **{k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                               for k, v in rec.items()}})
            fails = 0
        except Exception as e:
            fails += 1
            rows.append({"symbol": sym, "error": str(e)[:120]})
            if fails >= MAX_FAILS:
                log(f"   {MAX_FAILS} failures in a row - stopping NSE at {i}/{len(symbols)}")
                break
        if i % 100 == 0:
            log(f"   {i}/{len(symbols)}")
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.astype(str).to_parquet(NSE_SH, index=False)
    return df


def xbrl_sample(sh, n=10):
    """Download the latest XBRL for a few stocks and list ownership-related fields found."""
    if sh is None or sh.empty or "xbrl" not in sh:
        log("C. no XBRL links in shareholding data - skipped")
        return pd.DataFrame()
    s = nse_session()
    keys = re.compile(r"(Promoter|Foreign|FPI|FII|Institution|MutualFund|Mutual|Insurance|Pledge|Encumb|"
                      r"Public|Bank|Retail|Individual)", re.I)
    out = []
    picks = sh.dropna(subset=["xbrl"]).query("xbrl != 'None' and xbrl != 'nan'")
    picks = picks.groupby("symbol").head(1).head(n)
    log(f"C. XBRL sample: {len(picks)} stocks")
    for _, r in picks.iterrows():
        try:
            x = s.get(r["xbrl"], timeout=30)
            time.sleep(1.0)
            txt = x.text
            tags = re.findall(r"<([\w\-]+:)?([A-Za-z0-9_]+)[^>]*contextRef=\"([^\"]+)\"[^>]*>([^<]{1,40})</", txt)
            hits = {}
            for _, tag, ctx, val in tags:
                if keys.search(tag) or keys.search(ctx):   # categories often live in the context name
                    hits.setdefault(f"{tag} [{ctx}]" if keys.search(ctx) and not keys.search(tag) else tag,
                                    []).append(f"{ctx}={val.strip()}")
            for tag, vals in sorted(hits.items()):
                out.append({"symbol": r["symbol"], "filing_date": r.get("date"), "tag": tag,
                            "n_values": len(vals), "examples": "; ".join(vals[:4])[:250]})
            if not hits:
                out.append({"symbol": r["symbol"], "tag": "(none found)", "examples": txt[:200]})
        except Exception as e:
            out.append({"symbol": r["symbol"], "tag": "ERROR", "examples": str(e)[:200]})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ report
def coverage(master):
    sheets = {}
    fin = set(master.loc[master["nse_industry"] == "Financial Services", "symbol"])
    n_all, n_fin = len(master), len(fin)
    if YF_ST.exists():
        st = pd.read_parquet(YF_ST)
        st = st[st["value"].notna()]
        rows = []
        for field, (stmt, items) in NEEDED.items():
            for freq in ("annual", "quarterly"):
                sub = st[(st["statement"] == stmt) & (st["freq"] == freq) & st["item"].isin(items)]
                per = sub.groupby("symbol")["period_end"].nunique()
                rows.append({"field": field, "freq": freq, "yfinance names tried": ", ".join(items),
                             "stocks with any": int((per >= 1).sum()),
                             "stocks with enough periods (annual>=4, quarterly>=5)":
                                 int((per >= (4 if freq == "annual" else 5)).sum()),
                             "of which financials": int(per.index.isin(fin).sum()),
                             "median periods": float(per.median()) if len(per) else 0})
        cov = pd.DataFrame(rows)
        sheets["Field_coverage"] = cov
        ap = st[st["freq"] == "annual"].groupby("symbol")["period_end"].nunique()
        qp = st[st["freq"] == "quarterly"].groupby("symbol")["period_end"].nunique()
        latest_q = st[st["freq"] == "quarterly"].groupby("symbol")["period_end"].max()
        per_stock = master[["symbol", "company", "tag", "nse_industry"]].copy()
        per_stock["annual_periods"] = per_stock["symbol"].map(ap).fillna(0).astype(int)
        per_stock["quarterly_periods"] = per_stock["symbol"].map(qp).fillna(0).astype(int)
        per_stock["latest_quarter"] = per_stock["symbol"].map(latest_q)
        sheets["Per_stock"] = per_stock
        sheets["Latest_quarter_dist"] = latest_q.dt.to_period("Q").astype(str).value_counts() \
            .rename_axis("quarter").reset_index(name="stocks")
        sheets["All_item_names"] = st.groupby(["statement", "freq", "item"])["symbol"].nunique() \
            .reset_index(name="stocks").sort_values(["statement", "freq", "stocks"], ascending=[True, True, False])
    if YF_INFO.exists():
        info = pd.read_parquet(YF_INFO)
        sheets["Info_coverage"] = pd.DataFrame(
            [{"key": k, "stocks with value": int(info[k].notna().sum()) if k in info else 0} for k in INFO_KEYS])
    if NSE_SH.exists():
        sh = pd.read_parquet(NSE_SH)
        ok = sh[sh.get("error", pd.Series("nan", index=sh.index)).isin(["nan", "None"])] if "error" in sh else sh
        q = ok.groupby("symbol").size()
        sheets["Shareholding_coverage"] = pd.DataFrame([
            {"item": "stocks with any record", "value": int((q >= 1).sum())},
            {"item": "stocks with >= 5 quarters", "value": int((q >= 5).sum())},
            {"item": "stocks with >= 8 quarters", "value": int((q >= 8).sum())},
            {"item": "fields", "value": ", ".join(c for c in sh.columns if c != "symbol")[:500]},
            {"item": "errors", "value": int((sh["error"].notna() & ~sh["error"].isin(["nan", "None"])).sum())
             if "error" in sh else 0},
        ])
        sheets["Shareholding_sample"] = ok.head(30)
    summary = [("stocks in universe (excl. REIT/InvIT)", n_all), ("financial companies", n_fin)]
    if "Field_coverage" in sheets:
        summary.append(("stocks with any yfinance statements", int((sheets["Per_stock"]["annual_periods"] > 0).sum())))
    return summary, sheets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-yf", action="store_true")
    ap.add_argument("--skip-nse", action="store_true")
    ap.add_argument("--skip-xbrl-sample", action="store_true")
    a = ap.parse_args()
    m = pd.read_csv(MASTER)
    m = m[(m["status"] == "active") & (m.get("instrument", "Equity") != "REIT/InvIT")]
    syms = m["symbol"].tolist()[: a.limit or None]
    log(f"Step 8 coverage run {datetime.now():%Y-%m-%d %H:%M} on {len(syms)} stocks")
    if not a.skip_yf:
        fetch_yf(syms)
    xs = pd.DataFrame()
    if not a.skip_nse:
        sh = fetch_shareholding(syms)
        xs = pd.DataFrame() if a.skip_xbrl_sample else xbrl_sample(sh)
    summary, sheets = coverage(m)
    with pd.ExcelWriter(REPORT) as xw:
        pd.DataFrame(summary, columns=["item", "value"]).to_excel(xw, sheet_name="Summary", index=False)
        for k, v in sheets.items():
            v.to_excel(xw, sheet_name=k[:31], index=False)
        if len(xs):
            xs.to_excel(xw, sheet_name="XBRL_fields_sample", index=False)
        pd.DataFrame(LOG).to_excel(xw, sheet_name="Log", index=False)
    log(f"Done. Report: {REPORT}")


if __name__ == "__main__":
    main()
