"""
Step 3 - Build the master stock list + sector mapping (Indian Stock Market Dashboard)

What it does (all decisions are in PROJECT.md, Step 3):
  1. Downloads index stock lists (Nifty 500, Microcap 250, Nifty IPO, benchmarks, sector indices)
     and keeps a dated snapshot of each list whenever it changes (D2).
  2. Finds recent IPOs (listed < 1 year) and adds those whose market cap >= the smallest
     Microcap 250 stock (yfinance market cap, D4). Tag IPO until it joins an index or turns 1 year (D6).
  3. Fetches NSE's 4-level classification (macro / sector / industry / basic industry) per stock (D5).
     Tries NSE first; if NSE refuses, uses yfinance industry labels. Cached; stops after 10 failures in a row.
  4. Writes data/universe/master.csv (key = ISIN, D1) and step3_report.xlsx for review.

Usage:
    python step3_universe.py                       # full run (~20 min first time)
    python step3_universe.py --skip-classification  # faster, skips part 3
"""
import argparse
import hashlib
import io
import json
import time
import traceback
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

TODAY = date.today()
DATA = Path("data/universe")
SNAP = DATA / "snapshots"
MASTER = DATA / "master.csv"
CLASS_CACHE = DATA / "classification_cache.csv"

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
PAUSE = 1.0

# Universe lists (file names confirmed in Step 2)
CORE_LISTS = {
    "Nifty 500": ["ind_nifty500list.csv"],
    "Nifty Microcap 250": ["ind_niftymicrocap250_list.csv"],
    "Nifty IPO": ["ind_niftyipo_list.csv"],
    "Nifty 50": ["ind_nifty50list.csv"],
    "Nifty Smallcap 250": ["ind_niftysmallcap250list.csv"],
}
# Sector indices (D3: sectoral only). Older ones confirmed in Step 2; newer ones use guessed
# file names - the script tries each candidate and reports any it can't find.
SECTOR_LISTS = {
    "Nifty Auto": ["ind_niftyautolist.csv"],
    "Nifty Bank": ["ind_niftybanklist.csv"],
    "Nifty Private Bank": ["ind_nifty_privatebanklist.csv"],
    "Nifty PSU Bank": ["ind_niftypsubanklist.csv"],
    "Nifty Financial Services": ["ind_niftyfinancelist.csv"],
    "Nifty FMCG": ["ind_niftyfmcglist.csv"],
    "Nifty IT": ["ind_niftyitlist.csv"],
    "Nifty Pharma": ["ind_niftypharmalist.csv"],
    "Nifty Healthcare Index": ["ind_niftyhealthcarelist.csv"],
    "Nifty Metal": ["ind_niftymetallist.csv"],
    "Nifty Realty": ["ind_niftyrealtylist.csv"],
    "Nifty Media": ["ind_niftymedialist.csv"],
    "Nifty Oil & Gas": ["ind_niftyoilgaslist.csv"],
    "Nifty Consumer Durables": ["ind_niftyconsumerdurableslist.csv"],
    # newer (guessed names)
    "Nifty Capital Goods": ["ind_niftycapitalgoodslist.csv", "ind_niftycapitalgoods_list.csv", "ind_niftycapitalgoodlist.csv"],
    "Nifty Cement": ["ind_niftycementlist.csv", "ind_niftycement_list.csv"],
    "Nifty Chemicals": ["ind_niftychemicalslist.csv", "ind_niftychemicals_list.csv"],
    "Nifty Power": ["ind_niftypowerlist.csv", "ind_niftypower_list.csv"],
    "Nifty Telecommunications": ["ind_niftytelecommunicationslist.csv", "ind_niftytelecomlist.csv", "ind_niftytelecommunications_list.csv"],
    "Nifty NBFC": ["ind_niftynbfclist.csv", "ind_niftynbfc_list.csv"],
    "Nifty Insurance": ["ind_niftyinsurancelist.csv", "ind_niftyinsurance_list.csv"],
    "Nifty Hospitals": ["ind_niftyhospitalslist.csv", "ind_niftyhospitals_list.csv"],
    "Nifty Retail": ["ind_niftyretaillist.csv", "ind_niftyretail_list.csv"],
    "Nifty Housing Finance": ["ind_niftyhousingfinancelist.csv", "ind_niftyhousingfinance_list.csv"],
    "Nifty Construction": ["ind_niftyconstructionlist.csv", "ind_niftyconstruction_list.csv"],
    "Nifty Consumer Services": ["ind_niftyconsumerserviceslist.csv", "ind_niftyconsumerservices_list.csv"],
}
HOSTS = [f"{NSE_ARCH}/content/indices/", f"{NIFTY_IDX}/IndexConstituent/"]

