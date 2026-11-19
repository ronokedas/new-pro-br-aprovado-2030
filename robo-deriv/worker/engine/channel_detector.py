"""100% Causal Linear Regression Channel Detector + Macro EMA + ATR/ADX Crash Guard.

Zero lookahead guarantee: every calculation uses strictly closed candles up to index `t`
(the end of the provided slice) and never peaks forward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence
import numpy as np


def _extract_ohlc(candles: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Extract epochs, open, high, low, close numpy float64 arrays from candle dicts."""
    n = len(candles)
    epochs = np.empty(n, dtype=np.int64)
    opens = np.empty(n, dtype=np.float64)
    highs = np.empty(n, dtype=np.float64)
    lows = np.empty(n, dtype=np.float64)
    closes = np.empty(n, dtype=np.float64)

    for i, c in enumerate(candles):
        ep = c.get("epoch", c.get("time", i))
        epochs[i] = int(ep)
        opens[i] = float(c["open"])
        highs[i] = float(c["high"])
        lows[i] = float(c["low"])
        closes[i] = float(c["close"])

    return epochs, opens, highs, lows, closes


def compute_ema(values: np.ndarray, period: int) -> np.ndarray:
    """Compute 100% causal Exponential Moving Average."""
    n = len(values)
    if n == 0:
        return np.array([], dtype=np.float64)
    ema = np.empty(n, dtype=np.float64)
    alpha = 2.0 / (period + 1.0)
    if n >= period:
        seed = float(np.mean(values[:period]))
        ema[:period] = seed
        # Fill earlier values causally with running mean so no NaN leaks
        running_sum = 0.0
        for i in range(period):
            running_sum += values[i]
            ema[i] = running_sum / (i + 1)
        ema[period - 1] = seed
        for i in range(period, n):
            ema[i] = alpha * values[i] + (1.0 - alpha) * ema[i - 1]
    else:
        ema[0] = values[0]
        for i in range(1, n):
            ema[i] = alpha * values[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute 100% causal Wilder's Average True Range (ATR)."""
    n = len(closes)
    if n == 0:
        return np.array([], dtype=np.float64)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    atr = np.empty(n, dtype=np.float64)
    if n <= period:
        running = 0.0
        for i in range(n):
            running += tr[i]
            atr[i] = running / (i + 1)
        return atr

    # Causal warm-up
    running = 0.0
    for i in range(period):
        running += tr[i]
        atr[i] = running / (i + 1)
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_adx(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute 100% causal Wilder's ADX, +DI, and -DI."""
    n = len(closes)
    if n == 0:
        empty = np.array([], dtype=np.float64)
        return empty, empty, empty

    tr = np.zeros(n, dtype=np.float64)
    plus_dm = np.zeros(n, dtype=np.float64)
    minus_dm = np.zeros(n, dtype=np.float64)

    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        if up_move > down_move and up_move > 0.0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0.0:
            minus_dm[i] = down_move

    sm_tr = np.zeros(n, dtype=np.float64)
    sm_plus = np.zeros(n, dtype=np.float64)
    sm_minus = np.zeros(n, dtype=np.float64)
    plus_di = np.zeros(n, dtype=np.float64)
    minus_di = np.zeros(n, dtype=np.float64)
    dx = np.zeros(n, dtype=np.float64)
    adx = np.zeros(n, dtype=np.float64)

    if n <= period:
        return adx, plus_di, minus_di

    sm_tr[period] = float(np.sum(tr[1 : period + 1]))
    sm_plus[period] = float(np.sum(plus_dm[1 : period + 1]))
    sm_minus[period] = float(np.sum(minus_dm[1 : period + 1]))

    for i in range(period, n):
        if i > period:
            sm_tr[i] = sm_tr[i - 1] - (sm_tr[i - 1] / period) + tr[i]
            sm_plus[i] = sm_plus[i - 1] - (sm_plus[i - 1] / period) + plus_dm[i]
            sm_minus[i] = sm_minus[i - 1] - (sm_minus[i - 1] / period) + minus_dm[i]

        if sm_tr[i] > 1e-12:
            plus_di[i] = 100.0 * sm_plus[i] / sm_tr[i]
            minus_di[i] = 100.0 * sm_minus[i] / sm_tr[i]
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 1e-12:
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / di_sum

    first_adx_idx = 2 * period
    if n > first_adx_idx:
        adx[first_adx_idx] = float(np.mean(dx[period : first_adx_idx + 1]))
        for i in range(first_adx_idx + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
        # Back-fill warm-up before 2*period with causal running mean of DX
        for i in range(period, first_adx_idx):
            adx[i] = float(np.mean(dx[period : i + 1]))
    else:
        for i in range(period, n):
            adx[i] = float(np.mean(dx[period : i + 1]))

    return adx, plus_di, minus_di


@dataclass
class ChannelSnapshot:
    symbol: str
    granularity: int
    epoch: int
    window: int
    slope: float
    intercept: float
    r2: float
    sigma: float
    center: float
    upper: float
    lower: float
    ema_50: float
    ema_200: float
    atr: float
    atr_median: float
    atr_ratio: float
    slope_atr_norm: float
    adx: float
    plus_di: float
    minus_di: float
    trend: str  # "UP" | "DOWN" | "NEUTRAL"
    is_bull_channel: bool
    is_bear_channel: bool
    crash_guard_active: bool
    crash_guard_long: bool
    crash_guard_short: bool
    crash_guard_reason: Optional[str] = None
    series: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize snapshot to JSON-friendly dict including Lightweight Charts line series."""
        upper_line = [{"time": pt["time"], "value": pt["upper"]} for pt in self.series]
        center_line = [{"time": pt["time"], "value": pt["center"]} for pt in self.series]
        lower_line = [{"time": pt["time"], "value": pt["lower"]} for pt in self.series]
        return {
            "symbol": self.symbol,
            "granularity": int(self.granularity),
            "epoch": int(self.epoch),
            "window": int(self.window),
            "slope": round(float(self.slope), 8),
            "intercept": round(float(self.intercept), 6),
            "r2": round(float(self.r2), 4),
            "sigma": round(float(self.sigma), 6),
            "center": round(float(self.center), 6),
            "upper": round(float(self.upper), 6),
            "lower": round(float(self.lower), 6),
            "ema_50": round(float(self.ema_50), 6),
            "ema_200": round(float(self.ema_200), 6),
            "atr": round(float(self.atr), 6),
            "atr_median": round(float(self.atr_median), 6),
            "atr_ratio": round(float(self.atr_ratio), 4),
            "slope_atr_norm": round(float(self.slope_atr_norm), 6),
            "adx": round(float(self.adx), 2),
            "plus_di": round(float(self.plus_di), 2),
            "minus_di": round(float(self.minus_di), 2),
            "trend": self.trend,
            "is_bull_channel": bool(self.is_bull_channel),
            "is_bear_channel": bool(self.is_bear_channel),
            "crash_guard_active": bool(self.crash_guard_active),
            "crash_guard_long": bool(self.crash_guard_long),
            "crash_guard_short": bool(self.crash_guard_short),
            "crash_guard_reason": self.crash_guard_reason,
            "series": self.series,
            "upper_line": upper_line,
            "center_line": center_line,
            "lower_line": lower_line,
        }


class ChannelDetector:
    """Causal Linear Regression Channel + EMA(50/200) + ATR(14) + ADX(14) Crash Guard."""

    def __init__(
        self,
        window: int = 60,
        band_std: float = 2.0,
        min_r2: float = 0.45,
        min_slope_atr: float = 0.02,
        adx_crash_threshold: float = 35.0,
        atr_spike_mult: float = 2.5,
        atr_period: int = 14,
        adx_period: int = 14,
        ema_fast_period: int = 50,
        ema_slow_period: int = 200,
    ) -> None:
        self.window = int(window)
        self.band_std = float(band_std)
        self.min_r2 = float(min_r2)
        self.min_slope_atr = float(min_slope_atr)
        self.adx_crash_threshold = float(adx_crash_threshold)
        self.atr_spike_mult = float(atr_spike_mult)
        self.atr_period = int(atr_period)
        self.adx_period = int(adx_period)
        self.ema_fast_period = int(ema_fast_period)
        self.ema_slow_period = int(ema_slow_period)

    def detect(
        self,
        candles: Sequence[Mapping[str, Any]],
        symbol: str = "cryBTCUSD",
        granularity: int = 300,
    ) -> Optional[ChannelSnapshot]:
        """Compute causal channel snapshot using strictly closed candles.

        Returns None if fewer than `window` candles are available.
        """
        # Filter out explicitly open candles if `is_closed` key is present
        closed = [c for c in candles if c.get("is_closed", True)]
        if len(closed) < self.window:
            return None

        epochs, _opens, highs, lows, closes = _extract_ohlc(closed)

        # Indicators over full causal history up to the latest closed bar
        ema_50_arr = compute_ema(closes, self.ema_fast_period)
        ema_200_arr = compute_ema(closes, self.ema_slow_period)
        atr_arr = compute_atr(highs, lows, closes, self.atr_period)
        adx_arr, plus_di_arr, minus_di_arr = compute_adx(highs, lows, closes, self.adx_period)

        ema_50 = float(ema_50_arr[-1])
        ema_200 = float(ema_200_arr[-1])
        atr = float(atr_arr[-1])
        adx = float(adx_arr[-1])
        plus_di = float(plus_di_arr[-1])
        minus_di = float(minus_di_arr[-1])

        # Recent median ATR (strictly causal lookback over the regression window)
        atr_slice = atr_arr[-self.window :]
        atr_median = float(np.median(atr_slice)) if len(atr_slice) > 0 else atr
        atr_ratio = (atr / atr_median) if atr_median > 1e-12 else 1.0

        # OLS Linear Regression on the last `window` closed closes
        w = self.window
        y = closes[-w:]
        win_epochs = epochs[-w:]
        x = np.arange(w, dtype=np.float64)
        x_mean = float(np.mean(x))
        y_mean = float(np.mean(y))
        dx = x - x_mean
        dy = y - y_mean
        sxx = float(np.dot(dx, dx))
        sxy = float(np.dot(dx, dy))

        slope = (sxy / sxx) if sxx > 1e-12 else 0.0
        intercept = y_mean - slope * x_mean
        fitted = intercept + slope * x
        residuals = y - fitted

        ss_res = float(np.dot(residuals, residuals))
        ss_tot = float(np.dot(dy, dy))
        if ss_tot > 1e-12:
            r2 = max(0.0, min(1.0, 1.0 - (ss_res / ss_tot)))
        else:
            r2 = 0.0

        sigma = float(np.sqrt(ss_res / w)) if w > 0 else 0.0
        center = float(fitted[-1])
        upper = center + self.band_std * sigma
        lower = center - self.band_std * sigma
        slope_atr_norm = (slope / atr) if atr > 1e-12 else 0.0

        # Crash Guard evaluation
        atr_spike = atr_ratio > self.atr_spike_mult
        adx_bear_crash = adx > self.adx_crash_threshold and minus_di > plus_di
        adx_bull_crash = adx > self.adx_crash_threshold and plus_di > minus_di

        crash_guard_long = bool(atr_spike or adx_bear_crash)
        crash_guard_short = bool(atr_spike or adx_bull_crash)
        crash_guard_active = bool(crash_guard_long or crash_guard_short)

        reasons: list[str] = []
        if atr_spike:
            reasons.append(f"ATR extremo ({atr_ratio:.2f}x mediana)")
        if adx_bear_crash:
            reasons.append(f"ADX forte contra alta (ADX={adx:.1f}, -DI={minus_di:.1f} > +DI={plus_di:.1f})")
        if adx_bull_crash:
            reasons.append(f"ADX forte contra baixa (ADX={adx:.1f}, +DI={plus_di:.1f} > -DI={minus_di:.1f})")
        crash_guard_reason = "; ".join(reasons) if reasons else None

        # Structural trend qualification
        is_bull_channel = bool(
            slope > 0.0
            and slope_atr_norm >= self.min_slope_atr
            and r2 >= self.min_r2
            and ema_50 > ema_200
        )
        is_bear_channel = bool(
            slope < 0.0
            and abs(slope_atr_norm) >= self.min_slope_atr
            and r2 >= self.min_r2
            and ema_50 < ema_200
        )

        if is_bull_channel:
            trend = "UP"
        elif is_bear_channel:
            trend = "DOWN"
        else:
            trend = "NEUTRAL"

        # Build chart-ready regression channel series across the window
        series = [
            {
                "time": int(win_epochs[i]),
                "upper": round(float(fitted[i] + self.band_std * sigma), 6),
                "center": round(float(fitted[i]), 6),
                "lower": round(float(fitted[i] - self.band_std * sigma), 6),
            }
            for i in range(w)
        ]

        return ChannelSnapshot(
            symbol=symbol,
            granularity=int(granularity),
            epoch=int(win_epochs[-1]),
            window=w,
            slope=slope,
            intercept=intercept,
            r2=r2,
            sigma=sigma,
            center=center,
            upper=upper,
            lower=lower,
            ema_50=ema_50,
            ema_200=ema_200,
            atr=atr,
            atr_median=atr_median,
            atr_ratio=atr_ratio,
            slope_atr_norm=slope_atr_norm,
            adx=adx,
            plus_di=plus_di,
            minus_di=minus_di,
            trend=trend,
            is_bull_channel=is_bull_channel,
            is_bear_channel=is_bear_channel,
            crash_guard_active=crash_guard_active,
            crash_guard_long=crash_guard_long,
            crash_guard_short=crash_guard_short,
            crash_guard_reason=crash_guard_reason,
            series=series,
        )
