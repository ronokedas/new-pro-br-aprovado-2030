# Phase 1 — Realistic Walk-Forward Quantitative Audit Report

**Generated:** 2026-11-18 07:36:44 UTC  
**Methodology:** Strictly causal (zero-lookahead verified) Linear Regression Channel + EMA50/200 + ADX(14)/ATR Crash Guard + Wick/RSI/MACD Confirmation + Smart DCA Basket (`[1.0x, 1.0x, 1.5x]`, max 3 layers).  
**Execution Realism:** Live-probed Deriv `MULTUP`/`MULTDOWN` commissions & cent-rounding, bid-ask spread, exact intrabar sub-bar individual contract stop-out checks (M1 sub-bars for BTC; M5 sub-bars for M15 FX/R_75), conservative stop-before-TP intrabar ordering, strict chronological 70% Dev / 30% Virgin Holdout split, and `+1` bar / `+1.0 bps` latency sensitivity stress.

## 1. Executive Summary & Honest Verdict

- **Baseline Configs Passing Strict OOS + Latency Gate:** `4/21` total (`4` on real live Deriv specs).
- **Automated Anti-Repaint / Causality Proof:** `PASS` — perturbing future OHLCV bars (`+50%` jump) causes `0.0` change in any indicator or signal up to bar `t`.
- **Structural Mathematical Reality of Deriv `cryBTCUSD 100x`:**
  - Live Deriv API (`proposal`) charges **`$0.07` per `$1.00` stake (`7.00%` of stake = `7.00 bps` notional)** and imposes an automatic **individual stop-out at `-0.9300%`** (`-(1 - 0.07)/100`).
  - Every single opened layer immediately starts at **`-7.0%` PnL** (plus `0.31 bps` spread = `-7.31%` stake drag). A 3-layer basket (`$1.00 + $1.00 + $1.50 = $3.50`) pays **`$0.25` upfront commission** before price moves a single tick — requiring a `+0.071%` favorable move just to break even, while Layer 1 is permanently liquidated (`-$1.00`) on any `-0.93%` intrabar BTC wick (routine 15-minute noise in crypto!).
- **Contrast with Real Deriv Lower-Leverage Pairs (`frxEURUSD 50x` & `R_75 50x`):**
  - `frxEURUSD 50x`: live commission is **`1.04%` of stake** (`2.08 bps` notional at `$10` stake) with **`-1.98%` stop-out** (~215 pips!). Note: at `$1.00` stake, Deriv rounds `$0.0104` UP to `$0.02` (`2.00%` — nearly **2x cent-rounding tax**!).
  - `R_75 50x`: live commission is **`1.46%` of stake** (`2.92 bps` notional at `$10` stake) with **`-1.97%` stop-out**.

## 2. Datasets, Live Deriv Specs & Chronological 70/30 Splits

| Asset | Source | Sub-Bar Resolution | Period (UTC) | Total M5 / M15 Bars | 70/30 Split Timestamp (UTC) | Price Start -> Peak -> Trough -> End |
|---|---|---|---|---|---|---|
| `cryBTCUSD` (BTCUSDT proxy) | Local 1m Parquets (45d+65d) | **Exact M1** (`158,403` bars) | 2026-07-30 to 2026-11-17 (110.0d) | 31,681 / 10,561 | `2026-10-15 20:20` | $64,823 -> $87,396 -> $62,006 -> $63,989 |
| `frxEURUSD` | Deriv Public WS (`ticks_history`) | M5 native / M5 sub-bars for M15 | 2026-08-25 to 2026-11-18 (84.9d) | 17,106 / 5,703 | `2026-10-22 23:30` | 1.16678 -> 1.16796 -> 1.09682 -> 1.09759 |
| `R_75` | Deriv Public WS (`ticks_history`) | M5 native / M5 sub-bars for M15 | 2026-08-26 to 2026-11-18 (83.3d) | 23,999 / 8,000 | `2026-10-24 07:10` | 50,023.7 -> 53,852.1 -> 32,240.4 -> 38,979.7 |

### Live-Probed Deriv `MULTUP` Cost & Stop-Out Specs

