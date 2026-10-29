"""Market/HML/PC1 research backtests using user-supplied MONTHLY factor data.

Can be imported in a QuantConnect Research notebook or run locally. Does not
pretend that HML/PC1 are listed securities. See README_zh.md for input schemas.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from timing_core import MODES, ReleaseTape, TimingModel


def monthly_index(frame):
    frame = frame.copy()
    if "date" in frame:
        frame.index = pd.to_datetime(frame.pop("date"))
    frame.index = pd.DatetimeIndex(frame.index).to_period("M").to_timestamp("M")
    if frame.index.has_duplicates:
        raise ValueError("Duplicate monthly dates")
    frame = frame.sort_index()
    if len(frame):
        p = frame.index.to_period("M")
        if not p.equals(pd.period_range(p[0], p[-1], freq="M")):
            raise ValueError("Input has a gap; cannot treat nonconsecutive months as adjacent")
    return frame


class FrozenPCA:
    """PCA estimated in an initial window and then FROZEN for OOS use.

    Market-neutral R_j = R_j-beta_j*R_market, standardized by training return
    volatility. Apply the SAME hedge, volatility scale and eigenvectors to
    log-BM. This defines a consistent linear valuation-mimicking signal.

    If the source already provides market-adjusted/standardized log-BM, pass
    bm_preprocessed=True and do not hedge or rescale those inputs twice.
    Original published factor data/preprocessing should take precedence.
    """
    def __init__(self, n_components=4, bm_preprocessed=False):
        self.n_components = n_components
        self.bm_preprocessed = bm_preprocessed

    def fit(self, returns, market_excess):
        R = np.asarray(returns, float)
        m = np.asarray(market_excess, float).reshape(-1)
        if R.ndim != 2 or len(R) != len(m) or len(R) < 24:
            raise ValueError("PCA training dimensions are invalid")
        if not np.isfinite(R).all() or not np.isfinite(m).all():
            raise ValueError("PCA inputs must be finite")
        if not 1 <= self.n_components <= R.shape[1]:
            raise ValueError("Too many principal components")
        self.beta = np.linalg.lstsq(np.column_stack([np.ones(len(m)), m]), R, rcond=None)[0][1]
        neutral = R-m[:, None]*self.beta
        self.vol = neutral.std(axis=0, ddof=1)
        if np.any(self.vol < 1e-10):
            raise ValueError("An anomaly has zero residual volatility")
        z = neutral/self.vol
        cov = np.cov(z, rowvar=False)
        values, vectors = np.linalg.eigh(cov)
        order = np.argsort(values)[::-1][:self.n_components]
        self.loadings = vectors[:, order]
        # Canonical sign chosen from training loadings only, never future returns.
        for j in range(self.n_components):
            anchor = np.argmax(abs(self.loadings[:, j]))
            self.loadings[:, j] *= np.sign(self.loadings[anchor, j]) or 1
        self.explained_variance_ratio = values[order]/values.sum()
        self.weights = self.loadings/self.vol[:, None]
        self.market_hedge = -self.beta @ self.weights
        return self

    def transform(self, returns, log_bm, market_excess, market_log_bm=None):
        R, B = np.asarray(returns, float), np.asarray(log_bm, float)
        m = np.asarray(market_excess, float).reshape(-1)
        if R.shape != B.shape or R.shape[1] != len(self.beta):
            raise ValueError("Returns and valuation panels must have identical anomaly columns")
        pc_returns = R @ self.weights + m[:, None]*self.market_hedge
        if self.bm_preprocessed:
            pc_bm = B @ self.loadings
        else:
            if market_log_bm is None:
                raise ValueError("Raw log-BM preprocessing needs market_log_bm")
            bm = np.asarray(market_log_bm, float).reshape(-1)
            pc_bm = B @ self.weights + bm[:, None]*self.market_hedge
        if not np.isfinite(pc_returns).all() or not np.isfinite(pc_bm).all():
            raise ValueError("Non-finite transformed PCA data")
        return pc_returns, pc_bm


def add_pca(panel, anomaly_returns, anomaly_logbm, initial=240,
            bm_preprocessed=False):
    panel = monthly_index(panel)
    R, B = monthly_index(anomaly_returns), monthly_index(anomaly_logbm)
    if set(R.columns) != set(B.columns):
        raise ValueError("Anomaly return and log-BM column names differ")
    if not R.index.equals(B.index) or not R.index.equals(panel.index):
        raise ValueError("PCA panels must use exactly the same monthly dates")
    B = B[R.columns]
    if len(R) <= initial:
        raise ValueError("Need an OOS sample after the PCA initial window")
    builder = FrozenPCA(min(4, R.shape[1]), bm_preprocessed)
    builder.fit(R.iloc[:initial], panel["market_excess"].iloc[:initial])
    pcr, pcb = builder.transform(R, B, panel["market_excess"],
                               panel.get("market_log_bm"))
    out = panel.copy()
    for j in range(pcr.shape[1]):
        out[f"pc{j+1}_return"] = pcr[:, j]
        out[f"pc{j+1}_log_bm"] = pcb[:, j]
    metadata = {
        "pca_training_end": str(panel.index[initial-1].date()),
        "pca_initial_months": initial, "pca_frozen": True,
        "n_anomalies": R.shape[1],
        "explained_variance_ratio": builder.explained_variance_ratio.tolist(),
        "anomaly_names": list(R.columns),
        "weights": builder.weights.tolist(), "market_hedge": builder.market_hedge.tolist(),
        "bm_preprocessed": bm_preprocessed}
    return out, metadata


TARGETS = {
    "market": (["market_excess"], ["log_dp"]),
    "hml": (["hml"], ["value_spread"]),
    "pc1": (["pc1_return"], ["pc1_log_bm"]),
    "joint": (["market_excess", "pc1_return"], ["log_dp", "pc1_log_bm"])}


def cw_pvalue(actual, baseline, alternative, hac_lags=3):
    """Approximate one-sided Clark-West test using Bartlett HAC standard errors.

    This is not the paper's stationary-bootstrap test for delta CER.
    """
    y, f0, f1 = map(np.asarray, (actual, baseline, alternative))
    d = (y-f0)**2 - (y-f1)**2 + (f0-f1)**2
    n = len(d)
    if n < 12:
        return float("nan")
    u = d-d.mean()
    lags = min(hac_lags, n-1)
    lrv = float(u @ u/n)
    for lag in range(1, lags+1):
        lrv += 2*(1-lag/(lags+1))*float(u[lag:] @ u[:-lag]/n)
    se = math.sqrt(max(lrv, 0)/n)
    if se < 1e-15:
        return 1.0 if d.mean() <= 0 else 0.0
    return 0.5*math.erfc(float(d.mean())/se/math.sqrt(2))


def run_research(panel, macro, target="market", window=240, expanding=False,
                 gamma=3.0, lam=300.0, economic_constraints=True,
                 stationarity_cap=0.995, max_abs_weight=None):
    """Report-style monthly factor P&L simulation, not broker execution.

    r_t in the input is the return ENDING in month t. At formation t we train
    only on (state_s, return_s+1), s<=t-1. The return t+1 is used only AFTER
    the forecast and position have been chosen.
    """
    if target not in TARGETS:
        raise ValueError("Unknown target")
    if window < 24:
        raise ValueError("window must be >=24")
    frame = monthly_index(panel)
    returns_cols, valuation_cols = TARGETS[target]
    required = returns_cols+valuation_cols+["rf"]
    if not set(required).issubset(frame.columns):
        raise ValueError("Missing panel columns: "+str(set(required)-set(frame.columns)))
    macro_values = []
    for date in frame.index:
        try:
            cutoff = date.to_period("M").end_time
            macro_values.append(macro.asof(cutoff)["value"])
        except ValueError:
            macro_values.append(np.nan)
    frame["macro"] = macro_values
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=required+["macro"])
    frame = monthly_index(frame)  # Reject interior missing months.
    if len(frame) < window+2:
        raise ValueError("Insufficient completed monthly observations for OOS evaluation")
    S = frame[valuation_cols+["macro"]].to_numpy(float)
    R = frame[returns_cols].to_numpy(float)
    old_weights = {mode: np.zeros(len(returns_cols)) for mode in MODES}
    rows = []
    for t in range(window, len(frame)-1):
        left = 0 if expanding else t-window
        for mode in MODES:
            model = TimingModel(mode, gamma, lam,
                                economic_constraints=economic_constraints,
                                stationarity_cap=stationarity_cap).fit(
                S[left:t], S[left+1:t+1], R[left+1:t+1])
            forecast = model.forecast(S[t])
            old = old_weights[mode]
            w = model.weight(S[t], old)
            if max_abs_weight is not None:
                if max_abs_weight <= 0:
                    raise ValueError("max_abs_weight must be positive")
                w = np.clip(w, -max_abs_weight, max_abs_weight)
            dw = w-old
            cost = 0.5*lam*float(dw @ model.sigma @ dw)
            # Only here, after fitting/forecasting/trading, observe month t+1.
            realized = R[t+1]
            gross_excess = float(w @ realized)
            gross_total = float(frame["rf"].iloc[t+1]) + gross_excess
            row = {"formation": str(frame.index[t].date()),
                   "return_month": str(frame.index[t+1].date()), "model": mode,
                   "gross_total": gross_total, "net_total": gross_total-cost,
                   "gross_excess": gross_excess, "net_excess": gross_excess-cost,
                   "model_cost": cost, "turnover": float(np.abs(dw).sum()),
                   "filter_share": float(np.mean(model.constraint_active)),
                   "var_stabilized": model.var_stabilized}
            for j, name in enumerate(returns_cols):
                row["forecast_"+name] = float(forecast[j])
                row["realized_"+name] = float(realized[j])
                row["weight_"+name] = float(w[j])
                row["aim_"+name] = float(model.aim(S[t])[j])
            rows.append(row)
            old_weights[mode] = w
    detail = pd.DataFrame(rows)
    baseline = detail[detail["model"] == "mean"].reset_index(drop=True)
    summaries = []
    for mode in MODES:
        f = detail[detail["model"] == mode].reset_index(drop=True)
        gross, net = f["gross_total"].to_numpy(), f["net_total"].to_numpy()
        excess = f["net_excess"].to_numpy()
        cer_gross = 12*(gross.mean()-0.5*gamma*gross.var(ddof=1))
        cer_net = 12*(net.mean()-0.5*gamma*net.var(ddof=1))
        base_gross, base_net = baseline["gross_total"], baseline["net_total"]
        base_cer_gross = 12*(base_gross.mean()-0.5*gamma*base_gross.var(ddof=1))
        base_cer_net = 12*(base_net.mean()-0.5*gamma*base_net.var(ddof=1))
        summary = {"model": mode, "n_oos": len(f),
                   "annual_arithmetic_return_net": float(12*net.mean()),
                   "annual_volatility_net": float(np.sqrt(12)*net.std(ddof=1)),
                   "annual_sharpe_net_excess": float(np.sqrt(12)*excess.mean()/excess.std(ddof=1))
                   if excess.std(ddof=1) > 0 else float("nan"),
                   "cer_gross": float(cer_gross), "cer_net": float(cer_net),
                   "delta_cer_gross_vs_mean": float(cer_gross-base_cer_gross),
                   "delta_cer_net_vs_mean": float(cer_net-base_cer_net),
                   "annual_model_cost": float(12*f["model_cost"].mean()),
                   "annual_turnover": float(12*f["turnover"].mean()),
                   "filter_share": float(f["filter_share"].mean()),
                   "var_stabilization_share": float(f["var_stabilized"].mean())}
        for name in returns_cols:
            y = f["realized_"+name].to_numpy()
            f0, f1 = baseline["forecast_"+name].to_numpy(), f["forecast_"+name].to_numpy()
            denominator = float(np.sum((y-f0)**2))
            summary["r2_oos_"+name] = float(1-np.sum((y-f1)**2)/denominator) if denominator else float("nan")
            summary["cw_hac_pvalue_"+name] = cw_pvalue(y, f0, f1)
        summaries.append(summary)
    return detail, pd.DataFrame(summaries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True)
    parser.add_argument("--macro", required=True)
    parser.add_argument("--target", choices=TARGETS, default="market")
    parser.add_argument("--window", type=int, default=240)
    parser.add_argument("--expanding", action="store_true")
    parser.add_argument("--no-economic-constraints", action="store_true")
    parser.add_argument("--gamma", type=float, default=3.0)
    parser.add_argument("--lam", type=float, default=300.0)
    parser.add_argument("--var-cap", type=float, default=0.995,
                        help="0 disables VAR stabilization")
    parser.add_argument("--max-abs-weight", type=float)
    parser.add_argument("--anomaly-returns")
    parser.add_argument("--anomaly-logbm")
    parser.add_argument("--pca-initial", type=int, default=240)
    parser.add_argument("--bm-preprocessed", action="store_true")
    parser.add_argument("--out", default="research_output")
    args = parser.parse_args()
    panel = monthly_index(pd.read_csv(args.panel))
    metadata = {}
    if bool(args.anomaly_returns) != bool(args.anomaly_logbm):
        parser.error("Supply BOTH --anomaly-returns and --anomaly-logbm")
    if args.anomaly_returns:
        if args.pca_initial > args.window:
            parser.error("pca-initial cannot extend beyond the first OOS formation window")
        panel, metadata = add_pca(panel, pd.read_csv(args.anomaly_returns),
                                 pd.read_csv(args.anomaly_logbm), args.pca_initial,
                                 args.bm_preprocessed)
    tape = ReleaseTape(pd.read_csv(args.macro))
    detail, summary = run_research(
        panel, tape, args.target, args.window, args.expanding, args.gamma, args.lam,
        not args.no_economic_constraints, None if args.var_cap == 0 else args.var_cap,
        args.max_abs_weight)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    detail.to_csv(out/"oos_detail.csv", index=False)
    summary.to_csv(out/"oos_summary.csv", index=False)
    metadata.update({"target": args.target, "window": args.window,
                     "expanding": args.expanding, "gamma": args.gamma, "lambda": args.lam,
                     "var_cap": args.var_cap, "input_returns_are_monthly_decimals": True,
                     "cer_bootstrap_pvalues_implemented": False})
    (out/"run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
