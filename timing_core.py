"""Nonlinear factor timing: pure NumPy/Pandas implementation.

All returns are monthly DECIMALS. States must be known before their target
returns. The quadratic-state resolvent computes the infinite-horizon aim
portfolio, rather than smoothing a one-period mean/variance portfolio.
"""
from io import StringIO
import math

import numpy as np
import pandas as pd


MODES = ("mean", "linear", "bivariate", "interaction")


def read_csv_payload(text, source):
    """Reject empty/block-page responses before handing them to Pandas."""
    if not isinstance(text, str) or not text.lstrip("\ufeff").strip():
        raise ValueError(source + ": empty CSV response")
    cleaned = text.lstrip("\ufeff").strip()
    if cleaned.startswith("<"):
        raise ValueError(source + ": received HTML/XML instead of CSV")
    try:
        frame = pd.read_csv(StringIO(cleaned))
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as error:
        raise ValueError(source + ": unreadable CSV response") from error
    if frame.empty:
        raise ValueError(source + ": CSV contains no data rows")
    return frame


def trade_parameters(gamma=3.0, lam=300.0, annual_discount=0.02):
    """Return (a, kappa, q) for the Bellman equation on report pp. 5-6.

    beta = 1-delta; a^2 + (gamma + delta*lam/beta)*a - gamma*lam = 0.
    The rationalized root avoids cancellation for very large lambda.
    """
    if gamma <= 0 or lam < 0 or annual_discount < 0:
        raise ValueError("Require gamma>0, lambda>=0, annual_discount>=0")
    if lam == 0:
        return 0.0, 1.0, 0.0
    beta = (1.0 + annual_discount) ** (-1.0 / 12.0)
    b = gamma + (1.0 - beta) * lam / beta
    a = 2.0 * gamma * lam / (b + math.sqrt(b*b + 4.0*gamma*lam))
    return a, a / lam, a / (gamma + a)


def quadratic_pairs(d):
    return [(i, j) for i in range(d) for j in range(i, d)]


def lift(x):
    """[1, f_0, ..., f_d, f_0^2, f_0*f_1, ..., f_d^2]."""
    x = np.asarray(x, dtype=float)
    one_row = x.ndim == 1
    x = np.atleast_2d(x)
    columns = [np.ones(len(x))] + [x[:, i] for i in range(x.shape[1])]
    columns += [x[:, i] * x[:, j] for i, j in quadratic_pairs(x.shape[1])]
    result = np.column_stack(columns)
    return result[0] if one_row else result


def moment_transition(c, A, omega):
    """Exact T with E[lift(f_next)|f] = T @ lift(f).

    f_next = c + A @ f + epsilon; E[epsilon]=0; Var[epsilon]=omega.
    Keeping squares is essential even when return regressions only use x*s:
    a general VAR generates square terms in E[x_next*s_next].
    """
    c, A, omega = map(lambda a: np.asarray(a, dtype=float), (c, A, omega))
    d = len(c)
    pairs = quadratic_pairs(d)
    T = np.zeros((1+d+len(pairs), 1+d+len(pairs)))
    T[0, 0] = 1.0
    T[1:1+d, 0] = c
    T[1:1+d, 1:1+d] = A
    for r, (i, j) in enumerate(pairs, 1+d):
        T[r, 0] = c[i]*c[j] + omega[i, j]
        T[r, 1:1+d] = c[i]*A[j] + c[j]*A[i]
        for col, (k, ell) in enumerate(pairs, 1+d):
            T[r, col] = (A[i, k]*A[j, ell] if k == ell else
                         A[i, k]*A[j, ell] + A[i, ell]*A[j, k])
    return T


def positive_covariance(residuals, floor=1e-8):
    residuals = np.atleast_2d(residuals)
    centered = residuals - residuals.mean(axis=0)
    cov = centered.T @ centered / max(len(centered)-1, 1)
    values, vectors = np.linalg.eigh(cov)
    return (vectors * np.maximum(values, floor)) @ vectors.T