REPORT = {}
LOG = []


def log(msg):
    print(msg)
    LOG.append({"time": datetime.now().strftime("%H:%M:%S"), "message": msg})


# ---------------------------------------------------------------- http
S = requests.Session()
S.headers.update(HEADERS)
_cookies = False


def get(url, timeout=30, need_cookies=False, tries=3):
    global _cookies
    if need_cookies and not _cookies:
        try:
            S.get(NSE_WWW, timeout=timeout)
            _cookies = True
            time.sleep(PAUSE)
        except Exception:
            pass
    last = None
    for a in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            time.sleep(PAUSE)
            if r.status_code == 200 and r.content:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                break
            if r.status_code in (401, 403) and need_cookies:
                _cookies = False
                S.get(NSE_WWW, timeout=timeout)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (a + 1))
    raise RuntimeError(f"{url} -> {last}")


def read_csv_bytes(content):
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            content = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(content))
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ---------------------------------------------------------------- 1. lists + snapshots
def save_snapshot(fname, content):
    """D2: keep a dated copy only when the list changed since the last snapshot."""
    digest = hashlib.md5(content).hexdigest()
    previous = sorted(SNAP.glob(f"*/{fname}"))
    if previous and hashlib.md5(previous[-1].read_bytes()).hexdigest() == digest:
        return "unchanged"
    d = SNAP / TODAY.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_bytes(content)
    return "new snapshot" if previous else "first snapshot"


def download_lists(spec):
    out, rows = {}, []
    for name, candidates in spec.items():
        got = None
        for fname in candidates:
            for host in HOSTS:
                try:
                    r = get(host + fname, tries=2)
                    df = read_csv_bytes(r.content)
                    if "ISIN Code" not in df.columns:
                        continue
                    got = (fname, host, df, save_snapshot(fname, r.content))
                    break
                except Exception:
                    continue
            if got:
                break
        if got:
            fname, host, df, snap = got
            out[name] = df
            rows.append({"index": name, "file": fname, "host": host, "stocks": len(df), "snapshot": snap})
        else:
            rows.append({"index": name, "file": "NOT FOUND (tried: " + ", ".join(candidates) + ")",
                         "host": "", "stocks": 0, "snapshot": ""})
    return out, pd.DataFrame(rows)


# ---------------------------------------------------------------- 2. IPO candidates
def recent_listings():
    """NSE equity master: all EQ-series stocks with listing date."""
    r = get(f"{NSE_ARCH}/content/equities/EQUITY_L.csv")
    df = read_csv_bytes(r.content)
    df.columns = [c.upper().strip() for c in df.columns]
    df["LISTING_DATE"] = pd.to_datetime(df["DATE OF LISTING"], format="%d-%b-%Y", errors="coerce").dt.date
    df = df[df["SERIES"].astype(str).str.strip().isin(["EQ", "BE"])]
    return df.rename(columns={"SYMBOL": "symbol", "NAME OF COMPANY": "company", "ISIN NUMBER": "isin"})


def market_caps(symbols):
    """D4: yfinance market cap in Rs crore."""
    import yfinance as yf
    out = {}
    for i, s in enumerate(symbols, 1):
        mc = None
        try:
            fi = yf.Ticker(f"{s}.NS").fast_info
            mc = fi.get("marketCap") if hasattr(fi, "get") else fi["marketCap"]
        except Exception:
            pass
        out[s] = round(mc / 1e7, 1) if mc else None  # rupees -> crore
        if i % 50 == 0:
            log(f"   market caps: {i}/{len(symbols)}")
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------- 3. classification
NSE_IN = "https://www.nseindia.in"   # shareholding API redirected here and worked (diagnostic A4)


def _nse_class(sym):
    r = S.get(f"{NSE_IN}/api/quote-equity?symbol={requests.utils.quote(sym)}", timeout=15,
              headers={"Referer": f"{NSE_IN}/"})
    time.sleep(PAUSE)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    ii = r.json().get("industryInfo") or {}
    if not ii.get("basicIndustry"):
        raise RuntimeError("no industryInfo in response")
    return {"macro": ii.get("macro"), "sector": ii.get("sector"), "industry": ii.get("industry"),
            "basic_industry": ii.get("basicIndustry"), "class_source": "NSE"}


