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
    df.columns = [c.replace("\ufeff", "").strip() for c in df.columns]
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
    up = {c.upper().replace(" ", "_"): c for c in df.columns}
    need = {"symbol": "SYMBOL", "series": "SERIES", "deliv_qty": "DELIV_QTY", "deliv_pct": "DELIV_PER"}
    missing = [v for v in need.values() if v not in up]
    if missing:
        raise RuntimeError(f"delivery file columns missing {missing}; got {list(df.columns)[:8]}")
    out = pd.DataFrame({k: df[up[v]] for k, v in need.items()})
    out["deliv_qty"], out["deliv_pct"] = num(out["deliv_qty"]), num(out["deliv_pct"])
    return out


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


def _current_month_start():
    return pd.Timestamp(date.today().replace(day=1))


def flush(price_buf, index_buf):
    """R4: days in the current month go to small daily files (YYYY-MM-DD.parquet) so the daily job never
    rewrites a big file; older days go straight into the monthly / yearly files."""
    cms = _current_month_start()
    for month, frames in price_buf.items():
        df = pd.concat(frames)
        old, new = df[df["date"] < cms], df[df["date"] >= cms]
        if len(old):
            append_parquet(PRICES / month[:4] / f"{month}.parquet", old, ["date", "isin", "series"])
        for d, g in new.groupby("date"):
            append_parquet(PRICES / f"{d:%Y}" / f"{d:%Y-%m-%d}.parquet", g, ["date", "isin", "series"])
    for year, frames in index_buf.items():
        df = pd.concat(frames)
        old, new = df[df["date"] < cms], df[df["date"] >= cms]
        if len(old):
            append_parquet(INDICES / f"{year}.parquet", old, ["date", "index"])
        for d, g in new.groupby("date"):
            append_parquet(INDICES / f"{d:%Y-%m-%d}.parquet", g, ["date", "index"])
    price_buf.clear()
    index_buf.clear()


def compact():
    """Once a month: fold last month's daily files into the monthly price file / yearly index file."""
    cms = f"{_current_month_start():%Y-%m}"
    moved = 0
    for f in sorted(PRICES.glob("*/????-??-??.parquet")):
        if f.stem[:7] < cms:
            append_parquet(PRICES / f.stem[:4] / f"{f.stem[:7]}.parquet", pd.read_parquet(f), ["date", "isin", "series"])
            f.unlink()
            moved += 1
    for f in sorted(INDICES.glob("????-??-??.parquet")):
        if f.stem[:7] < cms:
            append_parquet(INDICES / f"{f.stem[:4]}.parquet", pd.read_parquet(f), ["date", "index"])
            f.unlink()
            moved += 1
    if moved:
        print(f"compacted {moved} daily files into monthly/yearly files")
    return moved


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
    compact()
    log_df = read_log()
    done = set(log_df.loc[log_df["status"].isin(["ok", "holiday", "no_session"]), "date"])
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    # G2: weekends too - NSE holds special sessions (Muhurat, Budget day, DR-test Saturdays)
    todo = [d for d in days if d.isoformat() not in done]
    print(f"{len(todo)} days to check between {start} and {end} ({len(done)} already done)")
    price_buf, index_buf, new_log = {}, {}, []
    fails_in_row, stopped = 0, None

    for i, d in enumerate(todo, 1):
        if (time.time() - t0) / 60 > max_minutes:
            stopped = f"time limit reached at {d} - run again to continue"
            break
        row = {"date": d.isoformat(), "stocks": 0, "deliv_matched": 0, "indices": 0, "note": "",
               "loaded_at": datetime.now().isoformat(timespec="seconds")}
        got = {}
        if d.weekday() >= 5:  # weekend: only continue if NSE published an index file that day
            try:
                got["idx"] = indices(d)
            except NotFound:
                row["status"] = "no_session"
                new_log.append(row)
                continue
            except Exception as e:
                row["status"], row["note"] = "partial", f"idx: {str(e)[:120]}"
                new_log.append(row)
                continue
        for name, fn in (("idx", indices), ("px", stock_prices), ("dl", delivery)):
            if name in got:
                continue
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
    global NEW_DAYS
    NEW_DAYS = int(log_df[log_df["loaded_at"] >= datetime.fromtimestamp(t0).isoformat(timespec="seconds")]
                   ["status"].astype(str).str.startswith("ok").sum())
    return stopped


NEW_DAYS = 0


# ------------------------------------------------------------------ corporate actions (F3)
def load_prices(cols=None):
    files = sorted(PRICES.glob("*/*.parquet"))
    return pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True) if files else pd.DataFrame()


