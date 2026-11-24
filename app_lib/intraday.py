"""
Intraday data (Step 13): fetched live from yfinance when a page is opened; nothing is stored.
All times are India time. Every result carries the last bar time so pages can show the delay (I3).
"""
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
OPEN, CLOSE = dtime(9, 15), dtime(15, 30)

# Yahoo symbols to try for each NSE index (the I4 test writes the ones that work to data/meta/yahoo_index_map.csv)
INDEX_CANDIDATES = {
    "Nifty 50": ["^NSEI"], "Nifty Bank": ["^NSEBANK"],
    "Nifty Smallcap 250": ["NIFTYSMLCAP250.NS", "^CNXSC", "NIFTY_SMLCAP_250.NS"],
    "Nifty Auto": ["^CNXAUTO"], "Nifty IT": ["^CNXIT"], "Nifty Pharma": ["^CNXPHARMA"],
    "Nifty FMCG": ["^CNXFMCG"], "Nifty Metal": ["^CNXMETAL"], "Nifty Realty": ["^CNXREALTY"],
    "Nifty Media": ["^CNXMEDIA"], "Nifty PSU Bank": ["^CNXPSUBANK"],
    "Nifty Private Bank": ["NIFTY_PVT_BANK.NS", "^NIFTYPVTBANK"],
    "Nifty Financial Services": ["NIFTY_FIN_SERVICE.NS", "^CNXFIN"],
    "Nifty Healthcare Index": ["NIFTY_HEALTHCARE.NS"], "Nifty Consumer Durables": ["NIFTY_CONSR_DURBL.NS"],
    "Nifty Oil & Gas": ["NIFTY_OIL_AND_GAS.NS"], "Nifty Chemicals": ["NIFTY_CHEMICALS.NS"],
    "Nifty Capital Goods": ["NIFTY_CAPITAL_GOODS.NS"],
    "Nifty Cement": ["NIFTY_CEMENT.NS"], "Nifty Power": ["NIFTY_POWER.NS"],
    "Nifty Telecommunications": ["NIFTY_TELECOM.NS"], "Nifty NBFC": ["NIFTY_NBFC.NS"],
    "Nifty Insurance": ["NIFTY_INSURANCE.NS"], "Nifty Hospitals": ["NIFTY_HOSPITALS.NS"],
    "Nifty Retail": ["NIFTY_RETAIL.NS"], "Nifty Housing Finance": ["NIFTY_HOUSING_FIN.NS"],
    "Nifty Construction": ["NIFTY_CONSTRUCTION.NS"], "Nifty Consumer Services": ["NIFTY_CONSUMER_SERVICES.NS"],
}
MAP_FILE = Path("data/meta/yahoo_index_map.csv")


def now_ist():
    return datetime.now(IST)


def market_status(now=None):
    now = now or now_ist()
    if now.weekday() >= 5:
        return "closed (weekend)"
    t = now.time()
    if t < OPEN:
        return "pre-open"
    if t <= CLOSE:
        return "open"
    return "closed"


def index_symbols():
    """Index name -> Yahoo symbol: tested map if present, otherwise the first candidate."""
    if MAP_FILE.exists():
        mp = pd.read_csv(MAP_FILE).dropna(subset=["yahoo"])
        return dict(zip(mp["index"], mp["yahoo"]))
    return {k: v[0] for k, v in INDEX_CANDIDATES.items()}


def fetch(tickers, interval="5m", period="1d", chunk=100):
    """{ticker: DataFrame[Open High Low Close Volume] indexed by IST time}. Missing tickers are skipped."""
    import yfinance as yf
    out = {}
    tickers = list(dict.fromkeys(tickers))
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            d = yf.download(part, period=period, interval=interval, group_by="ticker", threads=True,
                            progress=False, auto_adjust=False, prepost=False)
        except Exception:
            continue
        for t in part:
            try:
                x = d[t] if isinstance(d.columns, pd.MultiIndex) else d
                x = x.dropna(subset=["Close"])
                if len(x):
                    x.index = pd.to_datetime(x.index).tz_convert(IST)
                    out[t] = x
            except Exception:
                pass
    return out


def split_days(bars):
    """(today's bars, previous session close) from a 2-day fetch."""
    d = bars.index.date
    last_day = d.max()
    today = bars[d == last_day]
    prev = bars[d < last_day]
    return today, (prev["Close"].iloc[-1] if len(prev) else np.nan)


def summarise(bars, prev_close=None, minutes=60):
    """Day change vs previous close, last-hour change, last bar time, last-hour volume."""
    c = bars["Close"]
    last_t = c.index[-1]
    ref = c[c.index <= last_t - timedelta(minutes=minutes)]
    hour_base = ref.iloc[-1] if len(ref) else bars["Open"].iloc[0]
    day_base = prev_close if prev_close and not np.isnan(prev_close) else bars["Open"].iloc[0]
    return {"last": c.iloc[-1], "last_bar": last_t,
            "day_pct": (c.iloc[-1] / day_base - 1) * 100,
            "hour_pct": (c.iloc[-1] / hour_base - 1) * 100,
            "hour_volume": bars.loc[bars.index > last_t - timedelta(minutes=minutes), "Volume"].sum()}


def delay_minutes(last_bar, interval_min=5, now=None):
    """Minutes between the END of the last bar and now (0 if the market is closed and data is final)."""
    now = now or now_ist()
    end = last_bar + timedelta(minutes=interval_min)
    if market_status(now) != "open":
        return None
    return max((now - end).total_seconds() / 60, 0)
