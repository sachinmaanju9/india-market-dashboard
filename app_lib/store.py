"""
Where data files live (Step 12, R4).

- Source data that only grows (raw prices, indices, ownership, logs, universe) is in the main branch.
- Files that are fully REGENERATED every day/week (screen metrics, fundamentals snapshots) live on a
  separate branch `derived` that keeps only the latest copy (force-pushed), so the repo doesn't grow
  by gigabytes a year.
`path()` returns a local file if present (GitHub jobs, local dev), otherwise downloads the latest copy
from the derived branch (the deployed Streamlit app).
"""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

REPO = "sachinmaanju9/india-market-dashboard"
RAW = f"https://raw.githubusercontent.com/{REPO}/derived/"
CACHE = Path(os.environ.get("DERIVED_CACHE", "/tmp/derived_cache"))
DERIVED = [
    "data/screens/metrics_latest.parquet", "data/screens/bench_returns.csv",
    "data/fundamentals/statements_inr.parquet", "data/fundamentals/yf_statements.parquet",
    "data/fundamentals/yf_info.parquet", "data/fundamentals/nse_shareholding.parquet",
]
IST = timezone(timedelta(hours=5, minutes=30))


def path(rel, max_age_min=30):
    local = Path(rel)
    if local.exists():
        return str(local)
    dst = CACHE / rel
    if dst.exists() and time.time() - dst.stat().st_mtime < max_age_min * 60:
        return str(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(RAW + rel + f"?t={int(time.time() // 600)}", timeout=60)
    r.raise_for_status()
    tmp = dst.with_suffix(dst.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dst)
    return str(dst)


def expected_trading_date(now=None):
    """Most recent weekday whose evening refresh (8:30 pm IST) should have run."""
    now = now or datetime.now(IST)
    d = now.date() if now.hour * 60 + now.minute >= 20 * 60 + 30 else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def freshness():
    """(last price date, stale?) using the load log: a holiday counts as checked."""
    log = Path("data/meta/load_log.csv")
    if not log.exists():
        return None, False
    lg = pd.read_csv(log, dtype={"date": str})
    ok = lg[lg["status"].astype(str).str.startswith("ok")]
    checked = lg[lg["status"].isin(["ok", "ok_no_delivery", "holiday", "no_session"])]
    last_price = pd.to_datetime(ok["date"]).max().date() if len(ok) else None
    last_checked = pd.to_datetime(checked["date"]).max().date() if len(checked) else None
    expected = expected_trading_date()
    stale = last_checked is None or last_checked < expected
    pending = bool(((pd.to_datetime(lg["date"]).dt.date == expected) & (lg["status"] == "pending")).any())
    return last_price, ("pending" if stale and pending else stale)


def banner(st):
    """Small 'data as of' line on every page; amber when the daily refresh is behind."""
    last, stale = freshness()
    if last is None:
        return
    if stale == "pending":
        st.caption(f"Data as of **{last:%a %d %b %Y}** · today's NSE files aren't published yet "
                   "(market holiday or a late upload) — the next run will check again.")
    elif stale:
        st.warning(f"Data as of **{last:%a %d %b %Y}** — the daily refresh looks behind. "
                   "Check the GitHub Actions tab.", icon="⚠️")
    else:
        st.caption(f"✓ Data as of **{last:%a %d %b %Y}** (refreshed daily after market close)")