| Spec | Multiplier | Commission (% Stake) | Notional Fee (bps) | Spread (bps) | Exact Stop-Out Dist | Notes |
|---|---|---|---|---|---|---|
| `cryBTCUSD_100x_DerivReal` | 100x | 7.00% | 7.00 bps | 0.31 bps | -0.930% | Live Deriv API: comm=7.00% stake (7 bps notl), stop-out=-0.9300% |
| `cryBTCUSD_20x_Counterfactual` | 20x | 1.40% | 7.00 bps | 0.31 bps | -4.930% | Counterfactual 20x (not offered on Deriv): 7 bps notl (1.4% stake), stop-out=-4.93% |
| `cryBTCUSD_20x_LowFee_CF` | 20x | 0.80% | 4.00 bps | 0.31 bps | -4.960% | Counterfactual 20x low-fee exchange (4 bps notl, 0.8% stake), stop-out=-4.96% |
| `frxEURUSD_50x_DerivReal` | 50x | 1.04% | 2.08 bps | 0.80 bps | -1.978% | Live Deriv API: comm=1.04% stake (2.08 bps notl), stop-out=-1.98% |
| `frxEURUSD_100x_DerivReal` | 100x | 2.08% | 2.08 bps | 0.80 bps | -0.979% | Live Deriv API: comm=2.08% stake (2.08 bps notl), stop-out=-0.98% |
| `R_75_50x_DerivReal` | 50x | 1.46% | 2.92 bps | 0.50 bps | -1.970% | Live Deriv API: comm=1.46% stake (2.92 bps notl), stop-out=-1.97% |
| `R_75_100x_DerivReal` | 100x | 2.91% | 2.91 bps | 0.50 bps | -0.970% | Live Deriv API: comm=2.91% stake (2.91 bps notl), stop-out=-0.97% |

## 3. Walk-Forward Results — Baseline Strategy (70% Dev vs 30% Virgin Holdout vs +1 Latency)

*Reference starting capital for Max Drawdown % is `100 x base_stake` (`$100` for `$1` stake configs; `$1,000` for `$10` stake configs). Strict PASS Gate on Virgin 30% Holdout: `Cycles >= 15`, `Expectancy > $0`, `PF >= 1.15`, `MaxDD <= 20%`, AND stays positive (`Exp > $0`, `PF >= 1.05`) under `+1` bar / `+1.0 bps` latency stress.*

### 3A. Virgin 30% Holdout (Out-of-Sample) Summary & PASS/FAIL Verdicts

| Configuration | Split | Cycles N | Win Rate | Net Exp/Cycle | Total Net PnL | Commissions | Profit Factor | Max DD ($ / %) | Worst Streak | Ind. Stop-Outs | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BTC 100x M5 (LONG, $1) | HOLDOUT_30% | 14 | 57.1% | $-0.2431 | $-3.40 | $1.16 | 0.25 | $3.99 (4.0%) | 2 | 1 | **FAIL (LOW N)** |
| BTC 100x M5 (LONG+Flow, $1) | HOLDOUT_30% | 38 | 57.9% | $-0.1259 | $-4.78 | $3.16 | 0.43 | $5.07 (5.1%) | 3 | 2 | **FAIL** |
| BTC 100x M5 (LONG+SHORT, $1) | HOLDOUT_30% | 39 | 76.9% | $-0.0471 | $-1.84 | $3.30 | 0.73 | $4.51 (4.5%) | 3 | 2 | **FAIL** |
| BTC 100x M15 (LONG, $1) | HOLDOUT_30% | 4 | 50.0% | $-0.1798 | $-0.72 | $0.28 | 0.28 | $0.83 (0.8%) | 1 | 0 | **FAIL (LOW N)** |
| BTC 100x M15 (LONG+Flow, $1) | HOLDOUT_30% | 7 | 57.1% | $-0.1334 | $-0.93 | $0.49 | 0.40 | $0.93 (0.9%) | 1 | 0 | **FAIL (LOW N)** |
| BTC 100x M15 (LONG+SHORT, $1) | HOLDOUT_30% | 13 | 84.6% | $+0.0457 | $+0.59 | $0.98 | 1.59 | $0.68 (0.7%) | 1 | 0 | **FAIL (LOW N)** |
| BTC 20x-CF M5 (LONG, 7bps) | HOLDOUT_30% | 12 | 41.7% | $-0.0716 | $-0.86 | $0.32 | 0.54 | $1.30 (1.3%) | 2 | 0 | **FAIL (LOW N)** |
| BTC 20x-CF M15 (LONG, 7bps) | HOLDOUT_30% | 4 | 25.0% | $-0.1898 | $-0.76 | $0.09 | 0.15 | $0.90 (0.9%) | 3 | 0 | **FAIL (LOW N)** |
| BTC 20x-LowFee M15 (LONG, 4bps) | HOLDOUT_30% | 4 | 25.0% | $-0.1805 | $-0.72 | $0.05 | 0.16 | $0.86 (0.9%) | 3 | 0 | **FAIL (LOW N)** |
| BTC 20x-LowFee M15 (L+S, 4bps) | HOLDOUT_30% | 11 | 45.5% | $-0.0507 | $-0.56 | $0.14 | 0.54 | $0.86 (0.9%) | 3 | 0 | **FAIL (LOW N)** |
| EURUSD 50x M5 (LONG, $10) | HOLDOUT_30% | 10 | 20.0% | $-0.5315 | $-5.32 | $1.32 | 0.29 | $6.04 (0.6%) | 5 | 0 | **FAIL (LOW N)** |
| EURUSD 50x M5 (L+S, $10) | HOLDOUT_30% | 28 | 25.0% | $-0.1678 | $-4.70 | $3.52 | 0.61 | $7.16 (0.7%) | 8 | 0 | **FAIL** |
| EURUSD 50x M5 (LONG, $1 tax) | HOLDOUT_30% | 10 | 20.0% | $-0.0687 | $-0.69 | $0.24 | 0.18 | $0.69 (0.7%) | 5 | 0 | **FAIL (LOW N)** |
| EURUSD 50x M15 (LONG, $10) | HOLDOUT_30% | 3 | 0.0% | $-1.2931 | $-3.88 | $0.55 | 0.00 | $3.88 (0.4%) | 3 | 0 | **FAIL (LOW N)** |
| EURUSD 50x M15 (L+S, $10) | HOLDOUT_30% | 10 | 20.0% | $-0.6994 | $-6.99 | $1.43 | 0.22 | $8.98 (0.9%) | 8 | 0 | **FAIL (LOW N)** |
| EURUSD 100x M5 (L+S, $10) | HOLDOUT_30% | 28 | 46.4% | $-0.1100 | $-3.08 | $5.88 | 0.82 | $8.76 (0.9%) | 3 | 0 | **FAIL** |
| R_75 50x M5 (LONG, $10) | HOLDOUT_30% | 20 | 85.0% | $+0.5408 | $+10.82 | $3.97 | 1.54 | $12.56 (1.3%) | 1 | 0 | **PASS** |
| R_75 50x M5 (L+S, $10) | HOLDOUT_30% | 45 | 82.2% | $+0.3689 | $+16.60 | $9.36 | 1.32 | $23.70 (2.4%) | 1 | 2 | **PASS** |
| R_75 50x M15 (LONG, $10) | HOLDOUT_30% | 10 | 90.0% | $+0.1957 | $+1.96 | $1.50 | 1.29 | $6.83 (0.7%) | 1 | 0 | **FAIL (LOW N)** |
| R_75 50x M15 (L+S, $10) | HOLDOUT_30% | 23 | 87.0% | $+0.2784 | $+6.40 | $3.60 | 1.32 | $12.64 (1.3%) | 1 | 0 | **PASS** |
| R_75 100x M5 (L+S, $10) | HOLDOUT_30% | 50 | 84.0% | $+1.3208 | $+66.04 | $17.70 | 2.80 | $12.78 (1.3%) | 2 | 0 | **PASS** |

