"""
Step 9a - Ownership history from NSE shareholding XBRL filings (decision U2)

For each active stock, the latest 8 quarterly shareholding filings (links come from
data/fundamentals/nse_shareholding.parquet) are downloaded from NSE's file server and parsed:
  promoter %, FII % (InstitutionsForeign), DII % (InstitutionsDomestic), MF %, insurance %,
  public %, small retail %, promoter pledge (% of total shares and % of promoter holding).
Output: data/fundamentals/ownership.parquet + ownership_report.xlsx
Resumable: filings already parsed are skipped. Stops after 15 failures in a row or --max-minutes.
"""
import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

MASTER = Path("data/universe/master.csv")
SH = Path("data/fundamentals/nse_shareholding.parquet")
OUT = Path("data/fundamentals/ownership.parquet")
REPORT = "ownership_report.xlsx"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
QUARTERS = 8
PCT_TAG = "ShareholdingAsAPercentageOfTotalNumberOfShares"
CATS = {   # our column -> XBRL context (category) name
    "promoter": "ShareholdingOfPromoterAndPromoterGroup",
    "fii": "InstitutionsForeign",
    "dii": "InstitutionsDomestic",
    "mf": "MutualFundsOrUTI",
    "insurance": "InsuranceCompanies",
    "public": "PublicShareholding",
    "retail_small": "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakh",
}
PROMOTER_CTX = "ShareholdingOfPromoterAndPromoterGroup_ContextI"


def values(txt, tag):
    """{context: value} for one XBRL element name (namespace prefix ignored)."""
    pat = re.compile(r"<(?:[\w\-]+:)?" + tag + r"\b[^>]*?contextRef=\"([^\"]+)\"[^>]*>([^<]*)</")
    return {ctx: v.strip() for ctx, v in pat.findall(txt)}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(txt):
    pct = values(txt, PCT_TAG)
    raw = {col: num(pct.get(f"{cat}_ContextI")) for col, cat in CATS.items()}
    # Some filings store 0.7359, others 73.59 (e.g. Jun-2025 quarter). Detect per filing.
    big = max([v for v in raw.values() if v is not None] or [0])
    scale = 1 if big > 1.5 else 100
    row = {col: (round(v * scale, 4) if v is not None else None) for col, v in raw.items()}
    # Companies with no promoter group (e.g. ETERNAL, IEX, BSE) have no promoter line
    if row["promoter"] is None and row["public"] is not None:
        row["promoter"] = 0.0
    pledged_n = num(values(txt, "NumberOfSharesEncumberedUnderPledged").get(PROMOTER_CTX))
    promo_n = num(values(txt, "NumberOfShares").get(PROMOTER_CTX))
    pledged_pct = num(values(txt, "EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares").get(PROMOTER_CTX))
    flag = values(txt, "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged")
    any_pledge = next(iter(flag.values()), "").lower() == "true" if flag else None
    # % of the PROMOTER's own holding that is pledged (the XBRL % field is also "of promoter holding")
    if pledged_n is not None and promo_n:
        row["pledge_pct_of_promoter"] = round(pledged_n / promo_n * 100, 4)
    elif pledged_pct is not None:
        row["pledge_pct_of_promoter"] = round(pledged_pct * (1 if pledged_pct > 1.5 else 100), 4)
    else:
        row["pledge_pct_of_promoter"] = 0.0 if any_pledge is False or row["promoter"] == 0 else None
    row["parsed_ok"] = row["promoter"] is not None and row["public"] is not None
    if all(v is None for v in raw.values()):
        row["error"] = "unsupported (older) XBRL format"
    return row


