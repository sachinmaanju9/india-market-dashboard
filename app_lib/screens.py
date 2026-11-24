"""
Screen definitions (Steps 7 & 8). Each screen: filters (pass/fail) + a 0-100 score.
All thresholds live in DEFAULTS so the app can expose them as adjustable controls.
Works on the metrics table produced by scripts/step9_screens.py (data/screens/metrics_latest.parquet).
"""
import numpy as np
import pandas as pd

DEFAULTS = {
    # T1 common
    "min_value_cr": 3.0, "min_days_ipo": 20,
    # T3 momentum
    "mom_max_from_high": 25.0,
    # T4 volatility & breakout
    "bb_pctile_max": 20.0, "setup_within_pct": 5.0, "breakout_vol_x": 1.5,
    # T5 + F4 mean reversion
    "mr_dd_min": 15.0, "mr_dd_max": 35.0, "mr_rsi_max": 35.0, "mr_below_50dma": 8.0, "mr_near_200dma": 10.0,
    "mr_roce_min": 15.0, "mr_de_max": 0.5,
    # F1 quality + growth
    "qg_roce_min": 15.0, "qg_rev_cagr_min": 12.0, "qg_pat_cagr_min": 12.0, "qg_de_max": 0.5,
    "qg_opm_std_max": 5.0, "qg_cfo_pat_min": 0.8, "qg_roe_fin_min": 15.0, "qg_roa_fin_min": 1.5,
    # F2 deep value
    "dv_cheapest_pct": 30.0, "dv_fcf_yield_min": 4.0, "dv_de_max": 1.0, "dv_piotroski_min": 5,
    # F3 earnings momentum
    "em_pat_yoy_min": 20.0, "em_rev_yoy_min": 10.0,
    # F5
    "piotroski_min": 7, "magic_top_n": 30,
    # F6 ownership
    "own_promoter_up": 0.5, "own_fii_up": 1.0, "own_dii_up": 1.0, "own_max_sell": 2.0,
}

RED_FLAG_RULES = {
    "Cash flow < half of profit (3y)": lambda m: (m["cfo_pat_3y"] < 0.5) & ~m["is_financial"],
    # Sachin's rule: receivable days >= 30 AND receivable-days growth > 1.2x revenue growth
    # (+ days must actually rise, so a revenue fall alone can't trigger it)
    "Receivables: days >= 30 and rising > 1.2x revenue growth": lambda m: (m["receivable_days"] >= 30)
    & (m["receivable_days_growth"] > 0)
    & (m["receivable_days_growth"] > 1.2 * m["revenue_growth_1y"]) & ~m["is_financial"],
    "Interest cover < 1.5": lambda m: (m["interest_cover"] < 1.5) & ~m["is_financial"],
    "Altman Z'' < 1.1 (distress zone)": lambda m: (m["altman_z"] < 1.1) & ~m["is_financial"],
    "Promoter stake down > 5 pts in a year": lambda m: m["d_promoter_4q"] < -5,
    "New shares > 10% in a year": lambda m: m["share_growth_1y"] > 10,
    "Promoter pledge > 25%": lambda m: m["pledge_pct"] > 25,
}


def red_flags(m):
    flags = pd.DataFrame({k: f(m).fillna(False) for k, f in RED_FLAG_RULES.items()}, index=m.index)
    m = m.copy()
    m["red_flag_count"] = flags.sum(axis=1)
    m["red_flags"] = flags.apply(lambda r: "; ".join(k for k, v in r.items() if v), axis=1)
    return m


def pct_rank(s):
    return s.rank(pct=True) * 100


def _common(m, p, need_days):
    """T1: liquidity, minimum history, IPO/NEWLIST >= 20 days. Returns mask + reason."""
    ok = (m["med_value_cr_20d"] >= p["min_value_cr"]) & (m["history_days"] >= max(need_days, p["min_days_ipo"]))
    return ok.fillna(False)


def _fund_universe(m):
    return m["instrument"].fillna("Equity") != "REIT/InvIT"