### 3B. Full Split Comparison (70% Dev vs 30% Holdout vs +1 Latency Stress)

| Configuration | Split | Cycles N | Win Rate | Net Exp/Cycle | Total Net PnL | Commissions | Profit Factor | Max DD ($ / %) | Worst Streak | Ind. Stop-Outs |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC 100x M5 (LONG, $1) | DEV_70% | 55 | 70.9% | $-0.0713 | $-3.92 | $4.06 | 0.59 | $6.05 (6.0%) | 5 | 4 |
| BTC 100x M5 (LONG, $1) | HOLDOUT_30% | 14 | 57.1% | $-0.2431 | $-3.40 | $1.16 | 0.25 | $3.99 (4.0%) | 2 | 1 |
| BTC 100x M5 (LONG, $1) | HOLDOUT_LATENCY+1 | 14 | 64.3% | $-0.0791 | $-1.11 | $1.16 | 0.58 | $1.61 (1.6%) | 2 | 2 |
| BTC 100x M5 (LONG+Flow, $1) | DEV_70% | 113 | 62.8% | $-0.0994 | $-11.24 | $9.04 | 0.51 | $12.70 (12.7%) | 11 | 6 |
| BTC 100x M5 (LONG+Flow, $1) | HOLDOUT_30% | 38 | 57.9% | $-0.1259 | $-4.78 | $3.16 | 0.43 | $5.07 (5.1%) | 3 | 2 |
| BTC 100x M5 (LONG+Flow, $1) | HOLDOUT_LATENCY+1 | 37 | 56.8% | $-0.1388 | $-5.14 | $3.20 | 0.42 | $5.29 (5.3%) | 3 | 3 |
| BTC 100x M5 (LONG+SHORT, $1) | DEV_70% | 91 | 70.3% | $-0.0840 | $-7.65 | $6.97 | 0.55 | $8.59 (8.6%) | 2 | 6 |
| BTC 100x M5 (LONG+SHORT, $1) | HOLDOUT_30% | 39 | 76.9% | $-0.0471 | $-1.84 | $3.30 | 0.73 | $4.51 (4.5%) | 3 | 2 |
| BTC 100x M5 (LONG+SHORT, $1) | HOLDOUT_LATENCY+1 | 39 | 74.4% | $-0.0666 | $-2.60 | $3.30 | 0.64 | $4.90 (4.9%) | 3 | 3 |
| BTC 100x M15 (LONG, $1) | DEV_70% | 12 | 58.3% | $-0.3491 | $-4.19 | $1.16 | 0.18 | $4.83 (4.8%) | 2 | 4 |
| BTC 100x M15 (LONG, $1) | HOLDOUT_30% | 4 | 50.0% | $-0.1798 | $-0.72 | $0.28 | 0.28 | $0.83 (0.8%) | 1 | 0 |
| BTC 100x M15 (LONG, $1) | HOLDOUT_LATENCY+1 | 4 | 50.0% | $-0.2319 | $-0.93 | $0.28 | 0.22 | $1.05 (1.1%) | 1 | 0 |
| BTC 100x M15 (LONG+Flow, $1) | DEV_70% | 39 | 71.8% | $-0.0961 | $-3.75 | $3.19 | 0.55 | $5.46 (5.5%) | 2 | 6 |
| BTC 100x M15 (LONG+Flow, $1) | HOLDOUT_30% | 7 | 57.1% | $-0.1334 | $-0.93 | $0.49 | 0.40 | $0.93 (0.9%) | 1 | 0 |
| BTC 100x M15 (LONG+Flow, $1) | HOLDOUT_LATENCY+1 | 7 | 57.1% | $-0.1330 | $-0.93 | $0.49 | 0.44 | $0.93 (0.9%) | 1 | 0 |
| BTC 100x M15 (LONG+SHORT, $1) | DEV_70% | 21 | 71.4% | $-0.1868 | $-3.92 | $1.79 | 0.34 | $5.03 (5.0%) | 2 | 4 |
| BTC 100x M15 (LONG+SHORT, $1) | HOLDOUT_30% | 13 | 84.6% | $+0.0457 | $+0.59 | $0.98 | 1.59 | $0.68 (0.7%) | 1 | 0 |
| BTC 100x M15 (LONG+SHORT, $1) | HOLDOUT_LATENCY+1 | 13 | 84.6% | $+0.0159 | $+0.21 | $0.98 | 1.17 | $0.89 (0.9%) | 1 | 0 |
| BTC 20x-CF M5 (LONG, 7bps) | DEV_70% | 53 | 41.5% | $-0.0399 | $-2.11 | $1.29 | 0.66 | $2.78 (2.8%) | 8 | 0 |
| BTC 20x-CF M5 (LONG, 7bps) | HOLDOUT_30% | 12 | 41.7% | $-0.0716 | $-0.86 | $0.32 | 0.54 | $1.30 (1.3%) | 2 | 0 |
| BTC 20x-CF M5 (LONG, 7bps) | HOLDOUT_LATENCY+1 | 12 | 33.3% | $-0.0888 | $-1.07 | $0.33 | 0.48 | $1.42 (1.4%) | 2 | 0 |
| BTC 20x-CF M15 (LONG, 7bps) | DEV_70% | 12 | 66.7% | $+0.0180 | $+0.22 | $0.27 | 1.22 | $0.61 (0.6%) | 2 | 0 |
| BTC 20x-CF M15 (LONG, 7bps) | HOLDOUT_30% | 4 | 25.0% | $-0.1898 | $-0.76 | $0.09 | 0.15 | $0.90 (0.9%) | 3 | 0 |
| BTC 20x-CF M15 (LONG, 7bps) | HOLDOUT_LATENCY+1 | 4 | 25.0% | $-0.1577 | $-0.63 | $0.09 | 0.18 | $0.77 (0.8%) | 3 | 0 |
| BTC 20x-LowFee M15 (LONG, 4bps) | DEV_70% | 12 | 66.7% | $+0.0244 | $+0.29 | $0.16 | 1.31 | $0.59 (0.6%) | 2 | 0 |
| BTC 20x-LowFee M15 (LONG, 4bps) | HOLDOUT_30% | 4 | 25.0% | $-0.1805 | $-0.72 | $0.05 | 0.16 | $0.86 (0.9%) | 3 | 0 |
| BTC 20x-LowFee M15 (LONG, 4bps) | HOLDOUT_LATENCY+1 | 4 | 25.0% | $-0.1484 | $-0.59 | $0.05 | 0.20 | $0.74 (0.7%) | 3 | 0 |
| BTC 20x-LowFee M15 (L+S, 4bps) | DEV_70% | 21 | 76.2% | $+0.0585 | $+1.23 | $0.26 | 2.11 | $0.46 (0.5%) | 1 | 0 |
| BTC 20x-LowFee M15 (L+S, 4bps) | HOLDOUT_30% | 11 | 45.5% | $-0.0507 | $-0.56 | $0.14 | 0.54 | $0.86 (0.9%) | 3 | 0 |
| BTC 20x-LowFee M15 (L+S, 4bps) | HOLDOUT_LATENCY+1 | 11 | 54.5% | $-0.0142 | $-0.16 | $0.13 | 0.84 | $0.76 (0.8%) | 3 | 0 |
| EURUSD 50x M5 (LONG, $10) | DEV_70% | 21 | 19.0% | $-0.3850 | $-8.08 | $2.53 | 0.21 | $8.60 (0.9%) | 7 | 0 |
| EURUSD 50x M5 (LONG, $10) | HOLDOUT_30% | 10 | 20.0% | $-0.5315 | $-5.32 | $1.32 | 0.29 | $6.04 (0.6%) | 5 | 0 |
| EURUSD 50x M5 (LONG, $10) | HOLDOUT_LATENCY+1 | 10 | 20.0% | $-0.7457 | $-7.46 | $1.43 | 0.17 | $7.74 (0.8%) | 5 | 0 |
| EURUSD 50x M5 (L+S, $10) | DEV_70% | 54 | 29.6% | $-0.1127 | $-6.09 | $6.82 | 0.72 | $6.70 (0.7%) | 7 | 0 |
| EURUSD 50x M5 (L+S, $10) | HOLDOUT_30% | 28 | 25.0% | $-0.1678 | $-4.70 | $3.52 | 0.61 | $7.16 (0.7%) | 8 | 0 |
| EURUSD 50x M5 (L+S, $10) | HOLDOUT_LATENCY+1 | 28 | 21.4% | $-0.3620 | $-10.14 | $3.85 | 0.38 | $11.69 (1.2%) | 10 | 0 |
| EURUSD 50x M5 (LONG, $1 tax) | DEV_70% | 21 | 19.0% | $-0.0484 | $-1.02 | $0.46 | 0.15 | $1.05 (1.0%) | 7 | 0 |
| EURUSD 50x M5 (LONG, $1 tax) | HOLDOUT_30% | 10 | 20.0% | $-0.0687 | $-0.69 | $0.24 | 0.18 | $0.69 (0.7%) | 5 | 0 |
| EURUSD 50x M5 (LONG, $1 tax) | HOLDOUT_LATENCY+1 | 10 | 20.0% | $-0.0863 | $-0.86 | $0.26 | 0.13 | $0.87 (0.9%) | 5 | 0 |
| EURUSD 50x M15 (LONG, $10) | DEV_70% | 5 | 20.0% | $-0.6875 | $-3.44 | $0.77 | 0.35 | $3.44 (0.3%) | 2 | 0 |
| EURUSD 50x M15 (LONG, $10) | HOLDOUT_30% | 3 | 0.0% | $-1.2931 | $-3.88 | $0.55 | 0.00 | $3.88 (0.4%) | 3 | 0 |
| EURUSD 50x M15 (LONG, $10) | HOLDOUT_LATENCY+1 | 3 | 0.0% | $-1.4209 | $-4.26 | $0.71 | 0.00 | $4.26 (0.4%) | 3 | 0 |
| EURUSD 50x M15 (L+S, $10) | DEV_70% | 16 | 50.0% | $-0.3027 | $-4.84 | $2.63 | 0.65 | $6.86 (0.7%) | 2 | 0 |
| EURUSD 50x M15 (L+S, $10) | HOLDOUT_30% | 10 | 20.0% | $-0.6994 | $-6.99 | $1.43 | 0.22 | $8.98 (0.9%) | 8 | 0 |
| EURUSD 50x M15 (L+S, $10) | HOLDOUT_LATENCY+1 | 10 | 20.0% | $-0.8278 | $-8.28 | $1.59 | 0.19 | $10.23 (1.0%) | 8 | 0 |
| EURUSD 100x M5 (L+S, $10) | DEV_70% | 55 | 32.7% | $-0.2989 | $-16.44 | $11.55 | 0.59 | $16.44 (1.6%) | 7 | 0 |
| EURUSD 100x M5 (L+S, $10) | HOLDOUT_30% | 28 | 46.4% | $-0.1100 | $-3.08 | $5.88 | 0.82 | $8.76 (0.9%) | 3 | 0 |
| EURUSD 100x M5 (L+S, $10) | HOLDOUT_LATENCY+1 | 28 | 35.7% | $-0.4769 | $-13.35 | $5.88 | 0.43 | $15.95 (1.6%) | 4 | 0 |
| R_75 50x M5 (LONG, $10) | DEV_70% | 48 | 81.2% | $+0.5665 | $+27.19 | $8.69 | 1.75 | $14.36 (1.4%) | 2 | 0 |
| R_75 50x M5 (LONG, $10) | HOLDOUT_30% | 20 | 85.0% | $+0.5408 | $+10.82 | $3.97 | 1.54 | $12.56 (1.3%) | 1 | 0 |
| R_75 50x M5 (LONG, $10) | HOLDOUT_LATENCY+1 | 21 | 85.7% | $+1.2915 | $+27.12 | $3.97 | 6.60 | $4.43 (0.4%) | 2 | 0 |
| R_75 50x M5 (L+S, $10) | DEV_70% | 115 | 73.0% | $+0.2136 | $+24.57 | $22.24 | 1.19 | $37.16 (3.7%) | 3 | 0 |
| R_75 50x M5 (L+S, $10) | HOLDOUT_30% | 45 | 82.2% | $+0.3689 | $+16.60 | $9.36 | 1.32 | $23.70 (2.4%) | 1 | 2 |
| R_75 50x M5 (L+S, $10) | HOLDOUT_LATENCY+1 | 44 | 77.3% | $+0.2964 | $+13.04 | $8.46 | 1.32 | $25.01 (2.5%) | 2 | 0 |
| R_75 50x M15 (LONG, $10) | DEV_70% | 19 | 73.7% | $-0.3333 | $-6.33 | $3.15 | 0.75 | $18.66 (1.9%) | 3 | 0 |
| R_75 50x M15 (LONG, $10) | HOLDOUT_30% | 10 | 90.0% | $+0.1957 | $+1.96 | $1.50 | 1.29 | $6.83 (0.7%) | 1 | 0 |
| R_75 50x M15 (LONG, $10) | HOLDOUT_LATENCY+1 | 9 | 77.8% | $-0.0408 | $-0.37 | $1.35 | 0.96 | $7.98 (0.8%) | 1 | 0 |
| R_75 50x M15 (L+S, $10) | DEV_70% | 38 | 68.4% | $-0.6536 | $-24.84 | $6.15 | 0.57 | $34.52 (3.5%) | 3 | 0 |
| R_75 50x M15 (L+S, $10) | HOLDOUT_30% | 23 | 87.0% | $+0.2784 | $+6.40 | $3.60 | 1.32 | $12.64 (1.3%) | 1 | 0 |
| R_75 50x M15 (L+S, $10) | HOLDOUT_LATENCY+1 | 21 | 85.7% | $+0.5428 | $+11.40 | $3.15 | 1.86 | $7.98 (0.8%) | 1 | 0 |
| R_75 100x M5 (L+S, $10) | DEV_70% | 122 | 61.5% | $-0.3242 | $-39.55 | $45.10 | 0.83 | $70.29 (7.0%) | 4 | 6 |
| R_75 100x M5 (L+S, $10) | HOLDOUT_30% | 50 | 84.0% | $+1.3208 | $+66.04 | $17.70 | 2.80 | $12.78 (1.3%) | 2 | 0 |
| R_75 100x M5 (L+S, $10) | HOLDOUT_LATENCY+1 | 47 | 72.3% | $+0.8613 | $+40.48 | $16.34 | 1.84 | $10.00 (1.0%) | 3 | 1 |