def repair(own):
    """Fix rows parsed by the first version: wrong x100 on percent-style filings, missing promoter
    for no-promoter companies, and the old pledge column."""
    if own.empty:
        return own
    cols = list(CATS)
    over = own[cols].max(axis=1) > 100.5
    own.loc[over, cols] = own.loc[over, cols] / 100
    nop = own["promoter"].isna() & own["public"].notna()
    own.loc[nop, "promoter"] = 0.0
    own.loc[nop & own["pledge_pct_of_promoter"].isna(), "pledge_pct_of_promoter"] = 0.0
    own["parsed_ok"] = own["promoter"].notna() & own["public"].notna()
    allnan = own[cols].isna().all(axis=1) & own["error"].isna() if "error" in own else own[cols].isna().all(axis=1)
    own.loc[allnan, "error"] = "unsupported (older) XBRL format"
    return own.drop(columns=[c for c in ["pledge_pct_of_total"] if c in own])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=280)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    m = pd.read_csv(MASTER)
    act = set(m.loc[m["status"] == "active", "symbol"])
    sh = pd.read_parquet(SH)
    sh = sh[sh["symbol"].isin(act) & sh["xbrl"].str.startswith("http", na=False)].copy()
    sh["quarter_end"] = pd.to_datetime(sh["date"], format="%d-%b-%Y", errors="coerce")
    sh["broadcast"] = pd.to_datetime(sh["broadcastDate"], format="%d-%b-%Y %H:%M:%S", errors="coerce")
    sh = sh.dropna(subset=["quarter_end"])
    # only regular quarter-end filings (event-based filings, e.g. after an allotment, are skipped)
    sh = sh[sh["quarter_end"].dt.is_month_end & sh["quarter_end"].dt.month.isin([3, 6, 9, 12])]
    sh = sh.sort_values("broadcast")
    sh = sh.drop_duplicates(["symbol", "quarter_end"], keep="last")          # revised filing wins
    sh = sh.sort_values("quarter_end").groupby("symbol").tail(QUARTERS)
    old = repair(pd.read_parquet(OUT)) if OUT.exists() else pd.DataFrame(columns=["xbrl"])
    if len(old):
        old.to_parquet(OUT, index=False)
    # parsed rows and older-format filings are not fetched again
    skip = (old["parsed_ok"] == True) | old.get("error", pd.Series("", index=old.index)).astype(str).str.startswith("unsupported") \
        if len(old) else pd.Series(dtype=bool)
    done = set(old.loc[skip, "xbrl"]) if len(old) else set()
    todo = sh[~sh["xbrl"].isin(done)]
    if a.limit:
        todo = todo[todo["symbol"].isin(sorted(todo["symbol"].unique())[: a.limit])]
    print(f"{len(sh)} filings wanted, {len(done)} already parsed, {len(todo)} to fetch", flush=True)

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.nseindia.com/"})
    rows, fails, stopped = [], 0, None
    for i, r in enumerate(todo.itertuples(), 1):
        if (time.time() - t0) / 60 > a.max_minutes:
            stopped = "time limit - run again to continue"
            break
        rec = {"symbol": r.symbol, "quarter_end": r.quarter_end, "xbrl": r.xbrl}
        try:
            resp = s.get(r.xbrl, timeout=40)
            time.sleep(0.6)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            rec.update(parse(resp.text))
            fails = 0
        except Exception as e:
            rec.update({"parsed_ok": False, "error": str(e)[:120]})
            fails += 1
            if fails >= 15:
                stopped = f"15 failures in a row at {i}/{len(todo)}"
                rows.append(rec)
                break
        rows.append(rec)
        if i % 200 == 0 or i == len(todo):
            old = save(old, rows)
            rows = []
            print(f"  {i}/{len(todo)} | {(time.time() - t0) / 60:.0f} min", flush=True)
    old = save(old, rows)
    report(old, stopped)


def save(old, rows):
    if not rows:
        return old
    new = pd.DataFrame(rows)
    out = pd.concat([old, new], ignore_index=True)
    out["ok_rank"] = out["parsed_ok"].fillna(False).astype(int)
    out = out.sort_values("ok_rank").drop_duplicates(["xbrl"], keep="last").drop(columns="ok_rank")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)
    return out


def report(own, stopped):
    ok = own[own["parsed_ok"] == True]
    per = ok.groupby("symbol")["quarter_end"].nunique()
    tot = ok["promoter"].fillna(0) + ok["public"].fillna(0)
    summary = [
        ("status", stopped or "complete"),
        ("filings parsed OK", len(ok)), ("filings failed", int((own["parsed_ok"] != True).sum())),
        ("  of which older XBRL format (Dec-2024, Mar-2025)", int(own.get("error", pd.Series(dtype=str)).astype(str).str.startswith("unsupported").sum())),
        ("stocks with >= 5 quarters", int((per >= 5).sum())), ("stocks with >= 6 quarters", int((per >= 6).sum())),
        ("latest quarter", str(ok["quarter_end"].max())[:10] if len(ok) else ""),
        ("filings where promoter + public is not ~100%", int(((tot - 100).abs() > 1.5).sum())),
        ("FII present", int(ok["fii"].notna().sum())), ("DII present", int(ok["dii"].notna().sum())),
        ("MF present", int(ok["mf"].notna().sum())),
        ("pledge known (incl. 'no pledge')", int(ok["pledge_pct_of_promoter"].notna().sum())),
        ("stocks with promoter pledge > 0 (latest)", int((ok.sort_values("quarter_end").groupby("symbol")
                                                          ["pledge_pct_of_promoter"].last() > 0).sum())),
    ]
    latest = ok.sort_values("quarter_end").groupby("symbol").tail(1)
    with pd.ExcelWriter(REPORT) as xw:
        pd.DataFrame(summary, columns=["item", "value"]).to_excel(xw, sheet_name="Summary", index=False)
        latest.sort_values("pledge_pct_of_promoter", ascending=False).to_excel(xw, sheet_name="Latest_quarter", index=False)
        own[own["parsed_ok"] != True].to_excel(xw, sheet_name="Failures", index=False)
    print(pd.DataFrame(summary).to_string(index=False))
    pd.DataFrame(summary, columns=["item", "value"]).to_csv(OUT.parent / "ownership_summary.csv", index=False)


if __name__ == "__main__":
    main()