def isin_links(px):
    """A face-value split gives a stock a NEW ISIN while the symbol continues. Link old -> latest ISIN
    when the same symbol ends on one ISIN and starts on another within 10 days. Saved to meta."""
    span = px.groupby(["symbol", "isin"])["date"].agg(["min", "max"]).reset_index()
    links = []
    for sym, g in span.sort_values("min").groupby("symbol"):
        rows = g.to_dict("records")
        for a, b in zip(rows, rows[1:]):
            if a["isin"] != b["isin"] and 0 < (b["min"] - a["max"]).days <= 10:
                links.append({"old_isin": a["isin"], "new_isin": b["isin"], "symbol": sym,
                              "old_last_date": a["max"].date(), "new_first_date": b["min"].date()})
    lk = pd.DataFrame(links, columns=["old_isin", "new_isin", "symbol", "old_last_date", "new_first_date"])
    META.mkdir(parents=True, exist_ok=True)
    lk.to_csv(META / "isin_links.csv", index=False)
    step = dict(zip(lk["old_isin"], lk["new_isin"]))

    def final(i):
        seen = set()
        while i in step and i not in seen:
            seen.add(i)
            i = step[i]
        return i
    return {i: final(i) for i in step}


def _snap(f, tol=0.01, max_den=10):
    """Snap a factor to a clean ratio (2, 10, 1.5, 4/3 ...) if within tol."""
    from fractions import Fraction
    fr = Fraction(f).limit_denominator(max_den)
    return float(fr) if abs(float(fr) / f - 1) < tol else round(f, 4)


def yf_closes(symbols, start):
    """yfinance daily Close (auto_adjust=False -> split/bonus adjusted, NOT dividend adjusted)."""
    import yfinance as yf
    out, missing = {}, []
    syms = list(symbols)
    for i in range(0, len(syms), 50):
        chunk = syms[i:i + 50]
        try:
            data = yf.download([f"{x}.NS" for x in chunk], start=start, auto_adjust=False,
                               group_by="ticker", threads=True, progress=False)
        except Exception:
            data = None
        for x in chunk:
            try:
                c = data[f"{x}.NS"]["Close"].dropna()
                if len(c) < 5:
                    raise KeyError
                c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
                out[x] = c
            except Exception:
                missing.append(x)
        print(f"  yfinance {min(i + 50, len(syms))}/{len(syms)}")
        time.sleep(2)
    # second pass, one by one, for anything the bulk call missed
    fails = 0
    for x in list(missing):
        if fails >= 10:
            break
        try:
            c = yf.Ticker(f"{x}.NS").history(start=start, auto_adjust=False)["Close"].dropna()
            time.sleep(0.5)
            if len(c) < 5:
                raise KeyError
            c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
            out[x] = c
            missing.remove(x)
            fails = 0
        except Exception:
            fails += 1
    return out, missing