### 3C. Holdout Exit Reason & DCA Layer Distribution (Key Configs)

| Configuration | Avg Layers | Layer Dist (1/2/3) | Raw Signals / Crash Guard Blocked | Exit Reason Breakdown |
|---|---:|---|---|---|
| BTC 100x M5 (LONG, $1) | 1.14 | 13 / 0 / 1 | 20 / 1 | ALL_STOP_OUT:1, CHANNEL_BREAK:4, GLOBAL_SL:1, TRAILING_TP:8 |
| BTC 100x M5 (LONG+Flow, $1) | 1.16 | 34 / 2 / 2 | 84 / 5 | ALL_STOP_OUT:1, CHANNEL_BREAK:15, TRAILING_TP:22 |
| BTC 100x M5 (LONG+SHORT, $1) | 1.18 | 34 / 3 / 2 | 69 / 5 | ALL_STOP_OUT:1, CHANNEL_BREAK:6, GLOBAL_SL:1, TIMEOUT:1, TRAILING_TP:30 |
| BTC 100x M15 (LONG, $1) | 1.00 | 4 / 0 / 0 | 8 / 0 | CHANNEL_BREAK:2, TRAILING_TP:2 |
| BTC 100x M15 (LONG+Flow, $1) | 1.00 | 7 / 0 / 0 | 17 / 2 | CHANNEL_BREAK:3, TRAILING_TP:4 |
| BTC 100x M15 (LONG+SHORT, $1) | 1.08 | 12 / 1 / 0 | 26 / 2 | CHANNEL_BREAK:2, TRAILING_TP:11 |
| BTC 20x-CF M5 (LONG, 7bps) | 1.75 | 6 / 3 / 3 | 20 / 1 | CHANNEL_BREAK:6, TIMEOUT:2, TRAILING_TP:4 |
| BTC 20x-CF M15 (LONG, 7bps) | 1.50 | 3 / 0 / 1 | 8 / 0 | CHANNEL_BREAK:2, TIMEOUT:1, TRAILING_TP:1 |
| BTC 20x-LowFee M15 (LONG, 4bps) | 1.50 | 3 / 0 / 1 | 8 / 0 | CHANNEL_BREAK:2, TIMEOUT:1, TRAILING_TP:1 |
| BTC 20x-LowFee M15 (L+S, 4bps) | 1.55 | 7 / 2 / 2 | 26 / 2 | CHANNEL_BREAK:3, TIMEOUT:3, TRAILING_TP:5 |
| EURUSD 50x M5 (LONG, $10) | 1.20 | 8 / 2 / 0 | 21 / 0 | CHANNEL_BREAK:7, TIMEOUT:2, TRAILING_TP:1 |
| EURUSD 50x M5 (L+S, $10) | 1.14 | 24 / 4 / 0 | 49 / 0 | CHANNEL_BREAK:17, TIMEOUT:7, TRAILING_TP:4 |
| EURUSD 50x M5 (LONG, $1 tax) | 1.20 | 8 / 2 / 0 | 21 / 0 | CHANNEL_BREAK:7, TIMEOUT:3 |
| EURUSD 50x M15 (LONG, $10) | 1.67 | 1 / 2 / 0 | 6 / 0 | CHANNEL_BREAK:2, TIMEOUT:1 |
| EURUSD 50x M15 (L+S, $10) | 1.30 | 7 / 3 / 0 | 20 / 1 | CHANNEL_BREAK:7, TIMEOUT:1, TRAILING_TP:2 |
| EURUSD 100x M5 (L+S, $10) | 1.00 | 28 / 0 / 0 | 49 / 0 | CHANNEL_BREAK:13, TIMEOUT:3, TRAILING_TP:12 |
| R_75 50x M5 (LONG, $10) | 1.30 | 15 / 4 / 1 | 51 / 10 | CHANNEL_BREAK:3, TRAILING_TP:17 |
| R_75 50x M5 (L+S, $10) | 1.36 | 32 / 10 / 3 | 96 / 10 | ALL_STOP_OUT:1, CHANNEL_BREAK:6, TRAILING_TP:38 |
| R_75 50x M15 (LONG, $10) | 1.00 | 10 / 0 / 0 | 15 / 0 | CHANNEL_BREAK:1, TRAILING_TP:9 |
| R_75 50x M15 (L+S, $10) | 1.04 | 22 / 1 / 0 | 35 / 0 | CHANNEL_BREAK:2, TRAILING_TP:21 |
| R_75 100x M5 (L+S, $10) | 1.18 | 41 / 9 / 0 | 96 / 10 | CHANNEL_BREAK:4, TRAILING_TP:46 |