def _yf_class(sym):
    import yfinance as yf
    info = yf.Ticker(f"{sym}.NS").info or {}
    time.sleep(0.4)
    if not info.get("industry"):
        raise RuntimeError("no industry in yfinance info")
    return {"yf_sector": info.get("sector"), "basic_industry": info.get("industry"), "class_source": "yfinance"}


MANUAL = Path("data/manual")
OVERRIDES = MANUAL / "industry_overrides.csv"
DEFAULT_OVERRIDES = [("AEROPLANE", "Fast Moving Consumer Goods"), ("BUILDPRO", "Consumer Services"),
                     ("DECNGOLD", "Metals & Mining"), ("GSPLTRANS", "Oil Gas & Consumable Fuels"),
                     ("MOLBIO", "Healthcare"), ("SMT", "Healthcare")]
BUFFER = 0.80  # E1: an added IPO/NEWLIST stock leaves only below 80% of the threshold


def bse_file_classification(isin_map):
    """Optional: BSE 'List of Scrips' export saved in data/manual/ (any .csv/.xlsx whose name starts
    with 'bse' or 'Equity'). BSE uses the same NSE-BSE common 4-level classification since Mar-2022.
    Matched on ISIN. Column names are detected loosely and reported."""
    files = sorted([*MANUAL.glob("bse*.csv"), *MANUAL.glob("Equity*.csv"), *MANUAL.glob("bse*.xls*"),
                    *MANUAL.glob("Equity*.xls*")]) if MANUAL.exists() else []
    if not files:
        log("   no BSE file in data/manual/ - skipping")
        return pd.DataFrame()
    f = files[-1]
    df = pd.read_csv(f) if f.suffix == ".csv" else pd.read_excel(f)
    df.columns = [str(c).strip() for c in df.columns]
    log(f"   BSE file {f.name}: {len(df)} rows; columns: {', '.join(df.columns)}")
    low = {c.lower(): c for c in df.columns}

    def col(*keys):
        for k in keys:
            for lc, c in low.items():
                if k in lc:
                    return c
        return None
    c_isin = col("isin")
    mapping = {"macro": col("sector name"), "sector": col("industry new name"),
               "industry": col("igroup"), "basic_industry": col("isubgroup")}
    if not c_isin or not mapping["basic_industry"]:
        log(f"   BSE file: could not find ISIN / basic-industry columns ({mapping}) - skipping")
        return pd.DataFrame()
    df[c_isin] = df[c_isin].astype(str).str.strip()
    by_isin = df.drop_duplicates(c_isin).set_index(c_isin)
    rows = []
    for sym, isin in isin_map.items():
        if isin in by_isin.index and pd.notna(by_isin.at[isin, mapping["basic_industry"]]):
            r = {"symbol": sym, "fetched": TODAY.isoformat(), "class_source": "BSE file"}
            for k, c in mapping.items():
                r[k] = by_isin.at[isin, c] if c else None
            rows.append(r)
    log(f"   BSE file matched {len(rows)}/{len(isin_map)} stocks by ISIN")
    return pd.DataFrame(rows)


