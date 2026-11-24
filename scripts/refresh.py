"""
Step 12 - Daily / weekly refresh orchestrator (R1-R5).

daily  : new NSE files (resumes from last loaded day) -> if anything new: split/bonus check -> screens
weekly : universe (lists, IPO/NEWLIST rule, labels) -> fundamentals incl. dividends -> shareholding
         -> new ownership filings -> split/bonus check -> screens
Every run appends one line to data/meta/refresh_log.csv. Exit code 1 if a required step failed,
so GitHub emails the owner (R5).
"""
import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

LOG = Path("data/meta/refresh_log.csv")
PY = sys.executable


def run(cmd, required=True):
    t0 = time.time()
    print(f"\n=== {' '.join(cmd)}", flush=True)
    rc = subprocess.call([PY] + cmd)
    took = round(time.time() - t0)
    print(f"=== exit {rc} in {took}s", flush=True)
    return {"step": cmd[0].split("/")[-1] + (" " + " ".join(cmd[1:]) if len(cmd) > 1 else ""),
            "rc": rc, "seconds": took, "required": required}


def new_days_since(t_start):
    lg = pd.read_csv("data/meta/load_log.csv", dtype={"date": str})
    recent = lg[lg["loaded_at"] >= t_start]
    return int(recent["status"].astype(str).str.startswith("ok").sum()), recent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "weekly"])
    ap.add_argument("--force-screens", action="store_true")
    a = ap.parse_args()
    t_start = datetime.now().isoformat(timespec="seconds")
    steps = []
    start = (date.today() - timedelta(days=21)).isoformat()   # resumable: done days are skipped

    if a.mode == "weekly":
        steps.append(run(["scripts/step3_universe.py"], required=False))
        steps.append(run(["scripts/step8_fundamentals.py", "--skip-xbrl-sample"], required=False))
        steps.append(run(["scripts/step9_ownership.py"], required=False))

    steps.append(run(["scripts/step4_backfill.py", "--start", start, "--max-minutes", "60"]))
    n_new, recent = new_days_since(t_start)
    print(f"new trading days loaded: {n_new}")
    if n_new or a.mode == "weekly" or a.force_screens or not Path("data/screens/metrics_latest.parquet").exists():
        steps.append(run(["scripts/step9_screens.py"]))
    else:
        print("nothing new - screens not recomputed")

    failed = [s["step"] for s in steps if s["rc"] != 0 and s["required"]]
    warn = [s["step"] for s in steps if s["rc"] != 0 and not s["required"]]
    lg = pd.read_csv("data/meta/load_log.csv", dtype={"date": str})
    last_ok = lg[lg["status"].astype(str).str.startswith("ok")]["date"].max()
    row = {"run_at": t_start, "mode": a.mode, "new_days": n_new, "last_price_date": last_ok,
           "seconds": sum(s["seconds"] for s in steps), "failed": "; ".join(failed), "warnings": "; ".join(warn),
           "steps": " | ".join(f"{s['step']}={s['rc']}({s['seconds']}s)" for s in steps)}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([pd.read_csv(LOG) if LOG.exists() else pd.DataFrame(), pd.DataFrame([row])]) \
        .tail(400).to_csv(LOG, index=False)
    print(pd.Series(row).to_string())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