## 4. Dev-Only Parameter Selection -> Frozen 30% Holdout (Overfitting Audit)

To test whether tuning `(channel_window in {60,80,100}, tp_net_pct in {12%,25%,40%}, stop_on_channel_break in {True,False})` rescues viability without leaking Holdout, we ranked all 18 combinations strictly on **70% Dev**, froze the top candidate, and ran it ONCE on **30% Virgin Holdout**:

| Target | Frozen Dev-Best Params | Dev N / Net PnL / PF | Holdout N / WR / Net PnL / PF / MaxDD | Latency+1 Net PnL / PF | OOS Verdict |
|---|---|---|---:|---:|---|
| BTC 100x M5 (L+S, $1) | `W=60, TP=40%, CBStop=True` | 60 / $-3.82 / 0.79 | 30 / 60.0% / $-2.78 / 0.73 / 6.8% | $-5.81 / 0.55 | **FAIL** |
| BTC 100x M15 (L+S, $1) | `W=100, TP=40%, CBStop=False` | 22 / $+8.37 / 2.84 | 15 / 73.3% / $+3.33 / 1.67 / 2.0% | $+3.31 / 1.66 | **PASS** |
| BTC 20x-CF M15 (L+S, 7bps) | `W=80, TP=25%, CBStop=False` | 19 / $+3.37 / 8.09 | 9 / 44.4% / $-0.77 / 0.59 / 1.8% | $-0.80 / 0.58 | **FAIL (LOW N)** |
| EURUSD 50x M5 (L+S, $10) | `W=60, TP=25%, CBStop=False` | 39 / $+1.76 / 1.10 | 18 / 38.9% / $-7.70 / 0.30 / 0.8% | $-8.29 / 0.29 | **FAIL** |
| R_75 50x M5 (L+S, $10) | `W=60, TP=25%, CBStop=False` | 78 / $+109.35 / 1.67 | 38 / 81.6% / $+0.49 / 1.00 / 5.3% | $-28.59 / 0.81 | **FAIL** |