def momentum(m, p, bench6m):
    base = _common(m, p, 253)
    f = (base & (m["close"] > m["sma200"]) & (m["sma50"] > m["sma200"])
         & (m["pct_from_52w_high"] >= -p["mom_max_from_high"]) & (m["ret_6M"] > bench6m))
    r = m[f].copy()
    r["score"] = r["momentum_score"]
    return r


def breakout(m, p):
    base = _common(m, p, 252)
    setup = (base & (m["bb_width_pctile_1y"] <= p["bb_pctile_max"])
             & (m["close"] >= (1 - p["setup_within_pct"] / 100) * m["high_50d_close"]) & (m["close"] > m["sma200"]))
    s = m[setup].copy()
    s["list"] = "Setting up"
    s["score"] = pct_rank(-s["bb_width_pctile_1y"].fillna(100))
    broke = base & m["breakout_date"].notna() & (m["breakout_vol_x"] >= p["breakout_vol_x"])
    b = m[broke].copy()
    b["list"] = "Broke out"
    tight = -b["bb_width_pctile_1y"].fillna(100)
    early = -(b["close"] / b["breakout_level"] - 1)
    b["score"] = (pct_rank(b["breakout_vol_x"]) + pct_rank(tight) + pct_rank(early)) / 3
    b["score"] = np.where(b["breakout_status"] == "Failed", b["score"] - 100, b["score"])  # failed sorted last
    return s, b


def mean_reversion(m, p):
    base = _common(m, p, 222) & _fund_universe(m)
    dd = -m["pct_from_52w_high"]
    trend = (m["sma200"] > m["sma200_1m_ago"]) & (m["close"] >= (1 - p["mr_near_200dma"] / 100) * m["sma200"])
    pull = (dd.between(p["mr_dd_min"], p["mr_dd_max"])
            & ((m["rsi14"] < p["mr_rsi_max"]) | (m["close"] <= (1 - p["mr_below_50dma"] / 100) * m["sma50"])))
    qual = np.where(m["is_financial"], m["roe_3y"] >= p["mr_roce_min"],
                    (m["roce_3y"] >= p["mr_roce_min"]) & (m["de"] <= p["mr_de_max"]))
    f = base & trend & pull & pd.Series(qual, index=m.index) & m["pat_positive_3y"].fillna(False)
    r = m[f].copy()
    slope = r["sma200"] / r["sma200_1m_ago"] - 1
    r["score"] = (pct_rank(-r["rsi14"]) + pct_rank(-(r["close"] / r["sma50"])) + pct_rank(slope)) / 3
    return r


def quality_growth(m, p):
    base = _common(m, p, 0) & _fund_universe(m)
    nonfin = (~m["is_financial"] & (m["roce_3y"] >= p["qg_roce_min"]) & (m["rev_cagr_3y"] >= p["qg_rev_cagr_min"])
              & (m["pat_cagr_3y"] >= p["qg_pat_cagr_min"]) & (m["de"] <= p["qg_de_max"])
              & (m["opm_std"] <= p["qg_opm_std_max"]) & (m["cfo_pat_3y"] >= p["qg_cfo_pat_min"]))
    fin = (m["is_financial"] & (m["roe_3y"] >= p["qg_roe_fin_min"]) & (m["roa_3y"] >= p["qg_roa_fin_min"])
           & (m["pat_cagr_3y"] >= p["qg_pat_cagr_min"]))
    r = m[base & (nonfin | fin).fillna(False)].copy()
    rc = np.where(r["is_financial"], r["roe_3y"], r["roce_3y"])
    r["score"] = (pct_rank(pd.Series(rc, index=r.index)) + pct_rank(r["rev_cagr_3y"]) + pct_rank(r["pat_cagr_3y"])
                  + pct_rank(-r["opm_std"].fillna(r["opm_std"].max())) + pct_rank(r["cfo_pat_3y"].fillna(0))) / 5
    return r


