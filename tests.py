"""Numerical and temporal-integrity tests. No LEAN engine is required."""
from pathlib import Path
import ast
import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from timing_core import (ReleaseTape, TimingModel, lift, moment_transition,
                         proxy_monthly_panel, trade_parameters)
from paper_research import FrozenPCA, add_pca, run_research


def qc_loader_harness(parameters=None, files=None):
    """Load the real two-module adapter with only QCAlgorithm stubbed.

    This executes its actual macro loader, not the LEAN engine or order APIs.
    """
    fake_imports = types.ModuleType("AlgorithmImports")
    fake_imports.QCAlgorithm = type("QCAlgorithm", (), {})
    fake_imports.TimeZones = types.SimpleNamespace(NEW_YORK="New York")
    fake_imports.BrokerageName = types.SimpleNamespace(INTERACTIVE_BROKERS_BROKERAGE="IB")
    fake_imports.AccountType = types.SimpleNamespace(MARGIN="Margin")
    fake_imports.Resolution = types.SimpleNamespace(MINUTE="Minute")
    fake_imports.DataNormalizationMode = types.SimpleNamespace(RAW="Raw")
    fake_imports.ConstantSlippageModel = lambda value: value
    fake_imports.PortfolioTarget = lambda symbol, weight: types.SimpleNamespace(
        symbol=symbol, weight=weight)
    path = Path(__file__).parent/"main.py"
    spec = importlib.util.spec_from_file_location("qc_macro_loader_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"AlgorithmImports": fake_imports}):
        spec.loader.exec_module(module)
    algorithm = module.NonlinearFactorTiming()
    algorithm.project_id = 12345
    parameters = parameters or {}
    algorithm.get_parameter = lambda name: parameters.get(name)
    algorithm.debug = Mock()
    algorithm.download = Mock(side_effect=AssertionError("Network must not be used by default"))
    algorithm.object_store = Mock()
    if files is None:
        root = Path(__file__).parent/"examples"
        files = {name: (root/name).read_text(encoding="utf-8") for name in (
            "macro_cfnai_REVISED_DEMO.csv", "rf_tb3ms_REVISED_DEMO.csv")}
    algorithm.object_store.contains_key.side_effect = lambda key: key in files
    algorithm.object_store.read.side_effect = lambda key: files[key]
    algorithm.time = pd.Timestamp("2020-01-02 10:00").to_pydatetime()
    algorithm.settings = types.SimpleNamespace()
    algorithm.securities = {symbol: types.SimpleNamespace(
        has_data=True, set_slippage_model=Mock()) for symbol in ("SPY", "BIL")}
    algorithm.add_equity = Mock(side_effect=lambda symbol, *args, **kwargs:
                                types.SimpleNamespace(symbol=symbol))
    for name in ("set_start_date", "set_end_date", "set_cash", "set_time_zone",
                 "set_brokerage_model", "set_benchmark", "set_holdings", "plot",
                 "set_runtime_statistic"):
        setattr(algorithm, name, Mock())
    algorithm.schedule = types.SimpleNamespace(on=Mock())
    algorithm.date_rules = types.SimpleNamespace(month_start=Mock())
    algorithm.time_rules = types.SimpleNamespace(after_market_open=Mock())

    class Portfolio(dict):
        total_portfolio_value = 1_000_000

    algorithm.portfolio = Portfolio({symbol: types.SimpleNamespace(holdings_value=0)
                                     for symbol in ("SPY", "BIL")})
    return algorithm


def synthetic_panel(n=210, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.period_range("1990-01", periods=n, freq="M").to_timestamp("M")
    states = np.zeros((n, 2))
    for t in range(1, n):
        states[t] = states[t-1]*[0.93, 0.6]+rng.normal(size=2)*[0.17, 0.6]
    ret = np.zeros(n)
    for t in range(n-1):
        x, s = states[t]
        ret[t+1] = 0.004+0.008*x-0.006*x*s+rng.normal(scale=0.025)
    panel = pd.DataFrame({"market_excess": ret, "log_dp": states[:, 0]-4,
                          "rf": 0.001}, index=dates)
    tape = ReleaseTape(pd.DataFrame({
        "period": dates.to_period("M").to_timestamp(),
        "available_at": dates+pd.Timedelta(hours=8, minutes=30),
        "value": states[:, 1]}))
    return panel, tape


class TimingTests(unittest.TestCase):
    def test_fixed_control_needs_no_csv_and_rebalances_after_drift(self):
        algorithm = qc_loader_harness({"model": "fixed", "macro_url": "invalid",
                                       "rf_url": "invalid"}, files={})
        algorithm._update_history = Mock(side_effect=AssertionError("No training history"))
        algorithm.initialize()
        algorithm.rebalance()
        targets = algorithm.set_holdings.call_args.args[0]
        self.assertAlmostEqual(targets[0].weight, 0.55)
        self.assertAlmostEqual(targets[1].weight, 0.445)
        algorithm.portfolio["SPY"].holdings_value = 610_000
        algorithm.time = pd.Timestamp("2020-02-03 10:00").to_pydatetime()
        algorithm.rebalance()
        self.assertAlmostEqual(algorithm.set_holdings.call_args.args[0][0].weight, 0.55)
        algorithm.download.assert_not_called()
        algorithm.object_store.contains_key.assert_not_called()
        algorithm._update_history.assert_not_called()
        self.assertEqual(algorithm.scored, [])

    def test_gradual_control_uses_actual_weight_and_same_trading_speed(self):
        algorithm = qc_loader_harness({"model": "fixed", "fixed_entry": "gradual"})
        algorithm.initialize()
        algorithm.rebalance()
        kappa = trade_parameters(3, 300)[1]
        first = algorithm.set_holdings.call_args.args[0][0].weight
        self.assertAlmostEqual(first, kappa*0.55)
        self.assertLess(first, 0.06)
        drifted = first + 0.02
        algorithm.portfolio["SPY"].holdings_value = drifted*1_000_000
        algorithm.rebalance()
        second = algorithm.set_holdings.call_args.args[0][0].weight
        self.assertAlmostEqual(second, (1-kappa)*drifted+kappa*0.55)

    def test_fixed_control_rejects_invalid_exposure_and_entry(self):
        for bad in ({"fixed_weight": "-0.1"}, {"fixed_weight": "1.1"},
                    {"fixed_weight": "nan"}, {"fixed_entry": "unknown"}):
            with self.subTest(bad=bad):
                algorithm = qc_loader_harness({"model": "fixed", **bad})
                with self.assertRaises(ValueError):
                    algorithm.initialize()

    def test_allocation_limits_and_summary_counts_are_not_raw_target_values(self):
        algorithm = qc_loader_harness({"model": "fixed"})
        algorithm.initialize()
        for target in (-0.3, 0.4, 1.7):
            algorithm._place_allocation(target, pd.Period("2020-01", freq="M"))
        submitted = [call.args[0][0].weight for call in algorithm.set_holdings.call_args_list]
        np.testing.assert_allclose(submitted, [0, 0.4, 0.99])
        self.assertEqual(algorithm.allocation_stats["cap"], 1)
        self.assertEqual(algorithm.allocation_stats["floor"], 1)
        self.assertAlmostEqual(algorithm.allocation_stats["sum"], 1.39)
        algorithm.on_end_of_algorithm()
        log = "\n".join(call.args[0] for call in algorithm.debug.call_args_list)
        self.assertIn("cap touches=1/3", log)
        self.assertNotIn("1.7", log)

    def test_prediction_quality_summary_keeps_common_sample_and_fallback_counts(self):
        algorithm = qc_loader_harness({"model": "fixed"})
        algorithm.initialize()
        algorithm.scored = [
            {"realized": 0.01, "mean": 0, "linear": 0.002,
             "bivariate": -0.01, "interaction": 0.003},
            {"realized": -0.01, "mean": 0, "linear": -0.002,
             "bivariate": 0.01, "interaction": -0.003}]
        algorithm.fit_stats["interaction"] = {"fits": 3, "fallback": 2, "stabilized": 1}
        algorithm.on_end_of_algorithm()
        runtime = {call.args[0]: call.args[1]
                   for call in algorithm.set_runtime_statistic.call_args_list}
        self.assertEqual(runtime["Forecast n"], "2")
        self.assertEqual(runtime["OOS R2 interaction"], "51.0000%")
        self.assertEqual(runtime["OOS R2 bivariate"], "-300.0000%")
        log = "\n".join(call.args[0] for call in algorithm.debug.call_args_list)
        self.assertIn("mean fallback=2/3", log)
        self.assertIn("VAR stabilized=1/3", log)

    def test_bellman_scalar_solution_and_discount_identity(self):
        for lam in (0, 3, 300, 10000):
            a, kappa, q = trade_parameters(3, lam, 0.02)
            if lam == 0:
                self.assertEqual((a, kappa, q), (0, 1, 0))
                continue
            beta = 1.02**(-1/12)
            self.assertAlmostEqual(a*a+(3+(1-beta)*lam/beta)*a-3*lam, 0, places=7)
            self.assertAlmostEqual(q, beta*(1-kappa), places=12)
            self.assertTrue(0 < kappa < 1)

    def test_quadratic_lift_against_conditional_monte_carlo(self):
        rng = np.random.default_rng(12)
        c = np.array([0.1, -0.07])
        A = np.array([[0.8, 0.15], [-0.1, 0.6]])
        omega = np.array([[0.04, 0.013], [0.013, 0.09]])
        state = np.array([0.7, -0.9])
        draws = c+state@A.T+rng.multivariate_normal([0, 0], omega, size=120000)
        expected = moment_transition(c, A, omega) @ lift(state)
        np.testing.assert_allclose(lift(draws).mean(axis=0), expected, atol=0.003)

    def test_resolvent_against_independent_ar1_formula(self):
        rx, rs, covariance = 0.96, 0.65, 0.012
        A = np.diag([rx, rs])
        omega = np.array([[0.03, covariance], [covariance, 0.08]])
        T = moment_transition(np.zeros(2), A, omega)
        _, _, q = trade_parameters()
        x, s = 0.4, -0.8
        averaged = (1-q)*np.linalg.solve(np.eye(6)-q*T, lift([x, s]))
        cross_weight = (1-q)/(1-q*rx*rs)
        expected_cross = cross_weight*x*s + covariance/(1-rx*rs)*(1-cross_weight)
        self.assertAlmostEqual(averaged[1], (1-q)*x/(1-q*rx), places=12)
        self.assertAlmostEqual(averaged[4], expected_cross, places=12)
        # Also compare the infinite sum to a long explicit forward iteration.
        total, state, mass = np.zeros(6), lift([x, s]), 1-q
        for _ in range(1000):
            total += mass*state
            mass *= q
            state = T @ state
        np.testing.assert_allclose(total, averaged, atol=1e-12)

    def test_release_time_and_old_revision_precedence(self):
        tape = ReleaseTape(pd.DataFrame({
            "period": ["2020-01", "2020-02", "2020-01", "2020-03"],
            "available_at": ["2020-02-03 10:00", "2020-03-02 10:00",
                             "2020-03-03 10:00", "2020-04-01 10:00"],
            "value": [49, 48, 99, 40]}))
        with self.assertRaises(ValueError):
            tape.asof("2020-02-03 09:59")
        self.assertEqual(tape.asof("2020-02-03 10:00")["value"], 49)
        self.assertEqual(tape.asof("2020-03-03 11:00")["value"], 48)
        self.assertEqual(tape.asof("2020-03-31 23:59")["value"], 48)
        self.assertEqual(tape.asof("2020-04-01 10:00")["value"], 40)

    def test_reject_impossible_release_dates(self):
        with self.assertRaises(ValueError):
            ReleaseTape(pd.DataFrame({"period": ["2020-03"],
                                     "available_at": ["2020-03-10"], "value": [1]}))

    def test_fred_delay_is_explicit(self):
        tape = ReleaseTape.from_fred_latest(
            "observation_date,CFNAI\n2020-01-01,-0.3\n2020-02-01,-0.7\n", "CFNAI")
        with self.assertRaises(ValueError):
            tape.asof("2020-02-29")
        self.assertEqual(tape.asof("2020-03-01")["value"], -0.3)

    def test_empty_and_html_payloads_have_clear_errors(self):
        for text in (None, "", "  \r\n\t", "\ufeff\n"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "FRED CFNAI: empty CSV response"):
                    ReleaseTape.from_fred_latest(text, "CFNAI")
        with self.assertRaisesRegex(ValueError, "HTML/XML"):
            ReleaseTape.from_fred_latest("<!doctype html><html>Denied</html>", "CFNAI")
        with self.assertRaisesRegex(ValueError, "no data rows"):
            ReleaseTape.from_fred_latest("observation_date,CFNAI\n", "CFNAI")
        with self.assertRaisesRegex(ValueError, "no valid observations"):
            ReleaseTape.from_fred_latest("observation_date,CFNAI\n2020-01-01,.\n", "CFNAI")

    def test_separate_csv_snapshots_cover_required_history(self):
        root = Path(__file__).parent/"examples"
        for count, name in ((715, "macro_cfnai_REVISED_DEMO.csv"),
                            (1113, "rf_tb3ms_REVISED_DEMO.csv")):
            raw = (root/name).read_text(encoding="utf-8")
            tape = ReleaseTape.from_csv(raw)
            self.assertEqual(len(tape.frame), count)
            for month in pd.period_range("1998-01", "2025-12", freq="M"):
                self.assertTrue(np.isfinite(tape.asof(month.end_time)["value"]))

    def test_qc_default_macro_loader_never_downloads(self):
        algorithm = qc_loader_harness()
        for key, series in (("macro_key", "CFNAI"), ("rf_key", "TB3MS")):
            tape = algorithm._load_release_tape(key, series)
            self.assertGreater(len(tape.frame), 240)
            self.assertTrue(np.isfinite(tape.asof("2019-12-31 23:59")["value"]))
        algorithm.download.assert_not_called()

    def test_qc_missing_csv_has_upload_instructions(self):
        algorithm = qc_loader_harness(files={})
        with self.assertRaisesRegex(ValueError, "Upload it to Object Store"):
            algorithm._load_release_tape("macro_key", "CFNAI")
        algorithm.download.assert_not_called()

    def test_qc_exact_key_and_project_prefixed_keys_are_supported(self):
        root = Path(__file__).parent/"examples"
        raw = (root/"macro_cfnai_REVISED_DEMO.csv").read_text(encoding="utf-8")
        algorithm = qc_loader_harness(files={"12345/macro_cfnai_REVISED_DEMO.csv": raw})
        tape = algorithm._load_release_tape("macro_key", "CFNAI")
        self.assertEqual(len(tape.frame), 715)
        algorithm = qc_loader_harness(
            {"macro_key": "custom/pmi.csv"},
            {"custom/pmi.csv": "period,available_at,value\n2019-09-01,2019-10-02 10:00,49.1\n"
                               "2019-10-01,2019-11-01 10:00,48.3\n"})
        tape = algorithm._load_release_tape("macro_key", "CFNAI")
        self.assertEqual(len(tape.frame), 2)
        self.assertEqual(tape.asof("2019-12-31")["value"], 48.3)

    def test_qc_explicit_csv_url_can_load_without_object_store(self):
        url = "https://example.org/my-pmi.csv"
        algorithm = qc_loader_harness({"macro_url": url}, {})
        algorithm.download = Mock(return_value=(
            "period,available_at,value\n2019-09-01,2019-10-02 10:00,49.1\n"))
        tape = algorithm._load_release_tape("macro_key", "CFNAI")
        self.assertEqual(tape.asof("2019-10-31")["value"], 49.1)
        algorithm.download.assert_called_once_with(url)
        algorithm.object_store.contains_key.assert_not_called()

    def test_qc_csv_url_rejects_empty_responses_and_local_paths(self):
        algorithm = qc_loader_harness({"macro_url": "https://example.org/empty.csv"}, {})
        algorithm.download = Mock(return_value="")
        with self.assertRaisesRegex(ValueError, "macro_url response: empty CSV response"):
            algorithm._load_release_tape("macro_key", "CFNAI")
        algorithm = qc_loader_harness({"macro_url": "file:///internal.csv"}, {})
        with self.assertRaisesRegex(ValueError, "direct HTTPS CSV URL"):
            algorithm._load_release_tape("macro_key", "CFNAI")
        algorithm.download.assert_not_called()

    def test_qc_explicit_object_store_data_do_not_silently_fall_back(self):
        algorithm = qc_loader_harness({"macro_key": "my-pmi"}, {"my-pmi": ""})
        with self.assertRaisesRegex(ValueError, "Object Store key my-pmi: empty CSV response"):
            algorithm._load_release_tape("macro_key", "CFNAI")
        algorithm = qc_loader_harness({"macro_key": "my-pmi"}, {})
        with self.assertRaisesRegex(ValueError, "Missing macro CSV: my-pmi"):
            algorithm._load_release_tape("macro_key", "CFNAI")
        algorithm.download.assert_not_called()

    def test_dividend_yield_and_total_return_no_double_count(self):
        dates = pd.period_range("2019-01", periods=15, freq="M").to_timestamp("M")
        prices = {date: 100.0 for date in dates}
        div = {date: 1.0 for date in dates}
        macro = ReleaseTape(pd.DataFrame({"period": dates,
                                          "available_at": dates+pd.Timedelta(hours=8),
                                          "value": np.arange(len(dates))}))
        rf = ReleaseTape.from_fred_latest(
            "observation_date,TB3MS\n2018-11-01,0\n2018-12-01,0\n"+
            "\n".join(f"{d:%Y-%m}-01,0" for d in dates)+"\n", "TB3MS")
        result = proxy_monthly_panel(prices, div, macro, rf, dates[-1])
        self.assertAlmostEqual(result["log_dp"].iloc[-1], np.log(0.12))
        self.assertAlmostEqual(result["total_return"].iloc[-1], 0.01)
        self.assertAlmostEqual(result["market_excess"].iloc[-1], 0.01)
        self.assertTrue(result["log_dp"].iloc[:11].isna().all())

    def test_mean_aim_is_mean_variance_portfolio(self):
        panel, tape = synthetic_panel()
        states = np.column_stack([panel["log_dp"], tape.frame["value"]])
        y = panel["market_excess"].to_numpy()
        model = TimingModel("mean").fit(states[:-1], states[1:], y[1:])
        expected = y[1:].mean()/(3*model.sigma[0, 0])
        self.assertAlmostEqual(model.aim(states[-1])[0], expected, places=10)

    def test_economic_sign_filter_falls_back_to_mean(self):
        rng = np.random.default_rng(14)
        X = rng.normal(size=(150, 2))
        y = 0.004-0.02*X[:, 0]+0.001*rng.normal(size=150)
        model = TimingModel("linear", economic_constraints=True).fit(X, X*0.8, y)
        self.assertTrue(model.constraint_active[0])
        self.assertAlmostEqual(model.forecast([10, -10])[0], y.mean(), places=12)

    def test_future_returns_cannot_change_past_forecasts(self):
        panel, tape = synthetic_panel(n=155)
        before, _ = run_research(panel, tape, window=120, economic_constraints=False)
        changed = panel.copy()
        changed.iloc[145:, changed.columns.get_loc("market_excess")] += 0.5
        changed.iloc[145:, changed.columns.get_loc("log_dp")] += 1.0
        future, _ = run_research(changed, tape, window=120, economic_constraints=False)
        # Includes the formation immediately BEFORE the first changed outcome.
        keep = before["formation"] <= str(panel.index[144].date())
        cols = ["forecast_market_excess", "weight_market_excess", "aim_market_excess"]
        np.testing.assert_allclose(before.loc[keep, cols], future.loc[keep, cols], atol=1e-12)

    def test_missing_month_is_not_silently_compressed(self):
        panel, tape = synthetic_panel()
        with self.assertRaises(ValueError):
            run_research(panel.drop(panel.index[100]), tape, window=120)

    def test_pca_hedge_and_frozen_future_data(self):
        rng = np.random.default_rng(31)
        panel, _ = synthetic_panel(n=190)
        m = panel["market_excess"].to_numpy()
        n, k = len(panel), 50
        R = rng.normal(scale=0.03, size=(n, k)) + m[:, None]*rng.normal(size=k)
        B = rng.normal(size=(n, k))
        panel["market_log_bm"] = rng.normal(size=n)
        r = pd.DataFrame(R, index=panel.index)
        b = pd.DataFrame(B, index=panel.index)
        p1, meta1 = add_pca(panel, r, b, initial=120)
        r2, b2 = r.copy(), b.copy()
        r2.iloc[150:] += 0.3
        b2.iloc[150:] += 5
        p2, meta2 = add_pca(panel, r2, b2, initial=120)
        np.testing.assert_allclose(meta1["weights"], meta2["weights"], atol=0)
        np.testing.assert_allclose(p1.iloc[:150], p2.iloc[:150], atol=0)
        builder = FrozenPCA().fit(R[:120], m[:120])
        pc, _ = builder.transform(R, B, m, panel["market_log_bm"])
        neutral_cov = np.cov(np.column_stack([m[:120], pc[:120]]), rowvar=False)[0, 1:]
        np.testing.assert_allclose(neutral_cov, 0, atol=1e-12)

    def test_readable_project_modules_meet_size_and_source_constraints(self):
        root = Path(__file__).parent
        banned_imports = {"zlib", "base64", "lzma", "bz2", "gzip", "marshal"}
        for name in ("main.py", "timing_core.py"):
            text = (root/name).read_text(encoding="utf-8")
            self.assertLessEqual(len(text), 32000)
            self.assertLessEqual(len(text.encode("utf-8")), 32000)
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse({alias.name.split(".")[0] for alias in node.names} & banned_imports)
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0], banned_imports)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec", "compile"})
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # No dataset-sized string payload in either project module.
                    self.assertLess(len(node.value), 1500)
            compile(text, str(root/name), "exec")
        self.assertFalse((root/"embedded_macro.py").exists())
        self.assertFalse((root/"build_main.py").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

