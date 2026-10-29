from AlgorithmImports import *

"""Readable two-module QuantConnect strategy with allocation controls.

Add main.py and timing_core.py to the same project's root directory.
Default: SPY dividend yield x lagged CFNAI, with BIL as a cash proxy.
The supplied macro CSVs are revised history, not point-in-time PMI data.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from timing_core import (MODES, ReleaseTape, TimingModel, proxy_monthly_panel,
                         trade_parameters)


class NonlinearFactorTiming(QCAlgorithm):
    def initialize(self):
        start = datetime.fromisoformat(self.get_parameter("start") or "2020-01-01")
        end = datetime.fromisoformat(self.get_parameter("end") or "2025-12-31")
        if start.year < 2008:
            raise ValueError("BIL cash proxy requires start >= 2008")
        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.set_cash(1_000_000)
        self.set_time_zone(TimeZones.NEW_YORK)
        self.settings.daily_precise_end_time = True
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE,
                                 AccountType.MARGIN)
        self.model_name = (self.get_parameter("model") or "interaction").strip().lower()
        if self.model_name not in MODES + ("fixed",):
            raise ValueError("model must be mean/linear/bivariate/interaction/fixed")
        self.window = int(self.get_parameter("window") or 240)
        self.expanding = self._flag("expanding", False)
        self.sign_filter = self._flag("economic_constraints", True)
        self.gamma = float(self.get_parameter("gamma") or 3)
        self.lam = float(self.get_parameter("lambda") or 100*self.gamma)
        cap = float(self.get_parameter("var_cap") or 0.995)
        self.var_cap = None if cap == 0 else cap
        self.max_weight = float(self.get_parameter("max_weight") or 0.99)
        if not 0 < self.max_weight <= 0.995:
            raise ValueError("This long-only adapter requires max_weight<=0.995")
        if self.window < 24:
            raise ValueError("window must be >=24 months")
        self.fixed_weight = float(self.get_parameter("fixed_weight") or 0.55)
        self.fixed_entry = (self.get_parameter("fixed_entry") or "immediate").strip().lower()
        if self.model_name == "fixed":
            if not 0 <= self.fixed_weight <= self.max_weight:
                raise ValueError("fixed_weight must be between 0 and max_weight")
            if self.fixed_entry not in ("immediate", "gradual"):
                raise ValueError("fixed_entry must be immediate or gradual")
        self.entry_speed = trade_parameters(self.gamma, self.lam)[1]
        slip = float(self.get_parameter("slippage_bps") or 1) / 10000.0
        if slip < 0:
            raise ValueError("slippage_bps must be nonnegative")
        self.spy = self.add_equity("SPY", Resolution.MINUTE,
                                   data_normalization_mode=DataNormalizationMode.RAW).symbol
        self.bil = self.add_equity("BIL", Resolution.MINUTE,
                                   data_normalization_mode=DataNormalizationMode.RAW).symbol
        for symbol in (self.spy, self.bil):
            self.securities[symbol].set_slippage_model(ConstantSlippageModel(slip))
        self.set_benchmark(self.spy)
        self.scored = []
        self.pending_forecasts = None
        self.allocation_stats = {"n": 0, "sum": 0.0, "cap": 0, "floor": 0}
        self.fit_stats = {mode: {"fits": 0, "fallback": 0, "stabilized": 0}
                          for mode in MODES}
        self.schedule.on(self.date_rules.month_start(self.spy),
                         self.time_rules.after_market_open(self.spy, 30),
                         self.rebalance)
        if self.model_name == "fixed":
            # A tradeable control with the same assets, schedule and cost models.
            # It intentionally requires no macro CSVs or 240-month training history.
            self.debug(f"Fixed allocation control: SPY aim={self.fixed_weight:.1%}; "
                       f"entry={self.fixed_entry}; kappa={self.entry_speed:.6f}. "
                       "The immediate control rebalances to its fixed weight monthly; "
                       "the gradual control uses the timing policy's entry speed.")
            return
        # Data live in CSVs in Object Store, never in Python source files.
        self.macro = self._load_release_tape("macro_key", "CFNAI")
        self.risk_free = self._load_release_tape("rf_key", "TB3MS")
        self.prices, self.dividends = {}, {}
        self._update_history(datetime(1998, 1, 1),
                             self.time.replace(hour=0, minute=0, second=0, microsecond=0))
        self.debug("Market timing proxy: raw SPY trailing dividends; BIL residual allocation. "
                   "Original PC1/HML experiments are in paper_research.py. "
                   "lambda controls the dynamic policy; LEAN charges brokerage fees/slippage.")

    def _flag(self, name, default):
        value = self.get_parameter(name)
        return default if value is None or value == "" else value.lower() in ("1", "true", "yes")

    def _load_release_tape(self, parameter, series):
        defaults = {
            "CFNAI": "macro_cfnai_REVISED_DEMO.csv",
            "TB3MS": "rf_tb3ms_REVISED_DEMO.csv",
        }
        configured_key = (self.get_parameter(parameter) or "").strip()
        url_parameter = "macro_url" if parameter == "macro_key" else "rf_url"
        url = (self.get_parameter(url_parameter) or "").strip()
        if url and not configured_key:
            # Alternative for accounts without Object Store: the user's own CSV URL.
            if not url.lower().startswith("https://"):
                raise ValueError(url_parameter + " must be a direct HTTPS CSV URL")
            try:
                text = self.download(url)
            except Exception as error:
                raise ValueError("Could not load " + url_parameter
                                 + "; check the direct CSV URL and access permissions") from error
            return ReleaseTape.from_csv(text, source=url_parameter + " response")
        key = configured_key or defaults[series]
        # The UI can place an upload at the root or under a project prefix.
        keys = [key]
        if "/" not in key:
            keys.append(f"{self.project_id}/{key}")
        for candidate in keys:
            if self.object_store.contains_key(candidate):
                tape = ReleaseTape.from_csv(
                    self.object_store.read(candidate),
                    source="Object Store key " + candidate)
                self.debug(series + ": loaded monthly release CSV from Object Store. "
                           "The supplied demo files are revised history with an "
                           "assumed two-month delay, not actual release vintages.")
                return tape
        raise ValueError(
            "Missing macro CSV: " + key + ". Upload it to Object Store, then set "
            + parameter + " to its exact key; or supply " + url_parameter
            + " as a direct HTTPS CSV URL. Do not paste dataset values into .py files.")

    def _update_history(self, start, end):
        if start >= end:
            return
        # Explicit RAW prices: adjusted prices plus cash dividends double-count income.
        bars = self.history[TradeBar](self.spy, start, end, Resolution.DAILY,
                                     fill_forward=False,
                                     data_normalization_mode=DataNormalizationMode.RAW)
        for bar in bars:
            self.prices[pd.Timestamp(bar.time.date())] = float(bar.close)
        for div in self.history[Dividend](self.spy, start, end, Resolution.DAILY):
            self.dividends[pd.Timestamp(div.time.date())] = float(div.distribution)
        # This is intentionally a fixed-SPY implementation, not a generic ticker strategy.
        for split in self.history[Split](self.spy, start, end, Resolution.DAILY):
            if abs(float(split.split_factor)-1.0) > 1e-12:
                raise ValueError("Unexpected SPY split; add split adjustment before proceeding")
        if not self.prices:
            raise ValueError("No historical SPY prices; check US equity data access")

    def rebalance(self):
        if not all(self.securities[s].has_data for s in (self.spy, self.bil)):
            self.debug("No current SPY/BIL quote; skipping monthly order")
            return
        current_month = pd.Timestamp(self.time).to_period("M")
        previous = float(self.portfolio[self.spy].holdings_value) / float(
            self.portfolio.total_portfolio_value)
        if self.model_name == "fixed":
            target = self.fixed_weight
            if self.fixed_entry == "gradual":
                target = (1-self.entry_speed)*previous + self.entry_speed*self.fixed_weight
            self._place_allocation(target, current_month)
            return
        formation = (current_month-1).end_time
        start = max(self.prices)-timedelta(days=10)
        end = self.time.replace(hour=0, minute=0, second=0, microsecond=0)
        self._update_history(start.to_pydatetime(), end)
        panel = proxy_monthly_panel(self.prices, self.dividends, self.macro,
                                    self.risk_free, formation)
        columns = ["log_dp", "macro", "market_excess"]
        valid = panel.replace([np.inf, -np.inf], np.nan).dropna(subset=columns)
        if valid.empty or valid.index[-1].to_period("M") != current_month-1:
            raise ValueError("Latest completed month has no usable valuation/macro data")
        periods = valid.index.to_period("M")
        if not periods.equals(pd.period_range(periods[0], periods[-1], freq="M")):
            raise ValueError("Gap in monthly training data; never compress missing months")
        if len(valid)-1 < self.window:
            self.debug(f"Need {self.window} completed monthly pairs; have {len(valid)-1}")
            return
        if not self.expanding:
            valid = valid.iloc[-self.window-1:]
        states = valid[["log_dp", "macro"]].to_numpy(float)
        returns = valid["market_excess"].to_numpy(float)
        # Score predictions made at the PREVIOUS formation date.
        if self.pending_forecasts is not None:
            old = self.pending_forecasts
            if old["formation"]+1 == current_month-1:
                self.scored.append({"month": str(current_month-1),
                                    "realized": float(returns[-1]), **old["predictions"]})
        fitted = {}
        for mode in MODES:
            fitted[mode] = TimingModel(
                mode, self.gamma, self.lam,
                economic_constraints=self.sign_filter,
                stationarity_cap=self.var_cap).fit(states[:-1], states[1:], returns[1:])
            stats = self.fit_stats[mode]
            stats["fits"] += 1
            stats["fallback"] += int(any(fitted[mode].constraint_active))
            stats["stabilized"] += int(fitted[mode].var_stabilized)
        model = fitted[self.model_name]
        state = states[-1]
        unconstrained = float(model.weight(state, [previous])[0])
        predictions = {name: float(obj.forecast(state)[0]) for name, obj in fitted.items()}
        self.pending_forecasts = {"formation": current_month-1, "predictions": predictions}
        self._place_allocation(unconstrained, current_month)

    def _place_allocation(self, unconstrained, current_month):
        if not np.isfinite(unconstrained):
            raise ValueError("Non-finite portfolio target")
        # Practical exposure limits are an adaptation, not the paper's constrained solution.
        target = float(np.clip(unconstrained, 0.0, self.max_weight))
        stats = self.allocation_stats
        stats["n"] += 1
        stats["sum"] += target
        stats["cap"] += int(unconstrained >= self.max_weight)
        stats["floor"] += int(unconstrained <= 0)
        self.plot("Allocation", "SPY target", target)
        label = ("fixed/" + self.fixed_entry if self.model_name == "fixed"
                 else self.model_name)
        self.set_runtime_statistic("Model", label)
        self.set_runtime_statistic("SPY target", f"{target:.1%}")
        self.set_runtime_statistic("Avg SPY target", f"{stats['sum']/stats['n']:.1%}")
        self.set_runtime_statistic("Cap touches", f"{stats['cap']}/{stats['n']}")
        if self.model_name != "fixed":
            fitted = self.fit_stats[self.model_name]
            self.set_runtime_statistic("Mean fallback", f"{fitted['fallback']}/{fitted['fits']}")
        # LEAN executes reductions first when a list of PortfolioTargets is passed.
        self.set_holdings([PortfolioTarget(self.spy, target),
                           PortfolioTarget(self.bil, 0.995-target)],
                          tag=f"{self.model_name} {current_month-1}")

    def on_end_of_algorithm(self):
        stats = self.allocation_stats
        if stats["n"]:
            self.debug(f"Allocation summary: rebalances={stats['n']}; "
                       f"avg SPY target={stats['sum']/stats['n']:.2%}; "
                       f"cap touches={stats['cap']}/{stats['n']}; "
                       f"zero touches={stats['floor']}/{stats['n']}. "
                       "These are monthly targets, not daily actual exposures.")
        for mode in MODES[1:]:
            fitted = self.fit_stats[mode]
            if fitted["fits"]:
                self.debug(f"Fit diagnostics {mode}: mean fallback="
                           f"{fitted['fallback']}/{fitted['fits']}; "
                           f"VAR stabilized={fitted['stabilized']}/{fitted['fits']}. "
                           "Counts include the final unscored forecast.")
        if self.scored:
            scores = pd.DataFrame(self.scored)
            actual = scores["realized"].to_numpy()
            base_sse = float(np.sum((actual-scores["mean"].to_numpy())**2))
            for mode in MODES[1:]:
                sse = float(np.sum((actual-scores[mode].to_numpy())**2))
                r2 = 1-sse/base_sse if base_sse > 0 else float("nan")
                self.set_runtime_statistic("OOS R2 " + mode, f"{r2:.4%}")
                self.debug(f"{mode}: common-sample one-step OOS R2={r2:.4%}, "
                           f"n={len(scores)}; this is NOT an investment return")
            self.set_runtime_statistic("Forecast n", str(len(scores)))
            self.debug("Forecasts are scored at the following monthly rebalance. "
                       "The final backtest month's return is not scored; "
                       "a complete 2020-2025 run therefore normally scores 71 months.")