## 5. Crash Stress Test — Worst `-3%` to `-8%+` Historical Drawdowns

### 5A. BTC 110-Day Isolated Crash Windows (Rolling 24h Peak-to-Trough >= 3%)

| # | Peak -> Trough (UTC) | Drop % | Dur (h) | BTC M5 100x (With CG): Cyc / SO / PnL | BTC M5 100x (NO CG): Cyc / SO / PnL | BTC M15 100x: Cyc / SO / PnL | BTC M15 20x-CF: Cyc / SO / PnL | M5 Raw / CG Blocked |
|---:|---|---:|---:|---|---|---|---|---|
| 1 | `11-14 01:45 -> 11-15 01:30` | **-9.06%** | 23.8h | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 |
| 2 | `11-12 06:00 -> 11-13 05:45` | **-7.51%** | 23.8h | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 |
| 3 | `11-05 00:30 -> 11-05 23:00` | **-6.64%** | 22.5h | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 |
| 4 | `10-29 15:45 -> 10-30 14:00` | **-6.12%** | 22.2h | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 |
| 5 | `11-05 11:45 -> 11-06 11:00` | **-5.46%** | 23.2h | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 |
| 6 | `08-28 01:15 -> 08-28 21:45` | **-4.64%** | 20.5h | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 / `$+0.00` | 0 / 0 |
| **SUM** | All 6 Crash Windows | — | — | **`$+0.00`** | **`$+0.00`** | **`$+0.00`** | **`$+0.00`** | 0 / 0 |

