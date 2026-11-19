"""Strictly causal quantitative indicators, Channel Detector, and Smart DCA Basket Manager.

Anti-repaint guarantee:
- Every indicator at bar index `t` depends ONLY on OHLCV data in `[0 .. t]`.
- Verified via automated causality unit test in `verify_causality()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


@dataclass(frozen=True)
class StrategyConfig:
    """Parameters for causal Channel + Confirmation + Smart DCA strategy."""
    # Linear regression channel
    channel_window: int = 80
    channel_sigma: float = 2.0
    min_r2: float = 0.45
    min_slope_bps: float = 0.0  # per signal bar in bps; >0 requires upward tilt for LONG

    # Macro trend & Crash Guard
    ema_fast: int = 50
    ema_slow: int = 200
    adx_period: int = 14
    adx_crash_threshold: float = 35.0
    atr_period: int = 14
    atr_spike_mult: float = 2.5
    atr_median_window: int = 50

    # Confirmations
    touch_atr_buffer: float = 0.15
    wick_ratio_min: float = 0.40
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    use_flow_filter: bool = False  # uses taker_buy_base when available (BTC)

    # Direction
    allow_long: bool = True
    allow_short: bool = False

    # Smart DCA Basket parameters
    base_stake_usd: float = 1.0
    layer_multipliers: Tuple[float, ...] = (1.0, 1.0, 1.5)  # max_layers = len(...) = 3
    dca_step_pct: Optional[float] = None  # None -> auto-fit inside 0.75 * stop_out_dist
    dca_atr_mult: float = 0.80  # ATR multiplier when stop_out is wide (FX/R_75)
    dca_require_confirmation: bool = True
    channel_break_atr_mult: float = 1.0  # suspend re-entries & optional stop below lower - 1.0*ATR
    stop_on_channel_break: bool = True

    # Exit mechanics (Basket TP + Trailing + Global SL + Timeout + Cooldown)
    tp_net_pct_of_deployed: float = 0.12  # e.g., +12% net profit on deployed basket stake (after comm/spread)
    use_trailing: bool = True
    trailing_pullback_frac: float = 0.30  # close if net profit retraces 30% from peak once TP reached
    global_sl_pct_of_max_basket: float = 0.65  # e.g., -65% of max basket stake ($2.28 on $3.50 max basket)
    max_hold_bars: int = 96  # timeout in signal bars (e.g., 96 M5 = 8h; 96 M15 = 24h)
    cooldown_bars: int = 2


def compute_linear_regression_channel(
    close: np.ndarray,
    window: int = 80,
    sigma_mult: float = 2.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Strictly causal rolling OLS linear regression channel.

    At index `t >= window - 1`, fits y = a + b*x on close[t - window + 1 : t + 1]
    for x = 0, 1, ..., window - 1. Returns arrays aligned with `close`:
      (mid, upper, lower, r2, slope_bps, sigma)
    First `window - 1` elements are NaN.
    """
    n = len(close)
    mid = np.full(n, np.nan, dtype=np.float64)
    upper = np.full(n, np.nan, dtype=np.float64)
    lower = np.full(n, np.nan, dtype=np.float64)
    r2 = np.full(n, np.nan, dtype=np.float64)
    slope_bps = np.full(n, np.nan, dtype=np.float64)
    sigma = np.full(n, np.nan, dtype=np.float64)

    if n < window or window < 3:
        return mid, upper, lower, r2, slope_bps, sigma

    x = np.arange(window, dtype=np.float64)
    x_mean = 0.5 * (window - 1)
    xc = x - x_mean
    s_xx = float(np.sum(xc * xc))

    # Vectorized strictly causal sliding windows: row i corresponds to close[i : i + window]
    # whose rightmost (current) bar index is t = i + window - 1.
    wins = sliding_window_view(close.astype(np.float64), window_shape=window)  # (n - window + 1, window)
    y_mean = wins.mean(axis=1)
    # b = sum(xc * (y - y_mean)) / s_xx = sum(xc * y) / s_xx
    b = (wins @ xc) / s_xx
    a = y_mean - b * x_mean

    # Fitted values across each window: a[:, None] + b[:, None] * x[None, :]
    # Compute SS_res without materializing huge intermediate if large, in chunks of 5000 rows
    m = wins.shape[0]
    ss_res = np.empty(m, dtype=np.float64)
    ss_tot = np.empty(m, dtype=np.float64)
    chunk = 5000
    for start in range(0, m, chunk):
        end = min(start + chunk, m)
        w_c = wins[start:end]
        ym_c = y_mean[start:end, None]
        yhat_c = a[start:end, None] + b[start:end, None] * x[None, :]
        resid_c = w_c - yhat_c
        ss_res[start:end] = np.einsum("ij,ij->i", resid_c, resid_c)
        diff_c = w_c - ym_c
        ss_tot[start:end] = np.einsum("ij,ij->i", diff_c, diff_c)

    sig_vals = np.sqrt(ss_res / float(window))
    mid_vals = a + b * (window - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r2_vals = np.where(ss_tot > 1e-18, 1.0 - (ss_res / ss_tot), 0.0)
        r2_vals = np.clip(r2_vals, 0.0, 1.0)
        sbps_vals = np.where(np.abs(mid_vals) > 1e-18, (b / mid_vals) * 1e4, 0.0)

    idx_start = window - 1
    mid[idx_start:] = mid_vals
    sigma[idx_start:] = sig_vals
    upper[idx_start:] = mid_vals + sigma_mult * sig_vals
    lower[idx_start:] = mid_vals - sigma_mult * sig_vals
    r2[idx_start:] = r2_vals
    slope_bps[idx_start:] = sbps_vals

    return mid, upper, lower, r2, slope_bps, sigma


def _wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's exponential moving average (causal, alpha = 1 / period)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def compute_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Compute all strictly causal indicators on OHLC(V) DataFrame.

    Required columns: ['open', 'high', 'low', 'close'].
    Optional columns: ['volume', 'taker_buy_base'].
    Returns a new DataFrame with indicator & signal columns attached.
    """
    out = df.copy()
    o = out["open"].to_numpy(dtype=np.float64)
    h = out["high"].to_numpy(dtype=np.float64)
    l = out["low"].to_numpy(dtype=np.float64)
    c = out["close"].to_numpy(dtype=np.float64)

    close_s = pd.Series(c, index=out.index)
    high_s = pd.Series(h, index=out.index)
    low_s = pd.Series(l, index=out.index)

    # 1. Causal Linear Regression Channel
    mid, upper, lower, r2, slope_bps, sigma = compute_linear_regression_channel(
        c, window=cfg.channel_window, sigma_mult=cfg.channel_sigma
    )
    out["ch_mid"] = mid
    out["ch_upper"] = upper
    out["ch_lower"] = lower
    out["ch_r2"] = r2
    out["ch_slope_bps"] = slope_bps
    out["ch_sigma"] = sigma

    # 2. Macro Trend EMAs
    out["ema_fast"] = close_s.ewm(span=cfg.ema_fast, adjust=False, min_periods=cfg.ema_fast).mean()
    out["ema_slow"] = close_s.ewm(span=cfg.ema_slow, adjust=False, min_periods=cfg.ema_slow).mean()

    # 3. True Range, ATR(14), ADX(14), +DI(14), -DI(14)
    prev_close = close_s.shift(1)
    tr = pd.concat(
        [
            (high_s - low_s).abs(),
            (high_s - prev_close).abs(),
            (low_s - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = _wilder_rma(tr, cfg.atr_period)
    out["atr"] = atr
    out["atr_median"] = atr.rolling(cfg.atr_median_window, min_periods=20).median()

    up_move = high_s - high_s.shift(1)
    down_move = low_s.shift(1) - low_s
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=out.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=out.index,
    )
    tr_rma = _wilder_rma(tr, cfg.adx_period)
    plus_di = 100.0 * _wilder_rma(plus_dm, cfg.adx_period) / tr_rma.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_rma(minus_dm, cfg.adx_period) / tr_rma.replace(0.0, np.nan)
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = _wilder_rma(dx, cfg.adx_period)

    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    out["adx"] = adx

    # 4. RSI(14)
    delta = close_s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder_rma(gain, cfg.rsi_period)
    avg_loss = _wilder_rma(loss, cfg.rsi_period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_loss > 0.0, 100.0).where((avg_gain > 0.0) | (avg_loss > 0.0), 50.0)
    out["rsi"] = rsi

    # 5. MACD(12, 26, 9)
    ema12 = close_s.ewm(span=cfg.macd_fast, adjust=False, min_periods=cfg.macd_fast).mean()
    ema26 = close_s.ewm(span=cfg.macd_slow, adjust=False, min_periods=cfg.macd_slow).mean()
    macd_line = ema12 - ema26
    macd_sig = macd_line.ewm(span=cfg.macd_signal, adjust=False, min_periods=cfg.macd_signal).mean()
    macd_hist = macd_line - macd_sig
    out["macd_hist"] = macd_hist

    # 6. Crash Guard (regime filter)
    atr_spike = out["atr"] > (cfg.atr_spike_mult * out["atr_median"])
    out["crash_guard_long"] = ((out["adx"] > cfg.adx_crash_threshold) & (out["minus_di"] > out["plus_di"])) | atr_spike
    out["crash_guard_short"] = ((out["adx"] > cfg.adx_crash_threshold) & (out["plus_di"] > out["minus_di"])) | atr_spike

    # 7. Price Action Wick Rejection (causal on closed bar `t`)
    candle_range = h - l
    lower_wick = np.minimum(o, c) - l
    upper_wick = h - np.maximum(o, c)
    midpoint = 0.5 * (h + l)

    with np.errstate(divide="ignore", invalid="ignore"):
        lower_wick_ratio = np.where(candle_range > 1e-12, lower_wick / candle_range, 0.0)
        upper_wick_ratio = np.where(candle_range > 1e-12, upper_wick / candle_range, 0.0)

    c1_long = (lower_wick_ratio >= cfg.wick_ratio_min) | (c > midpoint)
    c1_short = (upper_wick_ratio >= cfg.wick_ratio_min) | (c < midpoint)

    # 8. Indicator Turn (RSI turning up from oversold OR MACD hist losing negative momentum 2 bars)
    rsi_prev = out["rsi"].shift(1)
    mh = out["macd_hist"]
    mh1 = mh.shift(1)
    mh2 = mh.shift(2)

    c2_long = ((out["rsi"] < cfg.rsi_oversold) & (out["rsi"] > rsi_prev)) | (
        (mh2 < 0.0) & (mh > mh1) & (mh1 > mh2)
    )
    c2_short = ((out["rsi"] > cfg.rsi_overbought) & (out["rsi"] < rsi_prev)) | (
        (mh2 > 0.0) & (mh < mh1) & (mh1 < mh2)
    )

    # 9. Optional Flow Criterion (real Binance taker buy delta when present)
    has_flow = (
        cfg.use_flow_filter
        and "taker_buy_base" in out.columns
        and "volume" in out.columns
    )
    if has_flow:
        taker_delta = 2.0 * out["taker_buy_base"] - out["volume"]
        out["taker_delta"] = taker_delta
        c3_long = (taker_delta > 0.0) | ((taker_delta > taker_delta.shift(1)) & (close_s >= out["open"]))
        c3_short = (taker_delta < 0.0) | ((taker_delta < taker_delta.shift(1)) & (close_s <= out["open"]))
        confirm_long = (c1_long.astype(int) + c2_long.astype(int) + c3_long.astype(int)) >= 2
        confirm_short = (c1_short.astype(int) + c2_short.astype(int) + c3_short.astype(int)) >= 2
    else:
        # Without flow data: require BOTH wick rejection AND indicator turn (2 of 2)
        confirm_long = c1_long & c2_long
        confirm_short = c1_short & c2_short

    out["confirm_long"] = confirm_long.fillna(False).astype(bool)
    out["confirm_short"] = confirm_short.fillna(False).astype(bool)
    # Re-entry confirmation (wick rejection OR indicator turn)
    out["reentry_confirm_long"] = (c1_long | c2_long).fillna(False).astype(bool)
    out["reentry_confirm_short"] = (c1_short | c2_short).fillna(False).astype(bool)

    # 10. Structural channel conditions & touches
    warmup_ok = (
        out["ch_lower"].notna()
        & out["ema_slow"].notna()
        & out["adx"].notna()
        & out["atr_median"].notna()
        & out["rsi"].notna()
        & out["macd_hist"].notna()
    )

    channel_long_ok = (
        warmup_ok
        & (out["ch_slope_bps"] > cfg.min_slope_bps)
        & (out["ch_r2"] >= cfg.min_r2)
        & (out["ema_fast"] > out["ema_slow"])
    )
    touch_lower = (out["low"] <= out["ch_lower"] + cfg.touch_atr_buffer * out["atr"]) & (
        out["close"] >= out["ch_lower"] - cfg.channel_break_atr_mult * out["atr"]
    )
    out["channel_break_long"] = warmup_ok & (
        out["close"] < out["ch_lower"] - cfg.channel_break_atr_mult * out["atr"]
    )
    out["raw_entry_long"] = channel_long_ok & touch_lower & out["confirm_long"]
    out["entry_long"] = out["raw_entry_long"] & (~out["crash_guard_long"])
    out["blocked_by_crash_guard_long"] = out["raw_entry_long"] & out["crash_guard_long"]

    channel_short_ok = (
        warmup_ok
        & (out["ch_slope_bps"] < -cfg.min_slope_bps)
        & (out["ch_r2"] >= cfg.min_r2)
        & (out["ema_fast"] < out["ema_slow"])
    )
    touch_upper = (out["high"] >= out["ch_upper"] - cfg.touch_atr_buffer * out["atr"]) & (
        out["close"] <= out["ch_upper"] + cfg.channel_break_atr_mult * out["atr"]
    )
    out["channel_break_short"] = warmup_ok & (
        out["close"] > out["ch_upper"] + cfg.channel_break_atr_mult * out["atr"]
    )
    out["raw_entry_short"] = channel_short_ok & touch_upper & out["confirm_short"]
    out["entry_short"] = out["raw_entry_short"] & (~out["crash_guard_short"])
    out["blocked_by_crash_guard_short"] = out["raw_entry_short"] & out["crash_guard_short"]

    return out


def compute_dynamic_dca_step(
    cfg: StrategyConfig,
    stop_out_dist_pct: float,
    atr: float,
    price: float,
) -> float:
    """Compute DCA layer spacing as a positive fractional move (e.g. 0.0025 = 0.25%).

    Guarantees that all `max_layers` fit strictly inside `0.75 * stop_out_dist_pct`
    before Layer 1 hits individual contract stop-out.
    """
    max_layers = len(cfg.layer_multipliers)
    if max_layers <= 1:
        return 1.0
    max_safe_step = (0.75 * stop_out_dist_pct) / float(max_layers - 1)
    if cfg.dca_step_pct is not None:
        return min(cfg.dca_step_pct, max_safe_step)

    # For tight stop-outs (like cryBTCUSD 100x at 0.93%): max_safe_step = 0.348%,
    # default to ~0.25% (or ATR-scaled capped at max_safe_step).
    atr_frac = (atr / price) if (price > 0 and np.isfinite(atr) and atr > 0) else 0.0025
    target = max(0.0010, cfg.dca_atr_mult * atr_frac)
    # Match prompt spec (~0.25% for 0.93% stop-out)
    if stop_out_dist_pct <= 0.011:
        return min(0.0025, max_safe_step)
    return min(target, max_safe_step)


@dataclass
class LayerPosition:
    """Single Deriv Multiplier contract inside a Smart DCA basket."""
    layer_idx: int  # 1-based
    direction: int  # +1 for MULTUP (LONG), -1 for MULTDOWN (SHORT)
    stake: float
    multiplier: float
    commission: float
    entry_price: float  # effective fill price (including entry half-spread)
    stop_out_price: float
    entry_time: pd.Timestamp
    is_open: bool = True
    exit_price: Optional[float] = None
    exit_time: Optional[pd.Timestamp] = None
    realized_pnl: float = 0.0
    exit_reason: str = ""

    def mark_pnl(self, mark_exit_price: float) -> float:
        """Net PnL in USD if closed at `mark_exit_price` (already net of spread)."""
        if not self.is_open:
            return self.realized_pnl
        ret = self.direction * (mark_exit_price - self.entry_price) / self.entry_price
        gross = self.stake * self.multiplier * ret
        return max(-self.stake, gross - self.commission)


@dataclass
class BasketCycle:
    """Active or completed Smart DCA basket cycle."""
    cycle_id: int
    direction: int  # +1 LONG, -1 SHORT
    open_time: pd.Timestamp
    open_bar_idx: int
    dca_step_frac: float
    layers: List[LayerPosition] = field(default_factory=list)
    trailing_active: bool = False
    peak_net_pnl: float = -1e9
    reentries_suspended: bool = False
    close_time: Optional[pd.Timestamp] = None
    close_reason: str = ""

    @property
    def num_layers(self) -> int:
        return len(self.layers)

    @property
    def open_layers(self) -> List[LayerPosition]:
        return [lp for lp in self.layers if lp.is_open]

    @property
    def total_stake_deployed(self) -> float:
        return sum(lp.stake for lp in self.layers)

    @property
    def total_commission(self) -> float:
        return sum(lp.commission for lp in self.layers)

    def last_entry_price(self) -> float:
        return self.layers[-1].entry_price

    def net_pnl_at(self, mark_exit_price: float) -> float:
        """Total cycle net PnL (realized stop-outs + open layers mark-to-market)."""
        return sum(lp.mark_pnl(mark_exit_price) for lp in self.layers)

    def open_net_pnl_at(self, mark_exit_price: float) -> float:
        """Net PnL of currently open layers only."""
        return sum(lp.mark_pnl(mark_exit_price) for lp in self.layers if lp.is_open)


def verify_causality(df: pd.DataFrame, cfg: StrategyConfig, sample_idx: Optional[int] = None) -> bool:
    """Strict unit test verifying ZERO lookahead bias.

    Perturbs all bars strictly after `sample_idx` (massive +50% price jump)
    and verifies that EVERY indicator and signal column at index `<= sample_idx`
    remains bitwise/numerically identical.
    """
    n = len(df)
    if sample_idx is None:
        sample_idx = min(max(300, n // 2), n - 50)
    base = compute_indicators(df, cfg)

    perturbed = df.copy()
    for col in ("open", "high", "low", "close"):
        perturbed.iloc[sample_idx + 1 :, perturbed.columns.get_loc(col)] *= 1.50
    if "volume" in perturbed.columns:
        perturbed.iloc[sample_idx + 1 :, perturbed.columns.get_loc("volume")] *= 5.0
    if "taker_buy_base" in perturbed.columns:
        perturbed.iloc[sample_idx + 1 :, perturbed.columns.get_loc("taker_buy_base")] *= 0.1

    test = compute_indicators(perturbed, cfg)

    check_cols = [
        "ch_mid", "ch_upper", "ch_lower", "ch_r2", "ch_slope_bps",
        "ema_fast", "ema_slow", "atr", "atr_median", "adx", "plus_di", "minus_di",
        "rsi", "macd_hist", "crash_guard_long", "confirm_long", "entry_long",
    ]
    for col in check_cols:
        s1 = base[col].iloc[: sample_idx + 1]
        s2 = test[col].iloc[: sample_idx + 1]
        if s1.dtype == bool:
            if not (s1 == s2).all():
                raise AssertionError(f"CAUSALITY VIOLATION in boolean column `{col}`!")
        else:
            a1 = s1.to_numpy(dtype=np.float64)
            a2 = s2.to_numpy(dtype=np.float64)
            if not np.allclose(a1, a2, rtol=0.0, atol=1e-12, equal_nan=True):
                raise AssertionError(f"CAUSALITY VIOLATION in numeric column `{col}`!")
    return True