def classification(symbols, isin_map=None):
    """D5. Order: BSE manual file (if present) -> NSE (5-stock probe) -> yfinance industry labels.
    Circuit breaker: stop a source after 10 failures in a row (lesson from run 1)."""
    cache = pd.read_csv(CLASS_CACHE) if CLASS_CACHE.exists() else pd.DataFrame(columns=["symbol"])
    # Only rows that actually have a finer industry count as cached (run 1 saved failed rows by mistake)
    cache = cache[cache["basic_industry"].notna()] if "basic_industry" in cache else cache.iloc[0:0]
    bse = bse_file_classification(isin_map or {})
    if len(bse):  # BSE file wins over older yfinance labels
        cache = pd.concat([cache[~cache["symbol"].isin(bse["symbol"])], bse], ignore_index=True)
        cache.to_csv(CLASS_CACHE, index=False)
    have = set(cache["symbol"].astype(str))
    todo = [x for x in symbols if x not in have]
    log(f"   classification: {len(have)} cached, {len(todo)} to fetch")
    if not todo:
        return cache, pd.DataFrame()

    source = _yf_class
    probe_ok = 0
    for sym in todo[:5]:
        try:
            _nse_class(sym)
            probe_ok += 1
        except Exception as e:
            log(f"   NSE probe {sym}: {e}")
    if probe_ok >= 4:
        source = _nse_class
    log(f"   source chosen: {'NSE' if source is _nse_class else 'yfinance'} (NSE probe {probe_ok}/5)")

    rows, errors, streak = [], [], 0
    for i, sym in enumerate(todo, 1):
        try:
            r = {"symbol": sym, "fetched": TODAY.isoformat(), **source(sym)}
            rows.append(r)
            streak = 0
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)[:150]})
            streak += 1
            if streak >= 10:
                log(f"   10 failures in a row - stopping classification at {i}/{len(todo)}")
                break
        if i % 100 == 0:
            log(f"   classification: {i}/{len(todo)}")
            pd.concat([cache, pd.DataFrame(rows)]).to_csv(CLASS_CACHE, index=False)
    cache = pd.concat([cache, pd.DataFrame(rows)], ignore_index=True)
    cache.to_csv(CLASS_CACHE, index=False)
    log(f"   classification done: {len(rows)} ok, {len(errors)} failed (retried next run)")
    return cache, pd.DataFrame(errors)