def deep_value(m, p):
    base = _common(m, p, 0) & _fund_universe(m)
    x = m.copy()
    grp = x.groupby("nse_industry")
    big = grp["symbol"].transform("count") >= 5

    def cheap_pct(col):   # percentile of cheapness within sector (0 = cheapest); whole universe if < 5 peers
        sec = grp[col].rank(pct=True) * 100
        allr = x[col].rank(pct=True) * 100
        return np.where(big, sec, allr)
    x["pe_pctile_sector"] = cheap_pct("pe")
    x["eveb_pctile_sector"] = cheap_pct("ev_ebitda")
    x["pb_pctile_sector"] = cheap_pct("pb")
    c = p["dv_cheapest_pct"]
    nonfin = (~x["is_financial"] & (x["pe_pctile_sector"] <= c) & (x["eveb_pctile_sector"] <= c)
              & (x["fcf_yield"] >= p["dv_fcf_yield_min"]) & (x["de"] <= p["dv_de_max"])
              & (x["piotroski"] >= p["dv_piotroski_min"]))
    fin = x["is_financial"] & (x["pb_pctile_sector"] <= c) & (x["pe_pctile_sector"] <= c)
    guard = x["pat_positive_3y"].fillna(False)
    r = x[base & (nonfin | fin).fillna(False) & guard].copy()
    r["score"] = 100 - np.where(r["is_financial"], (r["pb_pctile_sector"] + r["pe_pctile_sector"]) / 2,
                                (r["pe_pctile_sector"] + r["eveb_pctile_sector"]) / 2)
    return r


def earnings_momentum(m, p):
    base = _common(m, p, 0) & _fund_universe(m)
    f = (base & (m["pat_yoy_q"] >= p["em_pat_yoy_min"]) & (m["rev_yoy_q"] >= p["em_rev_yoy_min"])
         & (m["pat_yoy_q"] > m["pat_growth_compare"]) & (m["opm_q"] > m["opm_q_yago"]))
    r = m[f.fillna(False)].copy()
    r["score"] = (pct_rank(r["pat_yoy_q"]) + pct_rank(r["pat_yoy_q"] - r["pat_growth_compare"])
                  + pct_rank(r["opm_q"] - r["opm_q_yago"])) / 3
    return r


def piotroski(m, p):
    base = _common(m, p, 0) & _fund_universe(m) & ~m["is_financial"]
    r = m[base & (m["piotroski"] >= p["piotroski_min"])].copy()
    r["score"] = r["piotroski"] / 9 * 100
    return r


UTILITIES = {"Power"}


def magic_formula(m, p):
    base = (_common(m, p, 0) & _fund_universe(m) & ~m["is_financial"] & ~m["nse_industry"].isin(UTILITIES)
            & (m["mf_earnings_yield"] > 0) & (m["mf_roc"] > 0))
    r = m[base].copy()
    r["mf_rank"] = r["mf_earnings_yield"].rank(ascending=False) + r["mf_roc"].rank(ascending=False)
    r = r.nsmallest(int(p["magic_top_n"]), "mf_rank")
    r["score"] = pct_rank(-r["mf_rank"])
    return r


def promoter_ownership(m, p):
    base = _common(m, p, 0) & _fund_universe(m) & m["d_promoter_4q"].notna()
    anybuy = ((m["d_promoter_4q"] >= p["own_promoter_up"]) | (m["d_fii_4q"] >= p["own_fii_up"])
              | (m["d_dii_4q"] >= p["own_dii_up"]))
    nosell = ((m["d_promoter_4q"] >= -p["own_max_sell"]) & (m["d_fii_4q"] >= -p["own_max_sell"])
              & (m["d_dii_4q"] >= -p["own_max_sell"]))
    r = m[(base & anybuy & nosell).fillna(False)].copy()
    r["score"] = r["ownership_score"]
    return r


def all_screens(m, p=None, bench6m=0.0):
    p = {**DEFAULTS, **(p or {})}
    setup, broke = breakout(m, p)
    return {
        "Momentum": momentum(m, p, bench6m),
        "Breakout – setting up": setup, "Breakout – broke out": broke,
        "Quality mean-reversion": mean_reversion(m, p),
        "Quality + Growth": quality_growth(m, p), "Deep value": deep_value(m, p),
        "Earnings momentum": earnings_momentum(m, p), "Piotroski F-score": piotroski(m, p),
        "Magic Formula": magic_formula(m, p), "Promoter & ownership": promoter_ownership(m, p),
    }
