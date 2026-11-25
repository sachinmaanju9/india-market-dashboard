"""
Step 13 / I4 - Intraday feasibility test (run during market hours, 9:15-15:30 IST).
1. Which Yahoo symbols work for each NSE index  -> data/meta/yahoo_index_map.csv (used by the app)
2. Delay of the latest 5-min and 1-min bars for indices and stocks
3. Time and coverage to fetch 5-min bars for all active stocks
Results -> data/meta/intraday_test.csv (one row per run) + intraday_test_report.xlsx
"""
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_lib import intraday as I  # noqa: E402


def main():
    now = I.now_ist()
    print(f"run at {now:%Y-%m-%d %H:%M} IST, market {I.market_status(now)}")
    rows = []
    for name, cands in I.INDEX_CANDIDATES.items():
        hit = None
        for c in cands:
            got = I.fetch([c])
            if c in got:
                b = got[c]
                hit = {"index": name, "yahoo": c, "bars_today": len(b), "last_bar": b.index[-1],
                       "delay_min": I.delay_minutes(b.index[-1], 5, now)}
                break
            time.sleep(0.3)
        rows.append(hit or {"index": name, "yahoo": None, "tried": ", ".join(cands)})
    imap = pd.DataFrame(rows)
    I.MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    imap.to_csv(I.MAP_FILE, index=False)
    print(imap.to_string())

    m = pd.read_csv("data/universe/master.csv")
    syms = m.loc[m["status"] == "active", "symbol"].tolist()
    t0 = time.time()
    bars = I.fetch([f"{s}.NS" for s in syms], interval="5m", period="1d")
    secs = round(time.time() - t0, 1)
    last = pd.Series({k: v.index[-1] for k, v in bars.items()})
    d5 = [I.delay_minutes(t, 5, now) for t in last] if len(last) else []
    one = I.fetch([f"{s}.NS" for s in ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC"]], interval="1m", period="1d")
    d1 = [I.delay_minutes(v.index[-1], 1, now) for v in one.values()]
    med = lambda x: round(pd.Series([v for v in x if v is not None]).median(), 1) if any(v is not None for v in x) else None  # noqa
    summ = {"run_at": f"{now:%Y-%m-%d %H:%M}", "market": I.market_status(now),
            "indices_found": int(imap["yahoo"].notna().sum()), "indices_total": len(imap),
            "stocks_requested": len(syms), "stocks_with_bars": len(bars), "fetch_seconds": secs,
            "median_delay_5m_stocks_min": med(d5), "median_delay_1m_stocks_min": med(d1),
            "median_delay_5m_indices_min": med(imap.get("delay_min", pd.Series(dtype=float)).tolist())}
    f = Path("data/meta/intraday_test.csv")
    pd.concat([pd.read_csv(f) if f.exists() else pd.DataFrame(), pd.DataFrame([summ])]).to_csv(f, index=False)
    print(pd.Series(summ).to_string())
    imap = imap.astype({"last_bar": str}) if "last_bar" in imap else imap
    last = last.astype(str)
    with pd.ExcelWriter("intraday_test_report.xlsx") as xw:
        pd.DataFrame([summ]).T.reset_index().to_excel(xw, sheet_name="Summary", index=False)
        imap.to_excel(xw, sheet_name="Index_map", index=False)
        last.rename("last_bar").reset_index().to_excel(xw, sheet_name="Stock_last_bars", index=False)


if __name__ == "__main__":
    main()
