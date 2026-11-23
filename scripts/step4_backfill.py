"""
Step 4 - Load 5 years of NSE daily data into the repo (Indian Stock Market Dashboard)

Decisions (PROJECT.md, Step 4): F1 store every NSE main-board stock · F2 two stock files per day
(bhavcopy for ISIN + prices, full bhavdata for delivery) + all-indices file · F3 detect splits/bonuses
from NSE's own adjusted previous close, cross-checked with yfinance · F4 runs on GitHub, data in repo.

Resumable: days already loaded are skipped, so just run it again if it stops.

Usage (from the repo root):
    python scripts/step4_backfill.py                          # 2021-11-01 -> yesterday
    python scripts/step4_backfill.py --start 2024-01-01 --end 2024-03-31
    python scripts/step4_backfill.py --actions-only           # only re-run split/bonus detection
"""
import argparse
import io
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ------------------------------------------------------------------ settings
DATA = Path("data")
PRICES = DATA / "prices" / "raw"
INDICES = DATA / "indices"
META = DATA / "meta"
LOAD_LOG = META / "load_log.csv"
ACTIONS = DATA / "prices" / "corporate_actions.csv"
MASTER = DATA / "universe" / "master.csv"
REPORT_FILE = "step4_report.xlsx"

DEFAULT_START = date(2021, 11, 1)
UDIFF_FROM = date(2024, 7, 8)          # NSE switched bhavcopy format on this date
KEEP_SERIES = {"EQ", "BE", "BZ", "RR", "IV"}  # main-board equity + REITs (RR) + InvITs (IV)
ACTION_THRESHOLD = 0.02                # prev_close vs last close gap that counts as a corporate action
YF_MATCH_TOL = 0.03                    # yfinance must agree within 3% to auto-apply

ARCH = "https://nsearchives.nseindia.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/",
}
PAUSE = 0.8
S = requests.Session()
S.headers.update(HEADERS)


class NotFound(Exception):
    pass


def fetch(url, tries=3):
    """Returns bytes; raises NotFound on 404 (holiday / no file), RuntimeError on other failures."""
    last = None
    for a in range(tries):
        try:
            r = S.get(url, timeout=40)
            time.sleep(PAUSE)
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code == 404:
                raise NotFound(url)
            last = f"HTTP {r.status_code}"
        except NotFound:
            raise
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(3 * (a + 1))
    raise RuntimeError(f"{url} -> {last}")


def read_csv(content):
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            content = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(content), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df


def num(s):
    return pd.to_numeric(s.replace({"-": None, "": None}), errors="coerce")


# ------------------------------------------------------------------ parsers -> one standard shape
def stock_prices(d):
    """Bhavcopy for date d -> date, isin, symbol, series, open, high, low, close, last, prev_close,
    volume, value, trades."""
    if d >= UDIFF_FROM:
        df = read_csv(fetch(f"{ARCH}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"))
        if "FinInstrmTp" in df:
            df = df[df["FinInstrmTp"].isin(["STK", ""]) | df["FinInstrmTp"].isna()]
        out = pd.DataFrame({
            "isin": df["ISIN"], "symbol": df["TckrSymb"], "series": df["SctySrs"],
            "open": num(df["OpnPric"]), "high": num(df["HghPric"]), "low": num(df["LwPric"]),
            "close": num(df["ClsPric"]), "last": num(df["LastPric"]), "prev_close": num(df["PrvsClsgPric"]),
            "volume": num(df["TtlTradgVol"]), "value": num(df["TtlTrfVal"]), "trades": num(df["TtlNbOfTxsExctd"]),
        })
    else:
        mon = d.strftime("%b").upper()
        df = read_csv(fetch(f"{ARCH}/content/historical/EQUITIES/{d.year}/{mon}/cm{d:%d}{mon}{d.year}bhav.csv.zip"))
        out = pd.DataFrame({
            "isin": df["ISIN"], "symbol": df["SYMBOL"], "series": df["SERIES"],
            "open": num(df["OPEN"]), "high": num(df["HIGH"]), "low": num(df["LOW"]),
            "close": num(df["CLOSE"]), "last": num(df["LAST"]), "prev_close": num(df["PREVCLOSE"]),
            "volume": num(df["TOTTRDQTY"]), "value": num(df["TOTTRDVAL"]), "trades": num(df["TOTALTRADES"]),
        })
    out = out[out["series"].isin(KEEP_SERIES)].copy()
    out.insert(0, "date", pd.Timestamp(d))
    return out


