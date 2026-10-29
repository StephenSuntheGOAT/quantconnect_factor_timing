# Archived QuantConnect results

Generated from original JSON exports by `python scripts/summarize_results.py`.

| Run | Return | CAGR | Sharpe (QC) | Max drawdown | Avg SPY target | Fees (USD) |
|---|---:|---:|---:|---:|---:|---:|
| Mean | 108.782% | 13.043% | 0.527 | 26.0% | 85.98% | 138.63 |
| Linear | 101.758% | 12.400% | 0.740 | 11.4% | 54.01% | 271.39 |
| Bivariate | 73.406% | 9.601% | 0.558 | 11.6% | 44.15% | 288.01 |
| Interaction (constrained) | 115.912% | 13.676% | 0.577 | 26.0% | 83.68% | 219.98 |
| A: fixed 55%, immediate | 73.080% | 9.566% | 0.435 | 19.2% | 55.00% | 156.88 |
| B: fixed 55%, gradual | 66.597% | 8.872% | 0.482 | 13.9% | 47.99% | 164.81 |
| C: interaction repeat | 115.912% | 13.676% | 0.577 | 26.0% | 83.68% | 219.98 |
| Interaction (no sign filter) | 68.023% | 9.027% | 0.543 | 11.5% | 38.43% | 277.45 |

All runs: 2020-01-01 to 2025-12-31; USD 1,000,000 initial equity.
C repeats the original constrained interaction configuration. It is not independent evidence.
The second Snake export is byte-identical and excluded from the table.
Average SPY target is the simple mean of 72 monthly chart targets, not daily actual exposure.
Chart cap counts describe clipped values; runtime cap counts use pre-clipping targets.
Sharpe, CAGR, drawdown and fees are copied from platform statistics, not re-estimated.
Empty OOS/fallback fields in the first exports mean not exported, not zero.
OOS R2 from the log screenshots is transcribed separately in evidence_transcription.json.
No full quote history or forecast-by-month series is reconstructed from chart values.