class TimingModel:
    """OLS return equations + VAR(1) + exact quadratic aim portfolio.

    Last state column is the procyclical macro indicator. Each return equation
    has its own valuation predictor, selected by predictor_indices. In joint
    timing the VAR allows cross-predictor dynamics.
    """
    def __init__(self, mode="interaction", gamma=3.0, lam=300.0,
                 annual_discount=0.02, economic_constraints=True,
                 stationarity_cap=0.995):
        if mode not in MODES:
            raise ValueError("Unknown model: " + mode)
        self.mode = mode
        self.gamma, self.lam = float(gamma), float(lam)
        self.economic_constraints = bool(economic_constraints)
        self.stationarity_cap = stationarity_cap
        self.a, self.kappa, self.q = trade_parameters(gamma, lam, annual_discount)

    def fit(self, states, next_states, next_returns, predictor_indices=None):
        """Every row is a COMPLETED (f_t, f_t+1, r_t+1) observation."""
        X, Xn = np.asarray(states, float), np.asarray(next_states, float)
        Y = np.asarray(next_returns, float)
        if Y.ndim == 1:
            Y = Y[:, None]
        if X.ndim != 2 or X.shape != Xn.shape or len(X) != len(Y):
            raise ValueError("Misaligned training observations")
        if X.shape[1] < 2 or len(X) < 24:
            raise ValueError("Need at least 24 observations and valuation + macro")
        if not all(np.isfinite(a).all() for a in (X, Xn, Y)):
            raise ValueError("Non-finite training data; never backfill future values")
        self.center = X.mean(axis=0)
        self.scale = X.std(axis=0, ddof=1)
        if np.any(self.scale < 1e-10):
            raise ValueError("A predictor has no variation")
        F = (X-self.center)/self.scale
        Fn = (Xn-self.center)/self.scale
        self.d, self.n = F.shape[1], Y.shape[1]
        if predictor_indices is None:
            predictor_indices = list(range(self.n))
        if len(predictor_indices) != self.n or any(
                p < 0 or p >= self.d-1 for p in predictor_indices):
            raise ValueError("Each return needs one valuation predictor")
        # Full VAR with an intercept; all estimates use completed observations.
        V = np.column_stack([np.ones(len(F)), F])
        coef = np.linalg.lstsq(V, Fn, rcond=None)[0]
        self.c, self.A = coef[0].copy(), coef[1:].T.copy()
        self.var_radius = float(max(abs(np.linalg.eigvals(self.A))))
        self.var_stabilized = False
        if self.stationarity_cap is not None:
            cap = float(self.stationarity_cap)
            if not 0 < cap < 1:
                raise ValueError("stationarity_cap must be in (0,1) or None")
            if self.var_radius > cap:
                self.A *= cap / self.var_radius
                # Preserve the estimated one-step average after stabilization.
                self.c = Fn.mean(axis=0) - self.A @ F.mean(axis=0)
                self.var_stabilized = True
        innovations = Fn - self.c - F @ self.A.T
        self.omega = positive_covariance(innovations)
        self.T = moment_transition(self.c, self.A, self.omega)
        Z = lift(F)
        pairs = quadratic_pairs(self.d)
        self.C = np.zeros((self.n, Z.shape[1]))
        self.constraint_active = []
        macro = self.d-1
        for i, pred in enumerate(predictor_indices):
            cols = [0]
            if self.mode != "mean":
                cols.append(1+pred)
            if self.mode in ("bivariate", "interaction"):
                cols.append(1+macro)
            cross = 1+self.d+pairs.index((pred, macro))
            if self.mode == "interaction":
                cols.append(cross)
            b = np.linalg.lstsq(Z[:, cols], Y[:, i], rcond=None)[0]
            self.C[i, cols] = b
            bad = (self.mode != "mean" and self.C[i, 1+pred] <= 0)
            if self.mode == "interaction":
                bad = bad or self.C[i, cross] >= 0
            active = bool(self.economic_constraints and bad)
            self.constraint_active.append(active)
            if active:
                # Report's economic-sign filter: use historical-mean forecast.
                self.C[i] = 0
                self.C[i, 0] = Y[:, i].mean()
        self.sigma = positive_covariance(Y-Z @ self.C.T)
        resolvent = np.eye(len(self.T)) - self.q*self.T
        if np.linalg.cond(resolvent) > 1e12:
            raise ValueError("Unstable state resolvent; inspect VAR estimates")
        self.aim_map = np.linalg.solve(
            self.gamma*self.sigma,
            (1-self.q) * np.linalg.solve(resolvent.T, self.C.T).T)
        return self

    def _state(self, state):
        state = np.asarray(state, float)
        if state.shape != self.center.shape or not np.isfinite(state).all():
            raise ValueError("Invalid current state")
        return lift((state-self.center)/self.scale)

    def forecast(self, state):
        return self.C @ self._state(state)

    def aim(self, state):
        # Sum_{s>=0} (1-q)*q^s*(gamma*Sigma)^-1 E_t[mu(f_t+s)].
        return self.aim_map @ self._state(state)

    def weight(self, state, previous_weight):
        return ((1-self.kappa)*np.asarray(previous_weight, float)
                + self.kappa*self.aim(state))

    def expected_weight_path(self, state, previous_weight, months=84):
        """Unconstrained expected response, useful for the report's GTRF plots."""
        z = self._state(state)
        w = np.atleast_1d(np.asarray(previous_weight, float))
        path = []
        for _ in range(months):
            w = (1-self.kappa)*w + self.kappa*(self.aim_map @ z)
            path.append(w.copy())
            z = self.T @ z
        return np.array(path)