def delivery(d):
    df = read_csv(fetch(f"{ARCH}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"))
    return pd.DataFrame({"symbol": df["SYMBOL"], "series": df["SERIES"],
                         "deliv_qty": num(df["DELIV_QTY"]), "deliv_pct": num(df["DELIV_PER"])})


def indices(d):
    df = read_csv(fetch(f"{ARCH}/content/indices/ind_close_all_{d:%d%m%Y}.csv"))
    low = {c.lower(): c for c in df.columns}

    def col(*keys):
        for k in keys:
            for lc, c in low.items():
                if k in lc:
                    return c
        return None
    out = pd.DataFrame({"index": df[col("index name")]})
    for name, keys in {"open": ("open",), "high": ("high",), "low": ("low",), "close": ("closing",),
                       "volume": ("volume",), "turnover_cr": ("turnover",), "pe": ("p/e",),
                       "pb": ("p/b",), "div_yield": ("div yield",)}.items():
        c = col(*keys)
        out[name] = num(df[c]) if c else None
    out.insert(0, "date", pd.Timestamp(d))
    return out


# ------------------------------------------------------------------ storage
def append_parquet(path, new, keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        new = pd.concat([pd.read_parquet(path), new], ignore_index=True)
    new = new.drop_duplicates(subset=keys, keep="last").sort_values(keys).reset_index(drop=True)
    new.to_parquet(path, index=False)


def flush(price_buf, index_buf):
    for month, frames in price_buf.items():
        append_parquet(PRICES / month[:4] / f"{month}.parquet", pd.concat(frames), ["date", "isin", "series"])
    for year, frames in index_buf.items():
        append_parquet(INDICES / f"{year}.parquet", pd.concat(frames), ["date", "index"])
    price_buf.clear()
    index_buf.clear()


def read_log():
    if LOAD_LOG.exists():
        return pd.read_csv(LOAD_LOG, dtype={"date": str, "note": str}).fillna({"note": ""})
    return pd.DataFrame(columns=["date", "status", "stocks", "deliv_matched", "indices", "note", "loaded_at"])


def write_log(log_df):
    META.mkdir(parents=True, exist_ok=True)
    log_df.drop_duplicates("date", keep="last").sort_values("date").to_csv(LOAD_LOG, index=False)


# ------------------------------------------------------------------ backfill
def backfill(start, end, max_minutes):
    t0 = time.time()
    log_df = read_log()
    done = set(log_df.loc[log_df["status"].isin(["ok", "holiday"]), "date"])
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    todo = [d for d in days if d.weekday() < 5 and d.isoformat() not in done]
    print(f"{len(todo)} weekdays to load between {start} and {end} ({len(done)} already done)")
    price_buf, index_buf, new_log = {}, {}, []
    fails_in_row, stopped = 0, None

    for i, d in enumerate(todo, 1):
        if (time.time() - t0) / 60 > max_minutes:
            stopped = f"time limit reached at {d} - run again to continue"
            break
        row = {"date": d.isoformat(), "stocks": 0, "deliv_matched": 0, "indices": 0, "note": "",
               "loaded_at": datetime.now().isoformat(timespec="seconds")}
        got = {}
        for name, fn in (("idx", indices), ("px", stock_prices), ("dl", delivery)):
            try:
                got[name] = fn(d)
            except NotFound:
                got[name] = "404"
            except Exception as e:
                got[name] = None
                row["note"] += f"{name}: {str(e)[:120]}; "

        if all(isinstance(got.get(k), str) for k in ("idx", "px")):
            row["status"] = "holiday"
        elif isinstance(got.get("px"), pd.DataFrame) and isinstance(got.get("idx"), pd.DataFrame):
            px = got["px"]
            if isinstance(got.get("dl"), pd.DataFrame):
                px = px.merge(got["dl"], on=["symbol", "series"], how="left")
                row["deliv_matched"] = int(px["deliv_qty"].notna().sum())
            else:
                px["deliv_qty"], px["deliv_pct"] = None, None
                row["note"] += "no delivery file; "
            price_buf.setdefault(d.strftime("%Y-%m"), []).append(px)
            index_buf.setdefault(str(d.year), []).append(got["idx"])
            row["stocks"], row["indices"] = len(px), len(got["idx"])
            row["status"] = "ok" if isinstance(got.get("dl"), pd.DataFrame) else "ok_no_delivery"
        else:
            row["status"] = "partial"  # retried on next run

        new_log.append(row)
        fails_in_row = fails_in_row + 1 if row["status"] == "partial" else 0
        if fails_in_row >= 10:
            stopped = f"10 failed days in a row at {d} - NSE may be refusing requests; stopped"
            break
        if i % 20 == 0 or i == len(todo):
            flush(price_buf, index_buf)
            log_df = pd.concat([log_df, pd.DataFrame(new_log)], ignore_index=True)
            new_log = []
            write_log(log_df)
            print(f"  {i}/{len(todo)} days | {d} | {(time.time() - t0) / 60:.0f} min")

    flush(price_buf, index_buf)
    if new_log:
        log_df = pd.concat([log_df, pd.DataFrame(new_log)], ignore_index=True)
    write_log(log_df)
    if stopped:
        print("STOPPED:", stopped)
    return stopped


# ------------------------------------------------------------------ corporate actions (F3)
def load_prices(cols=None):
    files = sorted(PRICES.glob("*/*.parquet"))
    return pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True) if files else pd.DataFrame()