def detect_actions():
    """G1 + H1-H4. Universe stocks only.
    G1  ratio = NSE raw close / yfinance adjusted close; a lasting step = split/bonus,
        factor = median ratio 5 days before / 5 days after (snapped to clean ratios).
    H1  ignore a yfinance step if applying it would make NSE's own open gap bigger (yfinance glitch).
    H2  confirm a split with the ex-date OPEN: adjusted open within +/-20% of last close.
    H3  NSE open gaps down >= 25% with no yfinance step = structural event (demerger, or split/bonus
        missing in yfinance): factor = last close / ex-date open (snapped if close to a clean ratio).
    H4  NEWLIST stocks: history starts on the NSE listing date.
    Other close-to-close moves beyond +/-35% are recorded as 'noted' (genuine moves, no adjustment)."""
    if not MASTER.exists():
        print("No universe file - skipping corporate actions")
        return pd.DataFrame()
    m = pd.read_csv(MASTER)
    m = m[m["status"] == "active"]
    px = load_prices(["date", "isin", "symbol", "series", "open", "close"])
    link = isin_links(px)
    px["isin"] = px["isin"].map(lambda i: link.get(i, i))   # pre-split history joins the current ISIN
    px = px[px["isin"].isin(m["isin"])]
    px = px.sort_values(["isin", "date", "series"]).drop_duplicates(["isin", "date"], keep="first")

    # H4: NEWLIST history starts at NSE listing date
    starts = {}
    if "listing_date" in m:
        nl = m[(m["tag"] == "NEWLIST") & m["listing_date"].notna()]
        starts = dict(zip(nl["isin"], pd.to_datetime(nl["listing_date"])))
        pd.DataFrame({"isin": list(starts), "history_start": [d.date() for d in starts.values()]}) \
            .to_csv(META / "history_start.csv", index=False)
    if starts:
        px = px[[d >= starts.get(i, pd.Timestamp.min) for i, d in zip(px["isin"], px["date"])]]

    yfc, missing = yf_closes(m["symbol"], start=str(DEFAULT_START - timedelta(days=10)))
    print(f"yfinance prices for {len(yfc)}/{len(m)} stocks; missing {len(missing)}")

    events = []

    def add(**kw):
        events.append(kw)

    for isin, sym in zip(m["isin"], m["symbol"]):
        g = px[px["isin"] == isin].set_index("date")
        if len(g) < 3:
            continue
        close, opn = g["close"], g["open"]
        last = close.shift(1)
        gap_open = opn / last            # NSE's own overnight gap at the open
        handled = set()
        if sym in yfc:
            j = pd.concat([close.rename("raw"), yfc[sym].rename("yf")], axis=1, join="inner").dropna()
            j = j[j["yf"] > 0]
            ratio = j["raw"] / j["yf"]
            step = ratio.shift(1) / ratio
            for d in step[(step - 1).abs() > 0.08].index:
                pos = ratio.index.get_loc(d)
                f = ratio.iloc[max(0, pos - 5):pos].median() / ratio.iloc[pos:pos + 5].median()
                if abs(f - 1) < 0.08:
                    continue  # one-day blip, not a lasting step
                f = _snap(f)
                prev_d = close.index[close.index.get_loc(d) - 1]
                go = gap_open.get(d)
                base = dict(isin=isin, symbol=sym, ex_date=d, last_date=prev_d, last_close=close[prev_d],
                            ex_open=opn.get(d), ex_close=close.get(d), factor=f, source="yfinance step")
                # H1: a real event's factor makes NSE's own open gap SMALLER; a glitch makes it bigger
                if go is not None and not pd.isna(go) and abs(go * f - 1) >= abs(go - 1):
                    add(**base, adj_move_pct=round((go * f - 1) * 100, 1), status="ignored",
                        event_type="yfinance glitch", note="adjusting would make NSE's own gap worse")
                    continue
                adj_open = opn[d] * f / close[prev_d] - 1                         # H2
                ok = -0.20 < adj_open < 0.20
                add(**base, adj_move_pct=round(adj_open * 100, 1), status="applied" if ok else "review",
                    event_type="split/bonus", note="" if ok else "adjusted open still beyond +/-20%")
                handled.add(d)
        # H3: big gap at the open with no yfinance step
        for d, go in gap_open[gap_open <= 0.75].items():   # down-gaps only: demergers/splits never gap up
            if d in handled or pd.isna(go):
                continue
            prev_d = close.index[close.index.get_loc(d) - 1]
            f = _snap(close[prev_d] / opn[d], tol=0.02, max_den=2)
            add(isin=isin, symbol=sym, ex_date=d, last_date=prev_d, last_close=close[prev_d],
                ex_open=opn[d], ex_close=close[d], factor=f, source="NSE open gap",
                adj_move_pct=round((close[d] * f / close[prev_d] - 1) * 100, 1), status="applied",
                event_type="structural (demerger / split missing in yfinance)",
                note="check: set manual_ignore if this was a genuine crash")
            handled.add(d)
        # remaining big close-to-close moves: genuine, recorded only
        for d, r in (close / last)[((close / last) - 1).abs() > 0.35].items():
            if d in handled:
                continue
            add(isin=isin, symbol=sym, ex_date=d, last_date=None, last_close=last.get(d), ex_open=opn.get(d),
                ex_close=close[d], factor=None, source="price move", adj_move_pct=round((r - 1) * 100, 1),
                status="noted", event_type="genuine move", note="opened within 25% - no adjustment")
    ev = pd.DataFrame(events)
    if not ev.empty:
        ev = ev.drop_duplicates(["isin", "ex_date", "status"]).sort_values(["ex_date", "symbol"])
        ev["current_symbol"] = ev["symbol"]
    # keep any manual decisions (status manual_apply / manual_ignore) from earlier runs
    if ACTIONS.exists():
        old = pd.read_csv(ACTIONS, parse_dates=["ex_date"])
        if "event_type" in old:  # only files from this method
            man = old[old["status"].astype(str).str.startswith("manual")]
            if len(man):
                key = set(zip(man["isin"], man["ex_date"]))
                ev = pd.concat([ev[[(i, d) not in key for i, d in zip(ev["isin"], ev["ex_date"])]], man])
    ev["method"] = "v3_H1-H4"
    ACTIONS.parent.mkdir(parents=True, exist_ok=True)
    ev.to_csv(ACTIONS, index=False)
    pd.DataFrame({"symbol_without_yfinance_data": missing}).to_csv(META / "yfinance_missing.csv", index=False)
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
        log_df[~log_df.status.isin(["ok", "holiday", "no_session"])],
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
            link_file = META / "isin_links.csv"
            if link_file.exists():
                lk = pd.read_csv(link_file)
                step = dict(zip(lk["old_isin"], lk["new_isin"]))
                for _ in range(5):
                    px["isin"] = px["isin"].map(lambda i: step.get(i, i))
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
    if (META / "isin_links.csv").exists():
        checks["ISIN changes linked (face-value splits)"] = len(pd.read_csv(META / "isin_links.csv"))

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
        if NEW_DAYS == 0 and ACTIONS.exists():
            print("no new trading days - split/bonus check skipped")
            return
    actions = detect_actions()
    report(stopped, actions)


if __name__ == "__main__":
    main()
