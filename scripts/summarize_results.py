"""Rebuild tables and CSVs from the archived QuantConnect exports, offline.

Uses only the standard library. --plots additionally requires matplotlib.
No market history is downloaded or inferred from equity charts.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def number(value, percent=False):
    if value is None or value == "":
        return None
    result = float(str(value).replace("%", "").replace("$", "").replace(",", "").strip())
    return result / 100 if percent else result


def points(data, chart, series):
    """Return UTC timestamp/value pairs, taking CLOSE for QC OHLC candles."""
    definition = data["charts"][chart]["series"][series]
    candles = definition.get("seriesType") == 2
    for point in definition["values"]:
        if not isinstance(point, list) or len(point) < 2:
            raise ValueError("Unexpected chart point format")
        if candles and len(point) != 5:
            raise ValueError("Expected [time,open,high,low,close] equity candle")
        yield int(point[0]), float(point[4] if candles else point[1])


def iso_utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def counter(text):
    if not text:
        return None, None
    numerator, denominator = text.split("/")
    return int(numerator), int(denominator)


def build(manifest):
    rows, provenance, datasets = [], [], {}
    for experiment in manifest["experiments"]:
        path = ROOT / experiment["raw_path"]
        data = read_json(path)
        datasets[experiment["id"]] = data
        stats, runtime = data["statistics"], data["runtimeStatistics"]
        allocation = list(points(data, "Allocation", "SPY target"))
        targets = [value for _, value in allocation]
        fallback, fits = counter(runtime.get("Mean fallback"))
        cap, cap_n = counter(runtime.get("Cap touches"))
        equity = list(points(data, "Strategy Equity", "Equity"))
        initial = number(stats["Start Equity"])
        ending = number(stats["End Equity"])
        net = number(stats["Net Profit"], percent=True)
        if abs(ending / initial - 1 - net) > 0.0000051:
            raise ValueError("Reported return and ending equity disagree: " + experiment["id"])
        if abs(equity[-1][1] - ending) > 0.011:
            raise ValueError("Final equity candle CLOSE disagrees: " + experiment["id"])
        if len(targets) != 72:
            raise ValueError("Expected 72 allocation observations")
        exported_average = number(runtime.get("Avg SPY target"), percent=True)
        average = statistics.fmean(targets)
        if exported_average is not None and abs(average-exported_average) > 0.000501:
            raise ValueError("Average allocation mismatch")
        eq_start = equity[0][1]
        series_rows = [{"timestamp_utc": iso_utc(t), "equity_close_usd": value,
                        "normalized_equity": value/eq_start} for t, value in equity]
        write_csv(ROOT / "results/derived" / (experiment["id"] + "_equity.csv"),
                  series_rows, ["timestamp_utc", "equity_close_usd", "normalized_equity"])
        write_csv(ROOT / "results/derived" / (experiment["id"] + "_allocation.csv"),
                  [{"timestamp_utc": iso_utc(t), "spy_target_fraction": value}
                   for t, value in allocation], ["timestamp_utc", "spy_target_fraction"])
        order_rows = []
        for order in data["orders"].values():
            symbol = order.get("symbol", {})
            order_rows.append({"id": order.get("id"), "created_time_utc": order.get("createdTime"),
                               "last_fill_time_utc": order.get("lastFillTime"),
                               "symbol": symbol.get("value"), "quantity": order.get("quantity"),
                               "price": order.get("price"), "status": order.get("status"),
                               "tag": order.get("tag")})
        write_csv(ROOT / "results/derived" / (experiment["id"] + "_orders.csv"), order_rows,
                  ["id", "created_time_utc", "last_fill_time_utc", "symbol", "quantity",
                   "price", "status", "tag"])
        write_csv(ROOT / "results/derived" / (experiment["id"] + "_drawdown.csv"),
                  [{"timestamp_utc": iso_utc(t), "drawdown_fraction": value/100}
                   for t, value in points(data, "Drawdown", "Equity Drawdown")],
                  ["timestamp_utc", "drawdown_fraction"])
        rows.append({
            "experiment_id": experiment["id"], "stage": experiment["stage"],
            "label": experiment["label"], "raw_path": experiment["raw_path"],
            "model": data["algorithmConfiguration"]["parameters"]["model"],
            "total_return_fraction": net, "cagr_fraction": number(stats["Compounding Annual Return"], True),
            "max_drawdown_fraction": number(stats["Drawdown"], True),
            "sharpe_qc": number(stats["Sharpe Ratio"]), "sortino_qc": number(stats["Sortino Ratio"]),
            "beta_qc": number(stats["Beta"]), "annual_volatility_fraction": number(stats["Annual Standard Deviation"]),
            "end_equity_usd": ending, "total_fees_usd": number(stats["Total Fees"]),
            "total_orders": int(stats["Total Orders"]), "allocation_count": len(targets),
            "avg_spy_target_fraction": average, "min_spy_target_fraction": min(targets),
            "max_spy_target_fraction": max(targets),
            "targets_at_099_from_chart": sum(value >= .99-1e-8 for value in targets),
            "targets_at_zero_from_chart": sum(abs(value) <= 1e-10 for value in targets),
            "runtime_cap_touches": cap, "runtime_cap_denominator": cap_n,
            "runtime_mean_fallbacks": fallback, "runtime_fit_count": fits,
            "forecast_n": int(runtime["Forecast n"]) if "Forecast n" in runtime else None,
            "oos_r2_linear_fraction": number(runtime.get("OOS R2 linear"), True),
            "oos_r2_bivariate_fraction": number(runtime.get("OOS R2 bivariate"), True),
            "oos_r2_interaction_fraction": number(runtime.get("OOS R2 interaction"), True),
            "lean_version": data.get("serverStatistics", {}).get("LEAN Version"),
            "cloud_run_start_utc": data.get("state", {}).get("StartTime"),
        })
        provenance.append({"experiment_id": experiment["id"],
                           "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "algorithm_configuration": data["algorithmConfiguration"],
                           "statistics": stats, "runtime_statistics": runtime,
                           "state": data.get("state"),
                           "lean_version": data.get("serverStatistics", {}).get("LEAN Version")})
    write_csv(ROOT / "results/summary.csv", rows, list(rows[0]))
    (ROOT / "results/exported_metadata.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Archived QuantConnect results", "",
             "Generated from original JSON exports by `python scripts/summarize_results.py`.", "",
             "| Run | Return | CAGR | Sharpe (QC) | Max drawdown | Avg SPY target | Fees (USD) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['label']} | {row['total_return_fraction']:.3%} | "
                     f"{row['cagr_fraction']:.3%} | {row['sharpe_qc']:.3f} | "
                     f"{row['max_drawdown_fraction']:.1%} | {row['avg_spy_target_fraction']:.2%} | "
                     f"{row['total_fees_usd']:.2f} |")
    lines += ["", "All runs: 2020-01-01 to 2025-12-31; USD 1,000,000 initial equity.",
              "C repeats the original constrained interaction configuration. It is not independent evidence.",
              "The second Snake export is byte-identical and excluded from the table.",
              "Average SPY target is the simple mean of 72 monthly chart targets, not daily actual exposure.",
              "Chart cap counts describe clipped values; runtime cap counts use pre-clipping targets.",
              "Sharpe, CAGR, drawdown and fees are copied from platform statistics, not re-estimated.",
              "Empty OOS/fallback fields in the first exports mean not exported, not zero.",
              "OOS R2 from the log screenshots is transcribed separately in evidence_transcription.json.",
              "No full quote history or forecast-by-month series is reconstructed from chart values.", ""]
    (ROOT / "results/summary.md").write_text("\n".join(lines), encoding="utf-8")
    benchmark_data = datasets[manifest["experiments"][0]["id"]]
    benchmark = list(points(benchmark_data, "Benchmark", "Benchmark"))
    write_csv(ROOT / "results/derived/benchmark_chart.csv",
              [{"timestamp_utc": iso_utc(t), "exported_chart_value": value,
                "normalized_chart_value": value/benchmark[0][1]} for t, value in benchmark],
              ["timestamp_utc", "exported_chart_value", "normalized_chart_value"])
    return datasets


def plots(manifest, datasets):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.dpi": 180})
    selected = [e for e in manifest["experiments"] if e["id"] != "diagnostic_C"]
    colors = ["#697586", "#176B87", "#C68033", "#734AA2", "#65A5AA", "#A79773", "#BB5361"]
    fig, axes = plt.subplots(3, 1, figsize=(12, 11), sharex=True,
                             gridspec_kw={"height_ratios": [1.3, 1, .9]})
    for e, color in zip(selected, colors):
        data = datasets[e["id"]]
        equity = list(points(data, "Strategy Equity", "Equity"))
        dates = [datetime.fromtimestamp(t, timezone.utc) for t, _ in equity]
        axes[0].plot(dates, [v/equity[0][1] for _, v in equity], lw=1.5, color=color, label=e["label"])
        allocation = list(points(data, "Allocation", "SPY target"))
        axes[1].step([datetime.fromtimestamp(t, timezone.utc) for t, _ in allocation],
                     [v for _, v in allocation], where="post", color=color, lw=1.1)
        dd = list(points(data, "Drawdown", "Equity Drawdown"))
        axes[2].plot([datetime.fromtimestamp(t, timezone.utc) for t, _ in dd],
                     [v/100 for _, v in dd], color=color, lw=1)
    axes[0].set_title("Nonlinear factor timing | archived QuantConnect runs, 2020-2025", loc="left", pad=12)
    axes[0].set_ylabel("Equity / initial equity")
    axes[0].legend(loc="upper left", ncol=2, fontsize=8, frameon=False)
    axes[1].set_ylabel("Monthly SPY target")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[2].set_ylabel("Exported drawdown")
    axes[2].yaxis.set_major_formatter(PercentFormatter(1))
    axes[2].xaxis.set_major_locator(mdates.YearLocator())
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for ax in axes:
        ax.grid(axis="y", alpha=.16)
        ax.margins(x=.01)
    fig.text(.08, .018, "Source: user-supplied JSON exports. Revised CFNAI proxy; no independent holdout. "
             "Targets are not actual daily exposures.", fontsize=8, color="#586370")
    fig.tight_layout(rect=[0, .035, 1, 1])
    fig.savefig(ROOT / "results/figures/overview.png")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()
    manifest = read_json(ROOT / "configs/experiments.json")
    datasets = build(manifest)
    if args.plots:
        plots(manifest, datasets)
    print(f"Rebuilt summaries and exported series for {len(datasets)} archived runs.")


if __name__ == "__main__":
    main()
