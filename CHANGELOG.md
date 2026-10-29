# Changelog

## 2026-10-29 — archival release

- Preserved the latest main.py, timing_core.py, paper_research.py and tests.py without changing strategy logic.
- Added all nine user-provided JSON downloads: four models, A/B/C and two byte-identical Snake files.
- Preserved all four user-provided log images, the reference report and the previous source bundle.
- Extracted exact parameter snapshots, source names, hashes, engine versions and cloud run timestamps.
- Added an experiment log, data inventory, reproducibility boundaries and QuantConnect/GitHub instructions.
- Added offline scripts for result summaries, chart series, orders, overview figure and archive verification.
- No new market-data download, parameter search, broker simulation or cloud backtest was executed during packaging.

## Earlier implementation stages — reconstructed record

External CSV handling replaced unreliable online loading. The final project split source into two readable
modules after size/encoded-source failures. Fixed 55% allocation controls and fit diagnostics were added,
followed by an economic-sign-filter ablation. Exact older source revisions are unavailable; only the latest
pre-archive source bundle is retained. See docs/EXPERIMENT_LOG_zh.md for evidence and chronology.
