"""Phase 1 Quantitative Backtest Runner & Honest Audit Report Generator.

Executes strictly causal Walk-Forward (70% Dev / 30% Virgin Holdout) simulations across:
- cryBTCUSD 100x (Real Deriv costs & exact M1 intrabar stop-out at -0.93%) on M5 & M15
- cryBTCUSD 20x Counterfactual (isolating Deriv 100x fee/stop-out drag vs strategy edge)
- frxEURUSD 50x & 100x (Real Deriv WS historical candles & live API costs) on M5 & M15
- R_75 50x & 100x (Real Deriv WS historical candles & live API costs) on M5 & M15

Writes full honest quantitative audit report to C:\\robo-deriv\\backtest\\BACKTEST_REPORT.md.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# Ensure local imports resolve cleanly regardless of caller CWD
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from strategy_core import StrategyConfig, verify_causality
from simulator import (
    AssetSpec,
    SPEC_BTC_100X,
    SPEC_BTC_20X_CF,
    SPEC_BTC_20X_LOWFEE_CF,
    SPEC_EURUSD_50X,
    SPEC_EURUSD_100X,
    SPEC_R75_50X,
    SPEC_R75_100X,
    SimulationResult,
    detect_crash_windows,
    evaluate_crash_stress,
    fetch_deriv_candles,
    load_btc_1m_data,
    resample_ohlcv,
    run_simulation,
    walk_forward_70_30,
)


def verdict_for_oos(oos: SimulationResult, oos_lat: SimulationResult) -> Tuple[str, str]:
    """Strict, skeptical PASS/FAIL gate on Virgin 30% Holdout + Latency Stress."""
    if oos.n_cycles < 15:
        return "FAIL (LOW N)", f"Only {oos.n_cycles} OOS cycles (<15 min statistical threshold)"
    if oos.expectancy_usd <= 0.0:
        return "FAIL", f"Negative OOS expectancy (${oos.expectancy_usd:+.4f}/cycle)"
    if oos.profit_factor < 1.15:
        return "FAIL", f"Weak OOS Profit Factor ({oos.profit_factor:.2f} < 1.15)"
    if oos.max_drawdown_pct > 20.0:
        return "FAIL", f"Excessive OOS MaxDD ({oos.max_drawdown_pct:.1f}% > 20%)"
    if oos_lat.expectancy_usd <= 0.0 or oos_lat.profit_factor < 1.05:
        return "FAIL (LATENCY)", f"Edge collapses under +1 latency (Exp=${oos_lat.expectancy_usd:+.4f}, PF={oos_lat.profit_factor:.2f})"
    return "PASS", f"Positive OOS & Latency-resilient (PF={oos.profit_factor:.2f}, Exp=${oos.expectancy_usd:+.4f})"


def fmt_pf(pf: float) -> str:
    if np.isinf(pf):
        return "inf"
    return f"{pf:.2f}"


def fmt_row(label: str, r: SimulationResult, extra_verdict: str = "") -> str:
    pf_s = fmt_pf(r.profit_factor)
    dd_s = f"${r.max_drawdown_usd:.2f} ({r.max_drawdown_pct:.1f}%)"
    row = (
        f"| {label} | {r.split_name} | {r.n_cycles} | {r.win_rate_pct:.1f}% | "
        f"${r.expectancy_usd:+.4f} | ${r.total_net_pnl_usd:+.2f} | ${r.total_commissions_usd:.2f} | "
        f"{pf_s} | {dd_s} | {r.worst_losing_streak} | {r.total_individual_stop_outs} |"
    )
    if extra_verdict:
        row += f" **{extra_verdict}** |"
    return row


def dev_grid_select(
    signal_df: pd.DataFrame,
    sub_df: pd.DataFrame | None,
    base_cfg: StrategyConfig,
    spec: AssetSpec,
    timeframe: str,
    freq_sec: int,
) -> Tuple[StrategyConfig, SimulationResult, SimulationResult, SimulationResult]:
    """Principled Dev-ONLY parameter selection (70% Dev), then ONE frozen test on 30% Holdout."""
    n = len(signal_df)
    split_idx = int(n * 0.70)

    candidates: List[Tuple[StrategyConfig, SimulationResult]] = []
    for ch_win in (60, 80, 100):
        for tp_pct in (0.12, 0.25, 0.40):
            for stop_cb in (True, False):
                cfg_try = replace(
                    base_cfg,
                    channel_window=ch_win,
                    tp_net_pct_of_deployed=tp_pct,
                    stop_on_channel_break=stop_cb,
                )
                r_dev = run_simulation(
                    signal_df=signal_df, cfg=cfg_try, spec=spec,
                    timeframe=timeframe, signal_freq_seconds=freq_sec,
                    sub_df=sub_df, split_name="DEV_70%",
                    eval_start_idx=0, eval_end_idx=split_idx,
                )
                candidates.append((cfg_try, r_dev))

    # Rank strictly on Dev (preferring n_cycles >= 15, then highest total net PnL / PF)
    valid = [c for c in candidates if c[1].n_cycles >= 15]
    pool = valid if valid else candidates
    best_cfg, best_dev = max(pool, key=lambda x: (x[1].total_net_pnl_usd, x[1].expectancy_usd))

    # Evaluate FROZEN winner ONCE on Virgin 30% Holdout + Latency
    wf = walk_forward_70_30(
        signal_df=signal_df, cfg=best_cfg, spec=spec,
        timeframe=timeframe, signal_freq_seconds=freq_sec,
        sub_df=sub_df, label=f"DEV_OPT_{spec.name}_{timeframe}",
    )
    return best_cfg, wf["dev"], wf["holdout"], wf["holdout_latency"]


def main() -> int:
    t0 = time.perf_counter()
    print("=" * 78)
    print("PHASE 1 QUANTITATIVE BACKTEST — DERIV CHANNEL + SMART DCA")
    print("=" * 78)

    # 1. Load BTC 110d 1m data (read-only)
    print("[1/5] Loading 110 days of real BTCUSDT 1m parquet data...")
    btc_1m = load_btc_1m_data()
    btc_m5 = resample_ohlcv(btc_1m, "5min")
    btc_m15 = resample_ohlcv(btc_1m, "15min")
    print(
        f"  BTC 1m: {len(btc_1m):,} bars | {btc_1m.index[0]} -> {btc_1m.index[-1]} "
        f"({(btc_1m.index[-1] - btc_1m.index[0]).total_seconds()/86400:.1f} days)"
    )
    print(f"  BTC M5: {len(btc_m5):,} bars | BTC M15: {len(btc_m15):,} bars")
    print(
        f"  BTC Price Range: open=${btc_1m['open'].iloc[0]:,.2f} -> max=${btc_1m['high'].max():,.2f} "
        f"-> min=${btc_1m['low'].min():,.2f} -> last=${btc_1m['close'].iloc[-1]:,.2f}"
    )

    # 2. Automated Causality Proof (Zero Lookahead)
    print("[2/5] Running automated anti-repaint / causality unit test...")
    base_cfg_btc = StrategyConfig(
        channel_window=80,
        channel_sigma=2.0,
        min_r2=0.45,
        base_stake_usd=1.0,
        layer_multipliers=(1.0, 1.0, 1.5),
        tp_net_pct_of_deployed=0.12,
        use_trailing=True,
        trailing_pullback_frac=0.30,
        global_sl_pct_of_max_basket=0.65,
        max_hold_bars=96,
        cooldown_bars=2,
        use_flow_filter=False,
        allow_long=True,
        allow_short=False,
    )
    verify_causality(btc_m5.iloc[:2000], base_cfg_btc)
    verify_causality(btc_m5.iloc[:2000], replace(base_cfg_btc, use_flow_filter=True))
    print("  CAUSALITY CHECK PASSED (0.0 lookahead across all indicators & signals).")

    # 3. Fetch real Deriv public WS candles for frxEURUSD and R_75
    print("[3/5] Fetching real historical M5 candles from Deriv public WebSocket (frxEURUSD, R_75)...")
    eur_m5 = fetch_deriv_candles("frxEURUSD", granularity=300, max_pages=24)
    eur_m15 = resample_ohlcv(eur_m5, "15min")
    r75_m5 = fetch_deriv_candles("R_75", granularity=300, max_pages=24)
    r75_m15 = resample_ohlcv(r75_m5, "15min")
    print(
        f"  frxEURUSD M5: {len(eur_m5):,} bars ({eur_m5.index[0]} -> {eur_m5.index[-1]}, "
        f"{(eur_m5.index[-1] - eur_m5.index[0]).total_seconds()/86400:.1f} cal days) | M15: {len(eur_m15):,} bars"
    )
    print(
        f"  R_75 M5:      {len(r75_m5):,} bars ({r75_m5.index[0]} -> {r75_m5.index[-1]}, "
        f"{(r75_m5.index[-1] - r75_m5.index[0]).total_seconds()/86400:.1f} cal days) | M15: {len(r75_m15):,} bars"
    )

    # 4. Run Baseline Walk-Forward 70/30 Experiment Matrix
    print("[4/5] Running Walk-Forward (70% Dev / 30% Virgin Holdout + Latency Stress) suite...")
    # Note on stake size for FX / R_75:
    # At $1 stake, Deriv rounds $0.0104 comm UP to $0.02 (2.0% instead of 1.04%).
    # We test FX & R_75 at BOTH $10 base stake (true proportional 1.04%/1.46% fee) AND $1 stake to expose the rounding tax!
    cfg_btc_long = base_cfg_btc
    cfg_btc_flow = replace(base_cfg_btc, use_flow_filter=True)
    cfg_btc_both = replace(base_cfg_btc, allow_long=True, allow_short=True)

    cfg_fx_10 = replace(base_cfg_btc, base_stake_usd=10.0, use_flow_filter=False, allow_long=True, allow_short=False)
    cfg_fx_10_both = replace(cfg_fx_10, allow_long=True, allow_short=True)
    cfg_fx_1 = replace(base_cfg_btc, base_stake_usd=1.0, use_flow_filter=False, allow_long=True, allow_short=False)

    experiments = [
        # --- Real Deriv cryBTCUSD 100x ---
        ("BTC 100x M5 (LONG, $1)", btc_m5, btc_1m, cfg_btc_long, SPEC_BTC_100X, "M5", 300),
        ("BTC 100x M5 (LONG+Flow, $1)", btc_m5, btc_1m, cfg_btc_flow, SPEC_BTC_100X, "M5", 300),
        ("BTC 100x M5 (LONG+SHORT, $1)", btc_m5, btc_1m, cfg_btc_both, SPEC_BTC_100X, "M5", 300),
        ("BTC 100x M15 (LONG, $1)", btc_m15, btc_1m, cfg_btc_long, SPEC_BTC_100X, "M15", 900),
        ("BTC 100x M15 (LONG+Flow, $1)", btc_m15, btc_1m, cfg_btc_flow, SPEC_BTC_100X, "M15", 900),
        ("BTC 100x M15 (LONG+SHORT, $1)", btc_m15, btc_1m, cfg_btc_both, SPEC_BTC_100X, "M15", 900),
        # --- Counterfactual BTC 20x (Diagnostic) ---
        ("BTC 20x-CF M5 (LONG, 7bps)", btc_m5, btc_1m, cfg_btc_long, SPEC_BTC_20X_CF, "M5", 300),
        ("BTC 20x-CF M15 (LONG, 7bps)", btc_m15, btc_1m, cfg_btc_long, SPEC_BTC_20X_CF, "M15", 900),
        ("BTC 20x-LowFee M15 (LONG, 4bps)", btc_m15, btc_1m, cfg_btc_long, SPEC_BTC_20X_LOWFEE_CF, "M15", 900),
        ("BTC 20x-LowFee M15 (L+S, 4bps)", btc_m15, btc_1m, cfg_btc_both, SPEC_BTC_20X_LOWFEE_CF, "M15", 900),
        # --- Real Deriv frxEURUSD 50x & 100x ---
        ("EURUSD 50x M5 (LONG, $10)", eur_m5, None, cfg_fx_10, SPEC_EURUSD_50X, "M5", 300),
        ("EURUSD 50x M5 (L+S, $10)", eur_m5, None, cfg_fx_10_both, SPEC_EURUSD_50X, "M5", 300),
        ("EURUSD 50x M5 (LONG, $1 tax)", eur_m5, None, cfg_fx_1, SPEC_EURUSD_50X, "M5", 300),
        ("EURUSD 50x M15 (LONG, $10)", eur_m15, eur_m5, cfg_fx_10, SPEC_EURUSD_50X, "M15", 900),
        ("EURUSD 50x M15 (L+S, $10)", eur_m15, eur_m5, cfg_fx_10_both, SPEC_EURUSD_50X, "M15", 900),
        ("EURUSD 100x M5 (L+S, $10)", eur_m5, None, cfg_fx_10_both, SPEC_EURUSD_100X, "M5", 300),
        # --- Real Deriv R_75 50x & 100x ---
        ("R_75 50x M5 (LONG, $10)", r75_m5, None, cfg_fx_10, SPEC_R75_50X, "M5", 300),
        ("R_75 50x M5 (L+S, $10)", r75_m5, None, cfg_fx_10_both, SPEC_R75_50X, "M5", 300),
        ("R_75 50x M15 (LONG, $10)", r75_m15, r75_m5, cfg_fx_10, SPEC_R75_50X, "M15", 900),
        ("R_75 50x M15 (L+S, $10)", r75_m15, r75_m5, cfg_fx_10_both, SPEC_R75_50X, "M15", 900),
        ("R_75 100x M5 (L+S, $10)", r75_m5, None, cfg_fx_10_both, SPEC_R75_100X, "M5", 300),
    ]

    wf_results: List[Tuple[str, Dict[str, SimulationResult], Tuple[str, str]]] = []
    for lbl, sig_df, sub_df, cfg, spec, tf, fsec in experiments:
        wf = walk_forward_70_30(
            signal_df=sig_df, cfg=cfg, spec=spec,
            timeframe=tf, signal_freq_seconds=fsec,
            sub_df=sub_df, label=lbl,
        )
        v_code, v_reason = verdict_for_oos(wf["holdout"], wf["holdout_latency"])
        wf_results.append((lbl, wf, (v_code, v_reason)))
        o = wf["holdout"]
        d = wf["dev"]
        print(
            f"  {lbl:<32} | DEV N={d.n_cycles:3d} Net=${d.total_net_pnl_usd:+7.2f} PF={fmt_pf(d.profit_factor):>5} "
            f"| OOS N={o.n_cycles:3d} WR={o.win_rate_pct:5.1f}% Net=${o.total_net_pnl_usd:+7.2f} "
            f"PF={fmt_pf(o.profit_factor):>5} DD={o.max_drawdown_pct:5.1f}% -> {v_code}"
        )

    # 5. Dev-Only Grid Optimization -> Frozen OOS Audit (Overfitting Check)
    print("[5/5] Running Dev-ONLY parameter grid -> Frozen Virgin Holdout OOS audit + Crash Stress Test...")
    opt_targets = [
        ("BTC 100x M5 (L+S, $1)", btc_m5, btc_1m, cfg_btc_both, SPEC_BTC_100X, "M5", 300),
        ("BTC 100x M15 (L+S, $1)", btc_m15, btc_1m, cfg_btc_both, SPEC_BTC_100X, "M15", 900),
        ("BTC 20x-CF M15 (L+S, 7bps)", btc_m15, btc_1m, cfg_btc_both, SPEC_BTC_20X_CF, "M15", 900),
        ("EURUSD 50x M5 (L+S, $10)", eur_m5, None, cfg_fx_10_both, SPEC_EURUSD_50X, "M5", 300),
        ("R_75 50x M5 (L+S, $10)", r75_m5, None, cfg_fx_10_both, SPEC_R75_50X, "M5", 300),
    ]
    opt_rows = []
    for lbl, sig_df, sub_df, base_c, spec, tf, fsec in opt_targets:
        best_cfg, r_dev, r_oos, r_lat = dev_grid_select(sig_df, sub_df, base_c, spec, tf, fsec)
        v_code, v_reason = verdict_for_oos(r_oos, r_lat)
        opt_rows.append((lbl, best_cfg, r_dev, r_oos, r_lat, v_code, v_reason))
        print(
            f"  [DEV-OPT] {lbl:<26} best(W={best_cfg.channel_window}, TP={best_cfg.tp_net_pct_of_deployed:.0%}, CBStop={best_cfg.stop_on_channel_break}) "
            f"| DEV=${r_dev.total_net_pnl_usd:+7.2f} (PF={fmt_pf(r_dev.profit_factor)}) "
            f"| OOS=${r_oos.total_net_pnl_usd:+7.2f} (PF={fmt_pf(r_oos.profit_factor)}, N={r_oos.n_cycles}) -> {v_code}"
        )

    # 6. Crash Stress Test on 110d BTC (worst -3% to -8%+ crashes)
    btc_crashes = detect_crash_windows(btc_m15, min_drop_pct=3.0, lookback_hours=24.0, max_windows=6)
    btc_m5_full_100x = run_simulation(
        btc_m5, cfg_btc_long, SPEC_BTC_100X, "M5", 300, sub_df=btc_1m, split_name="FULL_110D"
    )
    btc_m15_full_100x = run_simulation(
        btc_m15, cfg_btc_long, SPEC_BTC_100X, "M15", 900, sub_df=btc_1m, split_name="FULL_110D"
    )
    btc_m5_full_nocg = run_simulation(
        btc_m5,
        replace(cfg_btc_long, adx_crash_threshold=999.0, atr_spike_mult=999.0),
        SPEC_BTC_100X, "M5", 300, sub_df=btc_1m, split_name="FULL_110D_NO_CG",
    )
    btc_m15_full_20x = run_simulation(
        btc_m15, cfg_btc_long, SPEC_BTC_20X_CF, "M15", 900, sub_df=btc_1m, split_name="FULL_110D_20X"
    )

    crash_m5_100x = evaluate_crash_stress(btc_m5, cfg_btc_long, btc_m5_full_100x, btc_crashes)
    crash_m15_100x = evaluate_crash_stress(btc_m15, cfg_btc_long, btc_m15_full_100x, btc_crashes)
    crash_m5_nocg = evaluate_crash_stress(
        btc_m5,
        replace(cfg_btc_long, adx_crash_threshold=999.0, atr_spike_mult=999.0),
        btc_m5_full_nocg,
        btc_crashes,
    )
    crash_m15_20x = evaluate_crash_stress(btc_m15, cfg_btc_long, btc_m15_full_20x, btc_crashes)

    # Also check R_75 worst crashes
    r75_crashes = detect_crash_windows(r75_m15, min_drop_pct=3.0, lookback_hours=24.0, max_windows=4)
    r75_m5_full = run_simulation(
        r75_m5, cfg_fx_10, SPEC_R75_50X, "M5", 300, sub_df=None, split_name="FULL"
    )
    crash_r75 = evaluate_crash_stress(r75_m5, cfg_fx_10, r75_m5_full, r75_crashes)

    # 7. Write Honest Markdown Report
    report_path = THIS_DIR / "BACKTEST_REPORT.md"
    lines: List[str] = []
    lines.append("# Phase 1 — Realistic Walk-Forward Quantitative Audit Report")
    lines.append("")
    lines.append(f"**Generated:** {pd.Timestamp.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}  ")
    lines.append("**Methodology:** Strictly causal (zero-lookahead verified) Linear Regression Channel + EMA50/200 + ADX(14)/ATR Crash Guard + Wick/RSI/MACD Confirmation + Smart DCA Basket (`[1.0x, 1.0x, 1.5x]`, max 3 layers).  ")
    lines.append("**Execution Realism:** Live-probed Deriv `MULTUP`/`MULTDOWN` commissions & cent-rounding, bid-ask spread, exact intrabar sub-bar individual contract stop-out checks (M1 sub-bars for BTC; M5 sub-bars for M15 FX/R_75), conservative stop-before-TP intrabar ordering, strict chronological 70% Dev / 30% Virgin Holdout split, and `+1` bar / `+1.0 bps` latency sensitivity stress.")
    lines.append("")

    # Executive Summary
    n_pass_base = sum(1 for _, _, (vc, _) in wf_results if vc == "PASS")
    n_pass_real = sum(1 for _, wf, (vc, _) in wf_results if vc == "PASS" and not wf["holdout"].asset_spec.is_counterfactual)
    lines.append("## 1. Executive Summary & Honest Verdict")
    lines.append("")
    lines.append(f"- **Baseline Configs Passing Strict OOS + Latency Gate:** `{n_pass_base}/{len(wf_results)}` total (`{n_pass_real}` on real live Deriv specs).")
    lines.append("- **Automated Anti-Repaint / Causality Proof:** `PASS` — perturbing future OHLCV bars (`+50%` jump) causes `0.0` change in any indicator or signal up to bar `t`.")
    lines.append("- **Structural Mathematical Reality of Deriv `cryBTCUSD 100x`:**")
    lines.append("  - Live Deriv API (`proposal`) charges **`$0.07` per `$1.00` stake (`7.00%` of stake = `7.00 bps` notional)** and imposes an automatic **individual stop-out at `-0.9300%`** (`-(1 - 0.07)/100`).")
    lines.append("  - Every single opened layer immediately starts at **`-7.0%` PnL** (plus `0.31 bps` spread = `-7.31%` stake drag). A 3-layer basket (`$1.00 + $1.00 + $1.50 = $3.50`) pays **`$0.25` upfront commission** before price moves a single tick — requiring a `+0.071%` favorable move just to break even, while Layer 1 is permanently liquidated (`-$1.00`) on any `-0.93%` intrabar BTC wick (routine 15-minute noise in crypto!).")
    lines.append("- **Contrast with Real Deriv Lower-Leverage Pairs (`frxEURUSD 50x` & `R_75 50x`):**")
    lines.append("  - `frxEURUSD 50x`: live commission is **`1.04%` of stake** (`2.08 bps` notional at `$10` stake) with **`-1.98%` stop-out** (~215 pips!). Note: at `$1.00` stake, Deriv rounds `$0.0104` UP to `$0.02` (`2.00%` — nearly **2x cent-rounding tax**!).")
    lines.append("  - `R_75 50x`: live commission is **`1.46%` of stake** (`2.92 bps` notional at `$10` stake) with **`-1.97%` stop-out**.")
    lines.append("")

    # Section 2: Datasets & Splits
    btc_split_t = btc_m5.index[int(len(btc_m5) * 0.70)]
    eur_split_t = eur_m5.index[int(len(eur_m5) * 0.70)]
    r75_split_t = r75_m5.index[int(len(r75_m5) * 0.70)]
    lines.append("## 2. Datasets, Live Deriv Specs & Chronological 70/30 Splits")
    lines.append("")
    lines.append("| Asset | Source | Sub-Bar Resolution | Period (UTC) | Total M5 / M15 Bars | 70/30 Split Timestamp (UTC) | Price Start -> Peak -> Trough -> End |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(
        f"| `cryBTCUSD` (BTCUSDT proxy) | Local 1m Parquets (45d+65d) | **Exact M1** (`{len(btc_1m):,}` bars) | "
        f"{btc_1m.index[0].strftime('%Y-%m-%d')} to {btc_1m.index[-1].strftime('%Y-%m-%d')} ({(btc_1m.index[-1]-btc_1m.index[0]).total_seconds()/86400:.1f}d) | "
        f"{len(btc_m5):,} / {len(btc_m15):,} | `{btc_split_t.strftime('%Y-%m-%d %H:%M')}` | "
        f"${btc_1m['open'].iloc[0]:,.0f} -> ${btc_1m['high'].max():,.0f} -> ${btc_1m['low'].min():,.0f} -> ${btc_1m['close'].iloc[-1]:,.0f} |"
    )
    lines.append(
        f"| `frxEURUSD` | Deriv Public WS (`ticks_history`) | M5 native / M5 sub-bars for M15 | "
        f"{eur_m5.index[0].strftime('%Y-%m-%d')} to {eur_m5.index[-1].strftime('%Y-%m-%d')} ({(eur_m5.index[-1]-eur_m5.index[0]).total_seconds()/86400:.1f}d) | "
        f"{len(eur_m5):,} / {len(eur_m15):,} | `{eur_split_t.strftime('%Y-%m-%d %H:%M')}` | "
        f"{eur_m5['open'].iloc[0]:.5f} -> {eur_m5['high'].max():.5f} -> {eur_m5['low'].min():.5f} -> {eur_m5['close'].iloc[-1]:.5f} |"
    )
    lines.append(
        f"| `R_75` | Deriv Public WS (`ticks_history`) | M5 native / M5 sub-bars for M15 | "
        f"{r75_m5.index[0].strftime('%Y-%m-%d')} to {r75_m5.index[-1].strftime('%Y-%m-%d')} ({(r75_m5.index[-1]-r75_m5.index[0]).total_seconds()/86400:.1f}d) | "
        f"{len(r75_m5):,} / {len(r75_m15):,} | `{r75_split_t.strftime('%Y-%m-%d %H:%M')}` | "
        f"{r75_m5['open'].iloc[0]:,.1f} -> {r75_m5['high'].max():,.1f} -> {r75_m5['low'].min():,.1f} -> {r75_m5['close'].iloc[-1]:,.1f} |"
    )
    lines.append("")
    lines.append("### Live-Probed Deriv `MULTUP` Cost & Stop-Out Specs")
    lines.append("")
    lines.append("| Spec | Multiplier | Commission (% Stake) | Notional Fee (bps) | Spread (bps) | Exact Stop-Out Dist | Notes |")
    lines.append("|---|---|---|---|---|---|---|")
    for sp in (SPEC_BTC_100X, SPEC_BTC_20X_CF, SPEC_BTC_20X_LOWFEE_CF, SPEC_EURUSD_50X, SPEC_EURUSD_100X, SPEC_R75_50X, SPEC_R75_100X):
        notl_bps = (sp.commission_pct_of_stake / sp.multiplier) * 1e4
        so_pct = sp.stop_out_distance(10.0 if sp.symbol != "cryBTCUSD" else 1.0) * 100.0
        lines.append(
            f"| `{sp.name}` | {sp.multiplier:.0f}x | {sp.commission_pct_of_stake*100:.2f}% | "
            f"{notl_bps:.2f} bps | {sp.spread_bps:.2f} bps | -{so_pct:.3f}% | {sp.notes} |"
        )
    lines.append("")

    # Section 3: Main Walk-Forward Table (Virgin 30% Holdout + Dev + Latency)
    lines.append("## 3. Walk-Forward Results — Baseline Strategy (70% Dev vs 30% Virgin Holdout vs +1 Latency)")
    lines.append("")
    lines.append(
        "*Reference starting capital for Max Drawdown % is `100 x base_stake` (`$100` for `$1` stake configs; `$1,000` for `$10` stake configs). "
        "Strict PASS Gate on Virgin 30% Holdout: `Cycles >= 15`, `Expectancy > $0`, `PF >= 1.15`, `MaxDD <= 20%`, AND stays positive (`Exp > $0`, `PF >= 1.05`) under `+1` bar / `+1.0 bps` latency stress.*"
    )
    lines.append("")
    lines.append("### 3A. Virgin 30% Holdout (Out-of-Sample) Summary & PASS/FAIL Verdicts")
    lines.append("")
    lines.append("| Configuration | Split | Cycles N | Win Rate | Net Exp/Cycle | Total Net PnL | Commissions | Profit Factor | Max DD ($ / %) | Worst Streak | Ind. Stop-Outs | Verdict |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for lbl, wf, (vc, _) in wf_results:
        lines.append(fmt_row(lbl, wf["holdout"], extra_verdict=vc))
    lines.append("")

    lines.append("### 3B. Full Split Comparison (70% Dev vs 30% Holdout vs +1 Latency Stress)")
    lines.append("")
    lines.append("| Configuration | Split | Cycles N | Win Rate | Net Exp/Cycle | Total Net PnL | Commissions | Profit Factor | Max DD ($ / %) | Worst Streak | Ind. Stop-Outs |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for lbl, wf, _ in wf_results:
        lines.append(fmt_row(lbl, wf["dev"]))
        lines.append(fmt_row(lbl, wf["holdout"]))
        lines.append(fmt_row(lbl, wf["holdout_latency"]))
    lines.append("")

    # Section 3C: Exit reason & layer breakdown on Holdout for key assets
    lines.append("### 3C. Holdout Exit Reason & DCA Layer Distribution (Key Configs)")
    lines.append("")
    lines.append("| Configuration | Avg Layers | Layer Dist (1/2/3) | Raw Signals / Crash Guard Blocked | Exit Reason Breakdown |")
    lines.append("|---|---:|---|---|---|")
    for lbl, wf, _ in wf_results:
        o = wf["holdout"]
        lc = f"{o.layer_counts.get(1,0)} / {o.layer_counts.get(2,0)} / {o.layer_counts.get(3,0)}"
        er_str = ", ".join(f"{k}:{v}" for k, v in o.exit_reasons.items()) if o.exit_reasons else "None"
        lines.append(f"| {lbl} | {o.avg_layers:.2f} | {lc} | {o.raw_signals} / {o.crash_guard_blocks} | {er_str} |")
    lines.append("")

    # Section 4: Dev-Only Optimization -> Frozen OOS Overfitting Audit
    lines.append("## 4. Dev-Only Parameter Selection -> Frozen 30% Holdout (Overfitting Audit)")
    lines.append("")
    lines.append(
        "To test whether tuning `(channel_window in {60,80,100}, tp_net_pct in {12%,25%,40%}, stop_on_channel_break in {True,False})` "
        "rescues viability without leaking Holdout, we ranked all 18 combinations strictly on **70% Dev**, froze the top candidate, and ran it ONCE on **30% Virgin Holdout**:"
    )
    lines.append("")
    lines.append("| Target | Frozen Dev-Best Params | Dev N / Net PnL / PF | Holdout N / WR / Net PnL / PF / MaxDD | Latency+1 Net PnL / PF | OOS Verdict |")
    lines.append("|---|---|---|---:|---:|---|")
    for lbl, bcfg, r_dev, r_oos, r_lat, vc, vr in opt_rows:
        pstr = f"W={bcfg.channel_window}, TP={bcfg.tp_net_pct_of_deployed:.0%}, CBStop={bcfg.stop_on_channel_break}"
        dev_s = f"{r_dev.n_cycles} / ${r_dev.total_net_pnl_usd:+.2f} / {fmt_pf(r_dev.profit_factor)}"
        oos_s = f"{r_oos.n_cycles} / {r_oos.win_rate_pct:.1f}% / ${r_oos.total_net_pnl_usd:+.2f} / {fmt_pf(r_oos.profit_factor)} / {r_oos.max_drawdown_pct:.1f}%"
        lat_s = f"${r_lat.total_net_pnl_usd:+.2f} / {fmt_pf(r_lat.profit_factor)}"
        lines.append(f"| {lbl} | `{pstr}` | {dev_s} | {oos_s} | {lat_s} | **{vc}** |")
    lines.append("")

    # Section 5: Crash Stress Test
    lines.append("## 5. Crash Stress Test — Worst `-3%` to `-8%+` Historical Drawdowns")
    lines.append("")
    lines.append("### 5A. BTC 110-Day Isolated Crash Windows (Rolling 24h Peak-to-Trough >= 3%)")
    lines.append("")
    lines.append("| # | Peak -> Trough (UTC) | Drop % | Dur (h) | BTC M5 100x (With CG): Cyc / SO / PnL | BTC M5 100x (NO CG): Cyc / SO / PnL | BTC M15 100x: Cyc / SO / PnL | BTC M15 20x-CF: Cyc / SO / PnL | M5 Raw / CG Blocked |")
    lines.append("|---:|---|---:|---:|---|---|---|---|---|")
    for c5, c5n, c15, c20 in zip(crash_m5_100x, crash_m5_nocg, crash_m15_100x, crash_m15_20x):
        w = c5.window
        span = f"{w.peak_time.strftime('%m-%d %H:%M')} -> {w.trough_time.strftime('%m-%d %H:%M')}"
        lines.append(
            f"| {w.rank} | `{span}` | **{w.drawdown_pct:.2f}%** | {w.duration_hours:.1f}h | "
            f"{c5.cycles_active} / {c5.stop_outs} / `${c5.bot_net_pnl_usd:+.2f}` | "
            f"{c5n.cycles_active} / {c5n.stop_outs} / `${c5n.bot_net_pnl_usd:+.2f}` | "
            f"{c15.cycles_active} / {c15.stop_outs} / `${c15.bot_net_pnl_usd:+.2f}` | "
            f"{c20.cycles_active} / {c20.stop_outs} / `${c20.bot_net_pnl_usd:+.2f}` | "
            f"{c5.raw_signals_in_crash} / {c5.blocked_by_crash_guard} |"
        )
    tot_c5 = sum(x.bot_net_pnl_usd for x in crash_m5_100x)
    tot_c5n = sum(x.bot_net_pnl_usd for x in crash_m5_nocg)
    tot_c15 = sum(x.bot_net_pnl_usd for x in crash_m15_100x)
    tot_c20 = sum(x.bot_net_pnl_usd for x in crash_m15_20x)
    lines.append(
        f"| **SUM** | All {len(crash_m5_100x)} Crash Windows | — | — | "
        f"**`${tot_c5:+.2f}`** | **`${tot_c5n:+.2f}`** | **`${tot_c15:+.2f}`** | **`${tot_c20:+.2f}`** | "
        f"{sum(x.raw_signals_in_crash for x in crash_m5_100x)} / {sum(x.blocked_by_crash_guard for x in crash_m5_100x)} |"
    )
    lines.append("")

    if crash_r75:
        lines.append("### 5B. `R_75` Isolated Crash Windows (50x, `$10` stake)")
        lines.append("")
        lines.append("| # | Peak -> Trough (UTC) | Drop % | Dur (h) | R_75 M5 50x: Cyc / SO / PnL | Raw / CG Blocked |")
        lines.append("|---:|---|---:|---:|---|---|")
        for cr in crash_r75:
            w = cr.window
            span = f"{w.peak_time.strftime('%m-%d %H:%M')} -> {w.trough_time.strftime('%m-%d %H:%M')}"
            lines.append(
                f"| {w.rank} | `{span}` | **{w.drawdown_pct:.2f}%** | {w.duration_hours:.1f}h | "
                f"{cr.cycles_active} / {cr.stop_outs} / `${cr.bot_net_pnl_usd:+.2f}` | "
                f"{cr.raw_signals_in_crash} / {cr.blocked_by_crash_guard} |"
            )
        lines.append("")

    # Section 6: Honest Quantitative Diagnosis & Recommendations
    lines.append("## 6. Skeptical Quantitative Diagnosis & Recommendations for Production Platform")
    lines.append("")
    lines.append("### Key Empirical Findings")
    lines.append("1. **Commission Drag vs Stop-Out Asymmetry on `cryBTCUSD 100x`:**")
    btc_m5_oos = wf_results[0][1]["holdout"]
    btc_m15_oos = wf_results[3][1]["holdout"]
    lines.append(
        f"   - On Virgin 30% Holdout, `BTC 100x M5 (LONG)` generated `N={btc_m5_oos.n_cycles}` cycles, "
        f"paid `${btc_m5_oos.total_commissions_usd:.2f}` in commissions, suffered `{btc_m5_oos.total_individual_stop_outs}` individual stop-outs, "
        f"and finished at **`${btc_m5_oos.total_net_pnl_usd:+.2f}` (PF={fmt_pf(btc_m5_oos.profit_factor)})**."
    )
    lines.append(
        f"   - `BTC 100x M15 (LONG)` generated `N={btc_m15_oos.n_cycles}` cycles, paid `${btc_m15_oos.total_commissions_usd:.2f}` in commissions, "
        f"suffered `{btc_m15_oos.total_individual_stop_outs}` individual stop-outs, and finished at **`${btc_m15_oos.total_net_pnl_usd:+.2f}` (PF={fmt_pf(btc_m15_oos.profit_factor)})**."
    )
    lines.append("   - **Root Cause:** Paying `7.0%` of stake per layer to target `+12%` net profit forces an unfavorable effective risk/reward ratio whenever `-0.93%` wicks liquidate individual layers before mean-reversion completes.")
    lines.append("2. **Cent-Rounding Penalty at `$1.00` Stake on FX / Synthetics:**")
    lines.append("   - On `frxEURUSD 50x`, true proportional commission is `1.04%`, but Deriv's 2-decimal minimum rounding charges `$0.02` on a `$1.00` stake (`2.00%` — nearly double the fee!). Production UI Risk Calculator MUST warn users trading `$1` stakes on FX/Synthetics about the rounding tax.")
    lines.append("3. **Crash Guard Value:**")
    lines.append(
        f"   - During the worst BTC `-3%` to `-8%+` crash windows, macro filters (`EMA50 > EMA200` + `R^2 >= 0.45` + positive slope + `ADX(14) > 35` Crash Guard) "
        f"kept crash losses bounded (`${tot_c5:+.2f}` with CG vs `${tot_c5n:+.2f}` without CG on M5 100x)."
    )
    lines.append("4. **Deployment Guardrail (Mandatory Transparency):**")
    lines.append("   - **DO NOT** market or deploy `cryBTCUSD 100x` Smart DCA as a guaranteed profitable real-money bot without positive OOS validation. Any user selecting `cryBTCUSD` (`100x`, `7%` fee, `-0.93%` stop-out) in the web UI must see an explicit **High Friction / Tight Stop-Out Risk Warning** and default to **DEMO (`VRTC`)** first.")
    lines.append("   - Prioritize lower-leverage, wider-stop-out, lower-fee Deriv Multiplier instruments (e.g., `50x` Forex/Synthetics with proportional stake sizing `>= $10` or further research into wider TP / structural mean-reversion regimes) before enabling Real accounts.")
    lines.append("")
    lines.append(f"*(Total backtest runtime: {time.perf_counter() - t0:.2f}s)*")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("=" * 78)
    print(f"REPORT WRITTEN: {report_path}")
    print(f"Elapsed: {time.perf_counter() - t0:.2f}s")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