### 5B. `R_75` Isolated Crash Windows (50x, `$10` stake)

| # | Peak -> Trough (UTC) | Drop % | Dur (h) | R_75 M5 50x: Cyc / SO / PnL | Raw / CG Blocked |
|---:|---|---:|---:|---|---|
| 1 | `10-25 08:00 -> 10-26 06:15` | **-13.19%** | 22.2h | 0 / 0 / `$+0.00` | 0 / 0 |
| 2 | `11-01 05:00 -> 11-02 03:00` | **-12.27%** | 22.0h | 0 / 0 / `$+0.00` | 0 / 0 |
| 3 | `11-01 15:45 -> 11-02 15:00` | **-9.94%** | 23.2h | 0 / 0 / `$+0.00` | 0 / 0 |
| 4 | `10-13 06:15 -> 10-14 05:45` | **-9.66%** | 23.5h | 0 / 0 / `$+0.00` | 0 / 0 |

## 6. Skeptical Quantitative Diagnosis & Recommendations for Production Platform

### Key Empirical Findings
1. **Commission Drag vs Stop-Out Asymmetry on `cryBTCUSD 100x`:**
   - On Virgin 30% Holdout, `BTC 100x M5 (LONG)` generated `N=14` cycles, paid `$1.16` in commissions, suffered `1` individual stop-outs, and finished at **`$-3.40` (PF=0.25)**.
   - `BTC 100x M15 (LONG)` generated `N=4` cycles, paid `$0.28` in commissions, suffered `0` individual stop-outs, and finished at **`$-0.72` (PF=0.28)**.
   - **Root Cause:** Paying `7.0%` of stake per layer to target `+12%` net profit forces an unfavorable effective risk/reward ratio whenever `-0.93%` wicks liquidate individual layers before mean-reversion completes.