# ---------------------------------------------------------------- 5. master table
def build_master(core, sectors, ipo_adds, cls, mcaps):
    recs = {}

    def add(df, tag):
        for _, row in df.iterrows():
            isin = str(row["ISIN Code"]).strip()
            if str(row["Symbol"]).strip().upper().startswith("DUMMY"):
                continue  # E3: NSE placeholder during demergers, not a real stock
            if isin not in recs:
                recs[isin] = {"isin": isin, "symbol": str(row["Symbol"]).strip(),
                              "company": str(row["Company Name"]).strip(),
                              "nse_industry": str(row["Industry"]).strip(), "tag": tag}

    add(core["Nifty 500"], "N500")
    add(core["Nifty Microcap 250"], "NEXT250")
    ipo_isins = set(core["Nifty IPO"]["ISIN Code"].astype(str).str.strip()) if "Nifty IPO" in core else set()
    for _, row in ipo_adds.iterrows():
        isin = str(row["isin"]).strip()
        if isin not in recs:
            # Real IPOs are in the Nifty IPO list; others are BSE-to-NSE moves or demergers
            recs[isin] = {"isin": isin, "symbol": row["symbol"], "company": row["company"],
                          "nse_industry": None, "tag": "IPO" if isin in ipo_isins else "NEWLIST",
                          "listing_date": row["LISTING_DATE"]}

    m = pd.DataFrame(recs.values())
    # sector-index memberships (by ISIN)
    memb = {}
    for name, df in {**sectors, **{k: core[k] for k in ("Nifty 50", "Nifty Smallcap 250", "Nifty IPO") if k in core}}.items():
        for isin in df["ISIN Code"].astype(str).str.strip():
            memb.setdefault(isin, []).append(name)
    m["in_indices"] = m["isin"].map(lambda i: "; ".join(memb.get(i, [])))
    m["sector_indices_count"] = m["isin"].map(lambda i: len([x for x in memb.get(i, []) if x in sectors]))
    if cls is not None and len(cls):
        c = cls.drop_duplicates("symbol", keep="last").set_index("symbol")
        for col in ("macro", "sector", "industry", "basic_industry", "yf_sector", "class_source"):
            if col in c:
                m[col] = m["symbol"].map(c[col])
    # New additions have no NSE industry (not in any index list). Infer it: the most common
    # NSE industry among indexed stocks with the same finer industry label.
    m["nse_industry_source"] = m["nse_industry"].notna().map({True: "NSE list", False: None})
    if "basic_industry" in m:
        known = m[m["nse_industry"].notna() & m["basic_industry"].notna()]
        mode = known.groupby("basic_industry")["nse_industry"].agg(lambda x: x.value_counts().index[0])
        fill = m["nse_industry"].isna() & m["basic_industry"].isin(mode.index)
        m.loc[fill, "nse_industry"] = m.loc[fill, "basic_industry"].map(mode)
        m.loc[fill, "nse_industry_source"] = "inferred"
    # E4: manual overrides (created with defaults on first run; edit freely)
    if not OVERRIDES.exists():
        OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(DEFAULT_OVERRIDES, columns=["symbol", "nse_industry"]).to_csv(OVERRIDES, index=False)
    ov = pd.read_csv(OVERRIDES).dropna(subset=["symbol"])
    for _, r in ov.iterrows():
        hit = m["symbol"] == str(r["symbol"]).strip()
        if hit.any() and pd.notna(r.get("nse_industry")):
            m.loc[hit, "nse_industry"] = r["nse_industry"]
            m.loc[hit, "nse_industry_source"] = "manual"
    # E2: REITs/InvITs - kept for price & sector views, excluded from fundamental screeners
    is_trust = m["company"].str.contains(r"\bREIT\b|Real Estate (?:Investment )?Trust|InvIT|Infrastructure Investment Trust",
                                         case=False, regex=True, na=False)
    m["instrument"] = is_trust.map({True: "REIT/InvIT", False: "Equity"})
    if "basic_industry" in m:
        m.loc[is_trust & m["basic_industry"].isna(), "basic_industry"] = "REIT"
    m["market_cap_cr"] = m["symbol"].map(mcaps)
    m["status"] = "active"
    m["last_seen"] = TODAY.isoformat()

    # merge with previous master: keep date_added, mark dropped (never delete)
    if MASTER.exists():
        old = pd.read_csv(MASTER)
        m["date_added"] = m["isin"].map(old.set_index("isin")["date_added"]).fillna(TODAY.isoformat())
        gone = old[~old["isin"].isin(m["isin"])].copy()
        gone.loc[gone["status"] == "active", "status"] = "dropped"
        m = pd.concat([m, gone], ignore_index=True)
    else:
        m["date_added"] = TODAY.isoformat()
    return m


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-classification", action="store_true")
    a = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    log(f"Step 3 started {datetime.now():%Y-%m-%d %H:%M}")

    log("1/4 Downloading index lists")
    core, core_rep = download_lists(CORE_LISTS)
    sectors, sec_rep = download_lists(SECTOR_LISTS)
    REPORT["Lists"] = pd.concat([core_rep, sec_rep], ignore_index=True)
    if "Nifty 500" not in core or "Nifty Microcap 250" not in core:
        raise SystemExit("Core lists missing - cannot build universe. See output above.")

    log("2/4 Recent IPOs and new listings")
    in750 = set(core["Nifty 500"]["ISIN Code"].str.strip()) | set(core["Nifty Microcap 250"]["ISIN Code"].str.strip())
    ipo_adds = pd.DataFrame(columns=["symbol", "company", "isin", "LISTING_DATE"])
    mcaps = {}
    try:
        eq = recent_listings()
        cutoff = TODAY - timedelta(days=365)
        recent = eq[(eq["LISTING_DATE"] >= cutoff) & (~eq["isin"].str.strip().isin(in750))].copy()
        ipo_isins = set(core.get("Nifty IPO", pd.DataFrame({"ISIN Code": []}))["ISIN Code"].str.strip())
        # Nifty IPO stocks listed within a year but missing from EQUITY_L filter, just in case
        extra = eq[eq["isin"].str.strip().isin(ipo_isins - in750) & (eq["LISTING_DATE"] >= cutoff)]
        cands = pd.concat([recent, extra]).drop_duplicates("isin")
        log(f"   {len(cands)} stocks listed in the last 12 months and not in the 750")
        micro_syms = core["Nifty Microcap 250"]["Symbol"].str.strip().tolist()
        mcaps = market_caps(micro_syms + cands["symbol"].str.strip().tolist())
        micro_caps = [mcaps[s] for s in micro_syms if mcaps.get(s)]
        threshold = min(micro_caps) if micro_caps else None
        log(f"   threshold = smallest Microcap 250 market cap = Rs {threshold} cr "
            f"({len(micro_caps)}/{len(micro_syms)} caps found)")
        cands["market_cap_cr"] = cands["symbol"].str.strip().map(mcaps)
        prev_adds = set()
        if MASTER.exists():
            pm = pd.read_csv(MASTER)
            prev_adds = set(pm[(pm["status"] == "active") & pm["tag"].isin(["IPO", "NEWLIST"])]["isin"])
        if threshold:
            already = cands["isin"].str.strip().isin(prev_adds)
            # an already-added stock with NO market cap this week (yfinance gap) is kept, not dropped
            cands["passes"] = cands["market_cap_cr"].ge(threshold) | \
                (already & cands["market_cap_cr"].ge(BUFFER * threshold)) | \
                (already & cands["market_cap_cr"].isna())
            cands["rule"] = (cands["market_cap_cr"].ge(threshold)).map({True: "above threshold", False: ""})
            cands.loc[already & cands["passes"] & (cands["rule"] == ""), "rule"] = "kept by 80% buffer"
            cands.loc[already & cands["market_cap_cr"].isna(), "rule"] = "kept: market cap unavailable this week"
        else:
            cands["passes"], cands["rule"] = False, "no threshold"
        REPORT["IPO_candidates"] = cands[["symbol", "company", "isin", "LISTING_DATE", "market_cap_cr", "passes", "rule"]] \
            .sort_values("market_cap_cr", ascending=False)
        ipo_adds = cands[cands["passes"]].copy()
        log(f"   {len(ipo_adds)} IPOs pass and are added")
    except Exception as e:
        log(f"   IPO step failed: {e}")
        traceback.print_exc()

    cls = None
    if not a.skip_classification:
        log("3/4 Finer industry classification")
        syms = (core["Nifty 500"]["Symbol"].str.strip().tolist()
                + core["Nifty Microcap 250"]["Symbol"].str.strip().tolist()
                + ipo_adds["symbol"].astype(str).str.strip().tolist())
        isin_map = {}
        for k in ("Nifty 500", "Nifty Microcap 250"):
            isin_map.update(dict(zip(core[k]["Symbol"].str.strip(), core[k]["ISIN Code"].str.strip())))
        isin_map.update(dict(zip(ipo_adds["symbol"].astype(str).str.strip(), ipo_adds["isin"].astype(str).str.strip())))
        syms = [x for x in syms if not str(x).upper().startswith("DUMMY")]
        cls, cls_err = classification(list(dict.fromkeys(syms)), isin_map)
        if len(cls_err):
            REPORT["Class_errors"] = cls_err
    elif CLASS_CACHE.exists():
        cls = pd.read_csv(CLASS_CACHE)

    log("4/4 Building master table")
    m = build_master(core, sectors, ipo_adds, cls, mcaps)
    m.to_csv(MASTER, index=False)
    REPORT["Master"] = m

    active = m[m.status == "active"]
    summary = [
        ("Active stocks", len(active)),
        ("  N500", int((active.tag == "N500").sum())),
        ("  NEXT250", int((active.tag == "NEXT250").sum())),
        ("  IPO", int((active.tag == "IPO").sum())),
        ("  NEWLIST", int((active.tag == "NEWLIST").sum())),
        ("REIT/InvIT (excluded from fundamental screeners)", int((active.get("instrument") == "REIT/InvIT").sum())),
        ("NSE industry set manually", int((active.get("nse_industry_source") == "manual").sum())),
        ("NSE industry still missing", int(active["nse_industry"].isna().sum())),
        ("NSE industry inferred (new additions)", int((active.get("nse_industry_source") == "inferred").sum())),
        ("Classification source", ", ".join(f"{k}={v}" for k, v in active.get("class_source", pd.Series(dtype=str)).value_counts().items())),
        ("Dropped (kept for history)", int((m.status == "dropped").sum())),
        ("Sector lists found", f"{len(sectors)}/{len(SECTOR_LISTS)}"),
        ("Stocks in >=1 sector index", int((active.sector_indices_count > 0).sum())),
        ("Stocks with basic industry", int(active["basic_industry"].notna().sum()) if "basic_industry" in active else 0),
        ("Distinct basic industries", int(active["basic_industry"].nunique()) if "basic_industry" in active else 0),
    ]
    ordered = {"Summary": pd.DataFrame(summary, columns=["item", "value"]), **REPORT}
    REPORT.clear()
    REPORT.update(ordered)
    if "basic_industry" in active:
        REPORT["Basic_industries"] = (active.groupby(["nse_industry", "basic_industry"]).size()
                                      .reset_index(name="stocks").sort_values(["nse_industry", "stocks"],
                                                                              ascending=[True, False]))
    REPORT["Log"] = pd.DataFrame(LOG)
    with pd.ExcelWriter("step3_report.xlsx") as xw:
        for k, v in REPORT.items():
            v.to_excel(xw, sheet_name=k[:31], index=False)
    print("\n" + REPORT["Summary"].to_string(index=False))
    print("\nDone. Files: data/universe/master.csv, data/universe/snapshots/, step3_report.xlsx")


if __name__ == "__main__":
    main()