def ny_timestamp(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("America/New_York").tz_localize(None)
    return stamp


class ReleaseTape:
    """A monthly indicator's release/vintage tape, with as-of access only.

    Required CSV columns: period,available_at,value. Naive timestamps are
    America/New_York. Revisions are separate rows with their real release time.
    The latest observation period takes precedence over revisions of old data.
    """
    def __init__(self, frame):
        required = {"period", "available_at", "value"}
        if not required.issubset(frame.columns):
            raise ValueError("Release CSV needs period,available_at,value")
        if frame.empty:
            raise ValueError("Release CSV contains no data rows")
        f = frame[list(sorted(required))].copy()
        f["period"] = pd.to_datetime(f["period"]).dt.to_period("M").dt.to_timestamp()
        f["available_at"] = f["available_at"].map(ny_timestamp)
        f["value"] = pd.to_numeric(f["value"], errors="raise")
        if f.isna().any().any() or not np.isfinite(f["value"]).all():
            raise ValueError("Missing or non-finite release data")
        if (f["available_at"] < f["period"]+pd.offsets.MonthEnd(0)).any():
            raise ValueError("Monthly observation released before its period ended")
        if f.duplicated(["period", "available_at"]).any():
            raise ValueError("Duplicate period/release timestamp")
        self.frame = f.sort_values(["period", "available_at"]).reset_index(drop=True)

    @classmethod
    def from_csv(cls, text, source="Release CSV"):
        return cls(read_csv_payload(text, source))

    @classmethod
    def from_fred_latest(cls, text, series, lag_months=2):
        """Revised-data DEMO only; a fixed delay does not remove revisions."""
        f = read_csv_payload(text, "FRED " + series)
        date_col = "observation_date" if "observation_date" in f else "DATE"
        if date_col not in f or series not in f:
            raise ValueError("Unexpected FRED response for " + series)
        out = pd.DataFrame({
            "period": pd.to_datetime(f[date_col]),
            "value": pd.to_numeric(f[series], errors="coerce")}).dropna()
        if out.empty:
            raise ValueError("FRED " + series + ": CSV contains no valid observations")
        out["available_at"] = out["period"] + pd.DateOffset(months=lag_months)
        return cls(out)

    def asof(self, cutoff, max_age_days=120):
        cutoff = ny_timestamp(cutoff)
        eligible = self.frame[self.frame["available_at"] <= cutoff]
        if eligible.empty:
            raise ValueError("No macro data available as of " + str(cutoff))
        row = eligible.iloc[-1]
        age = (cutoff-row["period"]).days
        if age > max_age_days:
            raise ValueError("Macro data are stale at " + str(cutoff))
        return {"value": float(row["value"]), "period": row["period"],
                "available_at": row["available_at"], "age_days": age}


def proxy_monthly_panel(prices, dividends, macro_tape, rf_tape, cutoff):
    """Construct completed SPY monthly data; raw prices + actual cash dividends.

    SPY is deliberately fixed in the QC adapter. This routine assumes no
    splits and is NOT a general split-adjustment implementation.
    """
    p = pd.Series(prices, dtype=float).sort_index()
    p.index = pd.to_datetime(p.index)
    d = pd.Series(dividends, dtype=float)
    if len(d):
        d.index = pd.to_datetime(d.index)
        d = d.groupby(level=0).sum()
    if p.empty or (p <= 0).any():
        raise ValueError("No valid raw equity prices")
    last_period = ny_timestamp(cutoff).to_period("M")
    p = p[p.index.to_period("M") <= last_period]
    daily_div = d.reindex(p.index, fill_value=0.0)
    # Total return includes reinvestment on the ex-dividend date.
    gross = (p+daily_div)/p.shift(1)
    wealth = gross.fillna(1.0).cumprod()
    periods = p.index.to_period("M")
    mp = p.groupby(periods).last()
    mw = wealth.groupby(periods).last()
    md = daily_div.groupby(periods).sum()
    full = pd.period_range(mp.index[0], mp.index[-1], freq="M")
    if not mp.index.equals(full):
        raise ValueError("Missing price month; do not join nonconsecutive returns")
    annual_div = md.rolling(12, min_periods=12).sum()
    result = pd.DataFrame({"price": mp, "total_return": mw.pct_change(fill_method=None),
                           "log_dp": np.log(annual_div/mp)})
    macro, rf, available, observation = [], [], [], []
    for period in result.index:
        formation = period.end_time
        try:
            release = macro_tape.asof(formation)
            macro.append(release["value"])
            available.append(release["available_at"])
            observation.append(release["period"])
        except ValueError:
            macro.append(np.nan)
            available.append(pd.NaT)
            observation.append(pd.NaT)
        # Financing for month m uses the annual yield known before month m.
        try:
            quote = rf_tape.asof((period-1).end_time)["value"] / 100.0
            rf.append((1+quote) ** (period.days_in_month/365.0)-1)
        except ValueError:
            rf.append(np.nan)
    result["macro"] = macro
    result["rf"] = rf
    result["market_excess"] = result["total_return"]-result["rf"]
    result["macro_available_at"] = available
    result["macro_period"] = observation
    result.index = result.index.to_timestamp("M")
    return result