def detect_actions():
    px = load_prices(["date", "isin", "symbol", "series", "close", "prev_close"])
    if px.empty:
        return pd.DataFrame()
    px = px.sort_values(["isin", "date", "series"]).drop_duplicates(["isin", "date"], keep="first")
    px["last_close"] = px.groupby("isin")["close"].shift(1)
    px["last_date"] = px.groupby("isin")["date"].shift(1)
    px["factor"] = px["last_close"] / px["prev_close"]
    ev = px[(px["factor"] - 1).abs() > ACTION_THRESHOLD].dropna(subset=["factor"]).copy()
    latest_sym = px.sort_values("date").groupby("isin")["symbol"].last()
    ev["current_symbol"] = ev["isin"].map(latest_sym)
    ev = ev.rename(columns={"date": "ex_date", "close": "ex_close"})[
        ["isin", "symbol", "current_symbol", "ex_date", "last_date", "last_close", "prev_close", "ex_close", "factor"]]
    ev["factor"] = ev["factor"].round(4)

    # keep earlier decisions (and any manual edits) for events already reviewed
    old = pd.read_csv(ACTIONS, parse_dates=["ex_date"]) if ACTIONS.exists() else pd.DataFrame()
    if not old.empty:
        key_old = set(zip(old["isin"], old["ex_date"]))
        ev = ev[[(i, d) not in key_old for i, d in zip(ev["isin"], ev["ex_date"])]]
    print(f"{len(ev)} new candidate corporate actions; checking with yfinance")
    ev = verify_with_yfinance(ev)
    allev = pd.concat([old, ev], ignore_index=True).sort_values(["ex_date", "isin"])
    ACTIONS.parent.mkdir(parents=True, exist_ok=True)
    allev.to_csv(ACTIONS, index=False)
    return allev


def verify_with_yfinance(ev):
    """yfinance 'Close' (auto_adjust=False) is split/bonus-adjusted but not dividend-adjusted.
    The jump in raw/yf ratio across the ex-date should equal our factor."""
    import yfinance as yf
    ev = ev.copy()
    ev["yf_factor"], ev["status"], ev["note"] = None, "review", ""
    fails = 0
    for idx, r in ev.iterrows():
        if fails >= 10:
            ev.at[idx, "note"] = "yfinance unavailable (10 failures in a row)"
            continue
        try:
            h = yf.Ticker(f"{r['current_symbol']}.NS").history(
                start=r["last_date"] - timedelta(days=3), end=r["ex_date"] + timedelta(days=3),
                auto_adjust=False)
            time.sleep(0.4)
            if h.empty:
                raise RuntimeError("no yfinance data")
            h.index = h.index.tz_localize(None).normalize()
            yb, ya = h["Close"].get(r["last_date"]), h["Close"].get(r["ex_date"])
            if yb is None or ya is None or pd.isna(yb) or pd.isna(ya):
                raise RuntimeError("dates missing in yfinance")
            # raw/yf ratio before the ex-date includes this split; after it doesn't -> their ratio = factor
            yf_factor = (r["last_close"] / yb) / (r["ex_close"] / ya)
            ev.at[idx, "yf_factor"] = round(float(yf_factor), 4)
            fails = 0
            if abs(yf_factor / r["factor"] - 1) < YF_MATCH_TOL:
                ev.at[idx, "status"] = "applied"
            elif abs(yf_factor - 1) < YF_MATCH_TOL:
                ev.at[idx, "status"] = "ignored"
                ev.at[idx, "note"] = "yfinance shows no split/bonus (likely dividend, rights or data quirk)"
            else:
                ev.at[idx, "note"] = "yfinance factor differs - review"
        except Exception as e:
            fails += 1
            ev.at[idx, "note"] = str(e)[:120]
    return ev