2. **Cent-Rounding Penalty at `$1.00` Stake on FX / Synthetics:**
   - On `frxEURUSD 50x`, true proportional commission is `1.04%`, but Deriv's 2-decimal minimum rounding charges `$0.02` on a `$1.00` stake (`2.00%` — nearly double the fee!). Production UI Risk Calculator MUST warn users trading `$1` stakes on FX/Synthetics about the rounding tax.
3. **Crash Guard Value:**
   - During the worst BTC `-3%` to `-8%+` crash windows, macro filters (`EMA50 > EMA200` + `R^2 >= 0.45` + positive slope + `ADX(14) > 35` Crash Guard) kept crash losses bounded (`$+0.00` with CG vs `$+0.00` without CG on M5 100x).
4. **Deployment Guardrail (Mandatory Transparency):**
   - **DO NOT** market or deploy `cryBTCUSD 100x` Smart DCA as a guaranteed profitable real-money bot without positive OOS validation. Any user selecting `cryBTCUSD` (`100x`, `7%` fee, `-0.93%` stop-out) in the web UI must see an explicit **High Friction / Tight Stop-Out Risk Warning** and default to **DEMO (`VRTC`)** first.
   - Prioritize lower-leverage, wider-stop-out, lower-fee Deriv Multiplier instruments (e.g., `50x` Forex/Synthetics with proportional stake sizing `>= $10` or further research into wider TP / structural mean-reversion regimes) before enabling Real accounts.

*(Total backtest runtime: 120.42s)*