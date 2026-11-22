"""
Step 2 - Data-source feasibility test (Indian Stock Market Dashboard)

Runs tests T1-T8 (on your Mac) or a subset (e.g. --only T1,T4 on GitHub Actions)
and writes everything to step2_results.xlsx. Nothing is installed or changed on
your computer apart from that results file.

Usage:
    python step2_test.py                 # all tests
    python step2_test.py --only T1,T4    # subset
"""
import argparse
import io
import random
import sys
import time
import traceback
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import requests

NSE_ARCH = "https://nsearchives.nseindia.com"
NSE_WWW = "https://www.nseindia.com"
NIFTY_IDX = "https://niftyindices.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
PAUSE = 1.0  # seconds between NSE requests (be polite)

SUMMARY = []   # one row per test
DETAILS = {}   # sheet name -> DataFrame
CTX = {}       # data shared between tests


# ---------------------------------------------------------------- helpers
def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


S = session()
_nse_cookies_ready = False


def get(url, timeout=30, need_cookies=False):
    """GET with retries. need_cookies=True visits nseindia.com first (needed for /api/)."""
    global _nse_cookies_ready
    if need_cookies and not _nse_cookies_ready:
        try:
            S.get(NSE_WWW, timeout=timeout)
            _nse_cookies_ready = True
            time.sleep(PAUSE)
        except Exception:
            pass
    last = None
    for attempt in range(3):
        try:
            r = S.get(url, timeout=timeout)
            time.sleep(PAUSE)
            if r.status_code == 200 and r.content:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:  # network error
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url} -> {last}")


def read_csv_bytes(content):
    """Read CSV, or the first CSV inside a zip."""
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            content = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(content))
    df.columns = [str(c).strip() for c in df.columns]
    return df


