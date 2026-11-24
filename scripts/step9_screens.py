"""
Step 9b - Compute all screen metrics for the latest date -> data/screens/
  metrics_latest.parquet : one row per active stock (technical + fundamental + ownership + red flags)
  bench_returns.csv      : 1M/3M/6M/12M returns of the 3 benchmarks (same session counts as stocks)
  screens_report.xlsx    : how many stocks pass each screen with default thresholds, data gaps
Also repairs ownership data (step9_ownership.repair) before use.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_lib import metrics as M  # noqa: E402
from app_lib import screens as SC  # noqa: E402
from app_lib.prices import load_adjusted  # noqa: E402

OUT = Path("data/screens")
REPORT = "screens_report.xlsx"
BENCH = ["Nifty 50", "Nifty Smallcap 250", "Nifty Bank"]


def usdinr_series():
    try:
        import yfinance as yf
        h = yf.Ticker("INR=X").history(period="6y")["Close"]
        h.index = h.index.tz_localize(None).normalize()
        return h.sort_index()
    except Exception as e:
        print("USD/INR download failed:", e)
        return None


def bench_returns():
    files = sorted(Path("data/indices").glob("*.parquet"))
    ix = pd.concat([pd.read_parquet(f, columns=["date", "index", "close"]) for f in files])
    ix["index"] = ix["index"].str.strip()
    rows = []
    for b in BENCH:
        c = ix[ix["index"] == b].sort_values("date")["close"].reset_index(drop=True)
        row = {"benchmark": b}
        for k, d in M.TD.items():
            row[f"ret_{k}"] = (c.iloc[-1] / c.iloc[-1 - d] - 1) * 100 if len(c) > d else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    t0 = time.time()
    master = pd.read_csv("data/universe/master.csv")
    master = master[master["status"] == "active"]
    print(f"{len(master)} active stocks")

    px = load_adjusted(master["isin"], start=pd.Timestamp.today() - pd.Timedelta(days=800))
    tech = M.technical(px).reset_index()
    print(f"technical: {len(tech)} stocks ({time.time() - t0:.0f}s)")

    st = pd.read_parquet("data/fundamentals/yf_statements.parquet")
    info = pd.read_parquet("data/fundamentals/yf_info.parquet")
    last_close = dict(zip(master["symbol"], master["isin"].map(tech.set_index("isin")["close"])))
    fund = M.fundamentals(st, info, master, last_close, usdinr_series())
    print(f"fundamentals: {len(fund)} stocks; USD-converted: {fund.loc[fund.usd_converted, 'symbol'].tolist()}")

    own_file = Path("data/fundamentals/ownership.parquet")
    own = pd.DataFrame(columns=["symbol"])
    if own_file.exists():
        from step9_ownership import repair
        o = repair(pd.read_parquet(own_file))
        o.to_parquet(own_file, index=False)
        own = M.ownership(o)
    print(f"ownership: {len(own)} stocks")

    keep = ["isin", "symbol", "company", "tag", "nse_industry", "basic_industry", "instrument"]
    m = master[[c for c in keep if c in master]].merge(tech, on="isin", how="left")
    m = m.merge(fund, on="symbol", how="left").merge(own, on="symbol", how="left")
    m["is_financial"] = m["is_financial"].fillna(m["nse_industry"] == "Financial Services").astype(bool)
    m = SC.red_flags(m)
    OUT.mkdir(parents=True, exist_ok=True)
    m.to_parquet(OUT / "metrics_latest.parquet", index=False)
    br = bench_returns()
    br.to_csv(OUT / "bench_returns.csv", index=False)

    bench6 = br.loc[br.benchmark == "Nifty 50", "ret_6M"].iloc[0]
    res = SC.all_screens(m, bench6m=bench6)
    summary = [("stocks", len(m)), ("as of", str(m["last_date"].max())[:10]),
               ("stocks with any red flag", int((m["red_flag_count"] > 0).sum()))]
    summary += [(f"screen: {k}", len(v)) for k, v in res.items()]
    gaps = pd.DataFrame({"metric": m.columns, "missing": m.isna().sum().values,
                         "missing %": (m.isna().mean() * 100).round(1).values})
    flags = pd.Series({k: int(f(m).fillna(False).sum()) for k, f in SC.RED_FLAG_RULES.items()})
    with pd.ExcelWriter(REPORT) as xw:
        pd.DataFrame(summary, columns=["item", "value"]).to_excel(xw, sheet_name="Summary", index=False)
        flags.rename_axis("red flag").reset_index(name="stocks").to_excel(xw, sheet_name="Red_flags", index=False)
        for k, v in res.items():
            cols = [c for c in ["symbol", "company", "nse_industry", "tag", "close", "score", "red_flags"] if c in v]
            v.sort_values("score", ascending=False)[cols].head(50).to_excel(xw, sheet_name=k[:31], index=False)
        gaps.to_excel(xw, sheet_name="Data_gaps", index=False)
    print(pd.DataFrame(summary).to_string(index=False))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