# ------------------------------------------------------------------ report
def report(stopped, actions):
    sheets = {}
    log_df = read_log()
    log_df["year"] = log_df["date"].str[:4]
    per_year = log_df.pivot_table(index="year", columns="status", values="date", aggfunc="count", fill_value=0)
    per_year["avg_stocks_per_day"] = log_df[log_df.status.str.startswith("ok")].groupby("year")["stocks"].mean().round(0)
    sheets["Per_year"] = per_year.reset_index()

    ok = log_df[log_df.status.str.startswith("ok")].copy()
    med = ok["stocks"].median() if len(ok) else 0
    problems = pd.concat([
        log_df[~log_df.status.isin(["ok", "holiday"])],
        ok[ok["stocks"] < 0.8 * med].assign(note=lambda x: x["note"] + "stock count unusually low"),
    ])
    sheets["Problem_days"] = problems

    px = load_prices(["date", "isin", "symbol", "series", "close", "high", "low", "deliv_pct"])
    checks = {}
    if not px.empty:
        checks = {
            "price rows": len(px),
            "distinct ISINs": px["isin"].nunique(),
            "first date": str(px["date"].min().date()), "last date": str(px["date"].max().date()),
            "duplicate isin+date+series": int(px.duplicated(["date", "isin", "series"]).sum()),
            "close <= 0": int((px["close"] <= 0).sum()),
            "high < low": int((px["high"] < px["low"]).sum()),
            "delivery % outside 0-100": int(((px["deliv_pct"] < 0) | (px["deliv_pct"] > 100)).sum()),
        }
        if MASTER.exists():
            m = pd.read_csv(MASTER)
            m = m[m["status"] == "active"]
            g = px.groupby("isin").agg(first_date=("date", "min"), last_date=("date", "max"), days=("date", "nunique"))
            cov = m[["isin", "symbol", "company", "tag"]].merge(g, left_on="isin", right_index=True, how="left")
            sheets["Universe_coverage"] = cov.sort_values("first_date", ascending=False)
            checks["universe stocks with no price data"] = int(cov["first_date"].isna().sum())
            checks["universe stocks with full history (from start)"] = int(
                (cov["first_date"] <= pd.Timestamp(DEFAULT_START) + timedelta(days=10)).sum())

    idx_files = sorted(INDICES.glob("*.parquet"))
    if idx_files:
        ix = pd.concat([pd.read_parquet(f, columns=["date", "index", "close"]) for f in idx_files])
        icov = ix.groupby("index").agg(first_date=("date", "min"), last_date=("date", "max"),
                                       days=("date", "nunique")).reset_index().sort_values("first_date")
        icov.to_csv(META / "index_coverage.csv", index=False)
        sheets["Index_coverage"] = icov

    if actions is not None and len(actions):
        sheets["Corporate_actions"] = actions
        # known events: does detection find them? (TATASTEEL 10:1 split, BAJFINANCE 2:1 split + 4:1 bonus)
        known = [("TATASTEEL", "2022-07-28", 10.0), ("BAJFINANCE", "2025-06-16", 10.0)]
        kc = []
        for sym, d, f in known:
            hit = actions[(actions["current_symbol"] == sym) &
                          ((pd.to_datetime(actions["ex_date"]) - pd.Timestamp(d)).abs() <= pd.Timedelta(days=7))]
            kc.append({"symbol": sym, "expected_ex_date": d, "expected_factor": f,
                       "found_factor": hit["factor"].iloc[0] if len(hit) else None,
                       "status": hit["status"].iloc[0] if len(hit) else "NOT DETECTED"})
        sheets["Known_event_check"] = pd.DataFrame(kc)
        checks.update({f"actions {k}": int(v) for k, v in actions["status"].value_counts().items()})

    size_mb = sum(f.stat().st_size for f in DATA.rglob("*.parquet")) / 1e6
    summary = [("status", stopped or "complete"), ("storage (parquet) MB", round(size_mb, 1)),
               *checks.items()]
    with pd.ExcelWriter(REPORT_FILE) as xw:
        pd.DataFrame(summary, columns=["item", "value"]).to_excel(xw, sheet_name="Summary", index=False)
        for k, v in sheets.items():
            v.to_excel(xw, sheet_name=k[:31], index=False)
    print(pd.DataFrame(summary, columns=["item", "value"]).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START.isoformat())
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    ap.add_argument("--max-minutes", type=float, default=320)
    ap.add_argument("--actions-only", action="store_true")
    a = ap.parse_args()
    stopped = None
    if not a.actions_only:
        stopped = backfill(date.fromisoformat(a.start), date.fromisoformat(a.end), a.max_minutes)
    actions = detect_actions()
    report(stopped, actions)


if __name__ == "__main__":
    main()
