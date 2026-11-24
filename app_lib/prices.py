"""
Adjusted daily prices for the universe (Step 4 rules).

- Raw NSE rows from data/prices/raw/YYYY/YYYY-MM.parquet
- Older ISINs joined to the current ISIN (data/meta/isin_links.csv)
- NEWLIST history starts at NSE listing date (data/meta/history_start.csv)
- Splits / bonuses / demergers: every event with status applied or manual_apply in
  data/prices/corporate_actions.csv divides prices BEFORE its ex-date by its factor
  (volume is multiplied), so the series is continuous.
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data")
COLS = ["date", "isin", "symbol", "series", "open", "high", "low", "close", "volume", "value", "deliv_pct"]


def _links():
    f = DATA / "meta" / "isin_links.csv"
    if not f.exists():
        return {}
    lk = pd.read_csv(f)
    step = dict(zip(lk["old_isin"], lk["new_isin"]))

    def final(i):
        seen = set()
        while i in step and i not in seen:
            seen.add(i)
            i = step[i]
        return i
    return {i: final(i) for i in step}


def load_adjusted(isins=None, start=None):
    files = sorted((DATA / "prices" / "raw").glob("*/*.parquet"))
    if start is not None:
        files = [f for f in files if f.stem >= pd.Timestamp(start).strftime("%Y-%m")]
    px = pd.concat([pd.read_parquet(f, columns=COLS) for f in files], ignore_index=True)
    link = _links()
    px["isin"] = px["isin"].map(lambda i: link.get(i, i))
    if isins is not None:
        px = px[px["isin"].isin(set(isins))]
    # one row per stock per day (EQ preferred over BE/BZ when both exist)
    px["s_rank"] = px["series"].map({"EQ": 0, "BE": 1, "BZ": 2}).fillna(3)
    px = px.sort_values(["isin", "date", "s_rank"]).drop_duplicates(["isin", "date"]).drop(columns="s_rank")

    hs = DATA / "meta" / "history_start.csv"
    if hs.exists():
        h = pd.read_csv(hs, parse_dates=["history_start"])
        start_map = dict(zip(h["isin"], h["history_start"]))
        keep = [d >= start_map.get(i, pd.Timestamp.min) for i, d in zip(px["isin"], px["date"])]
        px = px[keep]

    px["adj"] = 1.0
    ca_file = DATA / "prices" / "corporate_actions.csv"
    if ca_file.exists():
        ca = pd.read_csv(ca_file, parse_dates=["ex_date"])
        ca = ca[ca["status"].isin(["applied", "manual_apply"]) & ca["factor"].notna()]
        ca = ca[ca["isin"].isin(px["isin"].unique())]
        for isin, g in ca.groupby("isin"):
            m = px["isin"] == isin
            d = px.loc[m, "date"]
            f = np.ones(len(d))
            for _, ev in g.iterrows():
                f = f * np.where(d.values < np.datetime64(ev["ex_date"]), float(ev["factor"]), 1.0)
            px.loc[m, "adj"] = f
    px["raw_close"] = px["close"]
    for c in ("open", "high", "low", "close"):
        px[c] = px[c] / px["adj"]
    px["volume"] = px["volume"] * px["adj"]
    return px.drop(columns="adj").reset_index(drop=True)