def weekdays_back(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def record(test, name, status, finding, detail_df=None):
    SUMMARY.append({"Test": test, "What": name, "Status": status, "Finding": finding})
    if detail_df is not None and len(detail_df):
        DETAILS[f"{test}"] = detail_df
    icon = {"PASS": "OK  ", "PARTIAL": "PART", "FAIL": "FAIL"}.get(status, status)
    print(f"[{icon}] {test} {name}: {finding}")


def run(test, name, fn):
    print(f"\n--- {test}: {name}")
    try:
        fn()
    except Exception as e:
        record(test, name, "FAIL", f"{type(e).__name__}: {e}".strip()[:300])
        traceback.print_exc()


# ---------------------------------------------------------------- T1
def t1():
    """New-format (UDiFF) daily stock file + full bhavdata (has delivery)."""
    rows, got = [], None
    for d in weekdays_back(date.today() - timedelta(days=1), 7):
        url = f"{NSE_ARCH}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
        try:
            df = read_csv_bytes(get(url).content)
            got = (d, df)
            rows.append({"date": d, "file": "UDiFF bhavcopy", "result": f"{len(df)} rows"})
            break
        except Exception as e:
            rows.append({"date": d, "file": "UDiFF bhavcopy", "result": str(e)[:150]})
    if not got:
        record("T1", "NSE stock file (new format)", "FAIL", "No file in last 7 weekdays",
               pd.DataFrame(rows))
        return
    d, df = got
    CTX["t1_date"] = d
    series_col = "SctySrs" if "SctySrs" in df.columns else None
    eq = df[df[series_col] == "EQ"] if series_col else df
    CTX["nse_recent"] = eq

    # delivery % lives in a different file
    deliv = "not tested"
    try:
        url2 = f"{NSE_ARCH}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
        bd = read_csv_bytes(get(url2).content)
        deliv = "present" if any("DELIV" in c.upper() for c in bd.columns) else "missing"
        rows.append({"date": d, "file": "sec_bhavdata_full", "result": f"{len(bd)} rows; delivery {deliv}"})
    except Exception as e:
        deliv = f"failed ({str(e)[:80]})"
        rows.append({"date": d, "file": "sec_bhavdata_full", "result": str(e)[:150]})

    cols = pd.DataFrame({"columns_in_udiff_file": list(df.columns)})
    DETAILS["T1_columns"] = cols
    status = "PASS" if len(eq) > 1500 and deliv == "present" else "PARTIAL"
    record("T1", "NSE stock file (new format)", status,
           f"{d}: {len(eq)} EQ-series rows; delivery data {deliv}", pd.DataFrame(rows))


# ---------------------------------------------------------------- T2
def t2():
    """Old dates (2021) - try several known URL patterns."""
    rows, ok = [], []
    for d in weekdays_back(date(2021, 11, 26), 5):
        mon = d.strftime("%b").upper()
        candidates = {
            "old bhavcopy (archives)": f"https://archives.nseindia.com/content/historical/EQUITIES/{d.year}/{mon}/cm{d:%d}{mon}{d.year}bhav.csv.zip",
            "old bhavcopy (nsearchives)": f"{NSE_ARCH}/content/historical/EQUITIES/{d.year}/{mon}/cm{d:%d}{mon}{d.year}bhav.csv.zip",
            "UDiFF pattern on old date": f"{NSE_ARCH}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
            "sec_bhavdata_full": f"{NSE_ARCH}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv",
        }
        for label, url in candidates.items():
            try:
                df = read_csv_bytes(get(url).content)
                rows.append({"date": d, "source": label, "result": f"OK {len(df)} rows",
                             "columns": ", ".join(df.columns[:15])})
                ok.append(label)
                if label in ("sec_bhavdata_full", "old bhavcopy (archives)", "old bhavcopy (nsearchives)"):
                    CTX.setdefault("nse_old", (d, df))
            except Exception as e:
                rows.append({"date": d, "source": label, "result": str(e)[:150], "columns": ""})
        if ok:
            break
    status = "PASS" if ok else "FAIL"
    record("T2", "NSE stock file (2021, old format)", status,
           ("Working: " + ", ".join(sorted(set(ok)))) if ok else "No 2021 source worked",
           pd.DataFrame(rows))


# ---------------------------------------------------------------- T3
INDEX_LISTS = {
    "Nifty 500": "ind_nifty500list.csv",
    "Nifty Microcap 250": "ind_niftymicrocap250_list.csv",
    "Nifty Smallcap 250": "ind_niftysmallcap250list.csv",
    "Nifty 50": "ind_nifty50list.csv",
    "Nifty Bank": "ind_niftybanklist.csv",
    "Nifty IPO": "ind_niftyipo_list.csv",
    "Nifty Auto": "ind_niftyautolist.csv",
    "Nifty IT": "ind_niftyitlist.csv",
    "Nifty Pharma": "ind_niftypharmalist.csv",
    "Nifty FMCG": "ind_niftyfmcglist.csv",
    "Nifty Metal": "ind_niftymetallist.csv",
    "Nifty Realty": "ind_niftyrealtylist.csv",
    "Nifty Media": "ind_niftymedialist.csv",
    "Nifty PSU Bank": "ind_niftypsubanklist.csv",
    "Nifty Private Bank": "ind_nifty_privatebanklist.csv",
    "Nifty Financial Services": "ind_niftyfinancelist.csv",
    "Nifty Oil & Gas": "ind_niftyoilgaslist.csv",
    "Nifty Consumer Durables": "ind_niftyconsumerdurableslist.csv",
    "Nifty Healthcare": "ind_niftyhealthcarelist.csv",
}


def t3():
    rows, lists = [], {}
    for name, fname in INDEX_LISTS.items():
        res = None
        for host in (f"{NSE_ARCH}/content/indices/", f"{NIFTY_IDX}/IndexConstituent/"):
            try:
                df = read_csv_bytes(get(host + fname).content)
                lists[name] = df
                res = {"index": name, "host": host, "stocks": len(df),
                       "has_industry": any("industry" in c.lower() for c in df.columns),
                       "columns": ", ".join(df.columns)}
                break
            except Exception as e:
                res = {"index": name, "host": host, "stocks": 0, "has_industry": False,
                       "columns": str(e)[:150]}
        rows.append(res)
    CTX["lists"] = lists
    found = sum(1 for r in rows if r["stocks"] > 0)
    core = all(k in lists for k in ("Nifty 500", "Nifty Microcap 250"))
    industry = all(r["has_industry"] for r in rows if r["stocks"] > 0) if found else False
    status = "PASS" if core and found == len(rows) and industry else ("PARTIAL" if core else "FAIL")
    if "Nifty 500" in lists:
        ind_col = next((c for c in lists["Nifty 500"].columns if "industry" in c.lower()), None)
        if ind_col:
            DETAILS["T3_industries"] = (lists["Nifty 500"][ind_col].value_counts()
                                        .rename_axis("industry").reset_index(name="stocks_in_nifty500"))
    record("T3", "NSE index stock lists", status,
           f"{found}/{len(rows)} lists downloaded; Industry column on all: {industry}",
           pd.DataFrame(rows))


# ---------------------------------------------------------------- T4
NEEDED_INDICES = ["Nifty 50", "Nifty Smallcap 250", "Nifty Bank", "Nifty Auto", "Nifty IT",
                  "Nifty Pharma", "Nifty FMCG", "Nifty Metal", "Nifty Realty", "Nifty Media",
                  "Nifty PSU Bank", "Nifty Private Bank", "Nifty Financial Services",
                  "Nifty Oil & Gas", "Nifty Consumer Durables", "Nifty Healthcare Index",
                  "Nifty Microcap 250", "Nifty 500"]


def t4():
    rows, per_date = [], {}
    targets = [("recent", weekdays_back(date.today() - timedelta(days=1), 7)),
               ("2021", weekdays_back(date(2021, 11, 26), 5))]
    for label, days in targets:
        for d in days:
            url = f"{NSE_ARCH}/content/indices/ind_close_all_{d:%d%m%Y}.csv"
            try:
                df = read_csv_bytes(get(url).content)
                per_date[label] = (d, df)
                rows.append({"which": label, "date": d, "result": f"{len(df)} indices"})
                break
            except Exception as e:
                rows.append({"which": label, "date": d, "result": str(e)[:150]})
    if not per_date:
        record("T4", "NSE all-indices daily close", "FAIL", "Neither date downloaded", pd.DataFrame(rows))
        return
    cover = []
    for label, (d, df) in per_date.items():
        name_col = next((c for c in df.columns if "index name" in c.lower()), df.columns[0])
        names = set(df[name_col].astype(str).str.strip().str.lower())
        for idx in NEEDED_INDICES:
            cover.append({"index": idx, "date_set": label, "present": idx.lower() in names})
        DETAILS[f"T4_names_{label}"] = pd.DataFrame({"index_names": sorted(df[name_col].astype(str))})
    cov = pd.DataFrame(cover)
    missing = cov[~cov.present]
    both = len(per_date) == 2
    status = "PASS" if both and missing.empty else "PARTIAL"
    record("T4", "NSE all-indices daily close", status,
           f"Dates: {', '.join(f'{k}={v[0]}' for k, v in per_date.items())}; "
           f"missing names: {len(missing)} (see T4 sheets; may just be naming differences)",
           pd.concat([pd.DataFrame(rows), cov], ignore_index=True))


# ---------------------------------------------------------------- T5
def t5():
    import yfinance as yf
    if "nse_old" not in CTX:
        record("T5", "Split/bonus check", "FAIL", "Needs a 2021 NSE file from T2")
        return
    d, old = CTX["nse_old"]
    sym_col = next(c for c in old.columns if c.upper() in ("SYMBOL", "TCKRSYMB"))
    close_col = next(c for c in old.columns if c.upper() in ("CLOSE", "CLOSE_PRICE", "CLSPRIC"))
    old[sym_col] = old[sym_col].astype(str).str.strip()
    rows = []
    for sym in ["BAJFINANCE", "TATASTEEL", "IRCTC"]:
        try:
            nse_close = float(old.loc[old[sym_col] == sym, close_col].iloc[0])
            t = yf.Ticker(f"{sym}.NS")
            h = t.history(start=d, end=d + timedelta(days=5), auto_adjust=False)
            yf_close = float(h["Close"].iloc[0])
            splits = t.splits
            splits = splits[splits.index.tz_localize(None) > pd.Timestamp(d)] if len(splits) else splits
            factor = float(splits.prod()) if len(splits) else 1.0
            rows.append({"symbol": sym, "date": d, "nse_raw_close": nse_close,
                         "yf_split_adj_close": round(yf_close, 2),
                         "ratio_raw/adj": round(nse_close / yf_close, 3),
                         "yf_split_factor_since": factor,
                         "splits_after_date": "; ".join(f"{i.date()}:{v}" for i, v in splits.items()),
                         "explained": abs(nse_close / yf_close - factor) / factor < 0.03})
        except Exception as e:
            rows.append({"symbol": sym, "date": d, "explained": False, "splits_after_date": str(e)[:150]})
        time.sleep(1)
    df = pd.DataFrame(rows)
    n = int(df["explained"].sum())
    record("T5", "Split/bonus check", "PASS" if n == len(df) else "PARTIAL",
           f"{n}/{len(df)} stocks: raw-vs-adjusted gap matches recorded splits/bonuses", df)


# ---------------------------------------------------------------- T6
def t6():
    import yfinance as yf
    lists = CTX.get("lists", {})
    syms = []
    for k in ("Nifty 500", "Nifty Microcap 250"):
        if k in lists:
            col = next(c for c in lists[k].columns if c.lower() == "symbol")
            syms += lists[k][col].astype(str).str.strip().tolist()
    if not syms:
        syms = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"]
    tickers = [f"{s}.NS" for s in dict.fromkeys(syms)]
    t0 = time.time()
    data = yf.download(tickers, period="1mo", interval="1d", group_by="ticker",
                       threads=True, progress=False, auto_adjust=False)
    secs = round(time.time() - t0, 1)
    ok = 0
    for tk in tickers:
        try:
            if data[tk]["Close"].dropna().shape[0] > 5:
                ok += 1
        except Exception:
            pass
    rows = [{"part": "bulk daily", "tickers": len(tickers), "with_data": ok, "seconds": secs}]

    intra = yf.download([f"{s}.NS" for s in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"]],
                        period="1d", interval="5m", group_by="ticker", progress=False)
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    try:
        last_bar = intra.index.max().tz_convert("Asia/Kolkata")
        lag_min = round((now - last_bar).total_seconds() / 60, 1)
    except Exception:
        last_bar, lag_min = None, None
    rows.append({"part": "5-min intraday", "tickers": 5, "with_data": int(len(intra) > 0),
                 "seconds": None, "last_bar_IST": str(last_bar), "lag_minutes_now": lag_min,
                 "note": "lag only meaningful if run during market hours (9:15-15:30 IST)"})
    status = "PASS" if ok >= 0.95 * len(tickers) else ("PARTIAL" if ok > 0 else "FAIL")
    record("T6", "yfinance bulk + intraday", status,
           f"Daily: {ok}/{len(tickers)} tickers in {secs}s. Intraday last bar {last_bar} (lag {lag_min} min)",
           pd.DataFrame(rows))


# ---------------------------------------------------------------- T7/T8 sample
def sample_15():
    lists = CTX.get("lists", {})
    rnd = random.Random(42)

    def pick(name, fallback):
        if name in lists:
            col = next(c for c in lists[name].columns if c.lower() == "symbol")
            s = lists[name][col].astype(str).str.strip().tolist()
            return rnd.sample(s, 5)
        return fallback
    large = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"]
    small = pick("Nifty Smallcap 250", ["IEX", "CDSL", "KPITTECH", "RADICO", "BLS"])
    micro = pick("Nifty Microcap 250", ["SHAILY", "KRISHANA", "TARC", "SANSERA", "HCG"])
    return [("Large", s) for s in large] + [("Small", s) for s in small] + [("Micro", s) for s in micro]


def t7():
    import yfinance as yf
    rows = []
    for bucket, sym in sample_15():
        r = {"bucket": bucket, "symbol": sym}
        try:
            t = yf.Ticker(f"{sym}.NS")
            for label, df in [("annual_IS", t.financials), ("quarterly_IS", t.quarterly_financials),
                              ("annual_BS", t.balance_sheet), ("annual_CF", t.cashflow)]:
                r[f"yf_{label}_periods"] = 0 if df is None else df.shape[1]
                r[f"yf_{label}_lines"] = 0 if df is None else df.shape[0]
            info = t.info or {}
            for k in ("marketCap", "trailingPE", "priceToBook", "returnOnEquity",
                      "debtToEquity", "heldPercentInsiders", "dividendYield"):
                r[f"yf_{k}"] = info.get(k)
        except Exception as e:
            r["yf_error"] = str(e)[:150]
        try:
            url = f"{NSE_WWW}/api/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly"
            j = get(url, need_cookies=True).json()
            recs = j if isinstance(j, list) else j.get("data", [])
            r["nse_results_filings"] = len(recs)
            if recs:
                r["nse_latest_period"] = recs[0].get("toDate") or recs[0].get("period")
        except Exception as e:
            r["nse_results_filings"] = f"ERR {str(e)[:100]}"
        rows.append(r)
        time.sleep(1)
    df = pd.DataFrame(rows)
    yf_ok = int((pd.to_numeric(df.get("yf_annual_IS_periods"), errors="coerce") >= 3).sum())
    nse_ok = int((pd.to_numeric(df.get("nse_results_filings"), errors="coerce") >= 4).sum())
    status = "PASS" if yf_ok >= 12 or nse_ok >= 12 else ("PARTIAL" if yf_ok or nse_ok else "FAIL")
    record("T7", "Fundamentals coverage", status,
           f"yfinance ≥3 yrs annual P&L: {yf_ok}/15; NSE results filings ≥4: {nse_ok}/15", df)


def t8():
    rows = []
    for bucket, sym in sample_15():
        r = {"bucket": bucket, "symbol": sym}
        for label, url in [
            ("shareholding", f"{NSE_WWW}/api/corporate-share-holdings-master?index=equities&symbol={sym}"),
            ("pledge", f"{NSE_WWW}/api/corporate-pledgedata?index=equities&symbol={sym}"),
        ]:
            try:
                j = get(url, need_cookies=True).json()
                recs = j if isinstance(j, list) else j.get("data", [])
                r[f"{label}_records"] = len(recs)
                if recs and label == "shareholding":
                    r["shareholding_fields"] = ", ".join(list(recs[0].keys())[:12])
                    r["latest_quarter"] = recs[0].get("date") or recs[0].get("asOnDate")
            except Exception as e:
                r[f"{label}_records"] = f"ERR {str(e)[:100]}"
        rows.append(r)
    df = pd.DataFrame(rows)
    ok = int((pd.to_numeric(df.get("shareholding_records"), errors="coerce") >= 8).sum())
    record("T8", "Shareholding & pledge (NSE)", "PASS" if ok >= 12 else ("PARTIAL" if ok else "FAIL"),
           f"≥8 quarters of shareholding for {ok}/15 stocks", df)


# ---------------------------------------------------------------- main
TESTS = {
    "T1": ("NSE stock file (new format)", t1),
    "T2": ("NSE stock file (2021, old format)", t2),
    "T3": ("NSE index stock lists", t3),
    "T4": ("NSE all-indices daily close", t4),
    "T5": ("Split/bonus check", t5),
    "T6": ("yfinance bulk + intraday", t6),
    "T7": ("Fundamentals coverage", t7),
    "T8": ("Shareholding & pledge (NSE)", t8),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma list, e.g. T1,T4")
    ap.add_argument("--out", default="step2_results.xlsx")
    a = ap.parse_args()
    chosen = [t.strip().upper() for t in a.only.split(",")] if a.only else list(TESTS)
    print(f"Step 2 tests: {', '.join(chosen)} | started {datetime.now():%Y-%m-%d %H:%M} | "
          f"Python {sys.version.split()[0]}")
    for t in chosen:
        name, fn = TESTS[t]
        run(t, name, fn)
    with pd.ExcelWriter(a.out) as xw:
        pd.DataFrame(SUMMARY).to_excel(xw, sheet_name="Summary", index=False)
        for sheet, df in DETAILS.items():
            df.to_excel(xw, sheet_name=sheet[:31], index=False)
    print(f"\nDone. Results saved to {a.out}")
    print(pd.DataFrame(SUMMARY)[["Test", "Status", "Finding"]].to_string(index=False))


if __name__ == "__main__":
    main()
