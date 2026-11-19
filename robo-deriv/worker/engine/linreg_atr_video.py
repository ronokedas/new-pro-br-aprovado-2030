"""100% Causal implementation of the Linear Regression Channel + ATR(10) Scalp Mode
(inspired by `RegressaoLinearYT` — ProfitChart NTSL, adapted safely for Deriv Multipliers).

Exact rules:
1. Causal Linear Regression Channel over trailing `channel_window` (default 50) closed candles
   with +/- `channel_sigma` (default 2.0) residual standard deviation bands:
     - LinC (Upper band = mid + sigma * std)
     - LinM (Center regression line)
     - LinB (Lower band = mid - sigma * std)
2. Containment Guard on latest closed candle:
     - High < LinC AND Low > LinB (prevents market-chasing when already outside channel).
3. Directional Split via Center Line (`LinM`):
     - Close < LinM -> arm BUY LIMIT (`MULTUP`) at `LinB` (triggers intrabar when live spot <= LinB).
     - Close > LinM -> arm SELL LIMIT (`MULTDOWN`) at `LinC` (triggers intrabar when live spot >= LinC).
4. Volatility-Scaled Exits via `AvgTrueRange(atr_period)` (default 10):
     - Take Profit = avg_entry_price +/- (tp_atr_mult * ATR)  [default 0.75 * ATR(10)]
     - Stop Loss   = first_entry_price -/+ (sl_atr_mult * ATR) [default 1.90 * ATR(10)]
5. Optional Enhancements integrated cleanly:
     - `use_crash_guard`: blocks arming against extreme opposing ADX(>35) or ATR(>2.5x median) spikes.
     - Compatible with Smart DCA (`max_layers` 1..3): when extra layers open, TP shifts dynamically
       around the basket's new `avg_entry_price`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence
import numpy as np

from engine.channel_detector import ChannelSnapshot


@dataclass
class ArmedBandOrder:
    direction: str  # "MULTUP" | "MULTDOWN"
    trigger_price: float
    atr_value: float
    lin_upper: float
    lin_mid: float
    lin_lower: float
    r2: float
    slope: float
    armed_at_epoch: int
    symbol: str = ""


def compute_atr_simple(candles: Sequence[Mapping[str, Any]], period: int = 10) -> float:
    """Compute strictly causal simple average True Range (`AvgTrueRange(period, 0)`)."""
    n = len(candles)
    if n < 2:
        return 0.0
    p = max(2, int(period))
    tail = candles[-(p + 1):]
    trs: list[float] = []
    for i in range(1, len(tail)):
        h = float(tail[i]["high"])
        l = float(tail[i]["low"])
        prev_c = float(tail[i - 1]["close"])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if not trs:
        return 0.0
    return float(np.mean(trs[-p:]))


def compute_linreg_bands(
    candles: Sequence[Mapping[str, Any]],
    window: int = 50,
    sigma_mult: float = 2.0,
) -> Optional[dict[str, float]]:
    """Compute 100% causal right-endpoint OLS Linear Regression Channel on closed candles."""
    w = max(10, int(window))
    if len(candles) < w:
        return None
    y = np.array([float(c["close"]) for c in candles[-w:]], dtype=np.float64)
    x = np.arange(w, dtype=np.float64)
    x_mean = 0.5 * (w - 1)
    xc = x - x_mean
    s_xx = float(np.sum(xc * xc))
    if s_xx <= 1e-18:
        return None

    y_mean = float(np.mean(y))
    slope = float(np.dot(xc, y - y_mean) / s_xx)
    intercept = y_mean - slope * x_mean
    yhat = intercept + slope * x
    resid = y - yhat
    ss_res = float(np.dot(resid, resid))
    ss_tot = float(np.dot(y - y_mean, y - y_mean))
    std = float(np.sqrt(ss_res / float(w)))
    r2 = float(np.clip(1.0 - (ss_res / ss_tot), 0.0, 1.0)) if ss_tot > 1e-18 else 0.0

    lin_mid = float(yhat[-1])
    lin_upper = float(lin_mid + float(sigma_mult) * std)
    lin_lower = float(lin_mid - float(sigma_mult) * std)
    return {
        "upper": lin_upper,
        "mid": lin_mid,
        "lower": lin_lower,
        "std": std,
        "r2": r2,
        "slope": slope,
    }


def arm_video_band_order(
    candles: Sequence[Mapping[str, Any]],
    direction_mode: str = "BOTH",
    channel_window: int = 50,
    channel_sigma: float = 2.0,
    atr_period: int = 10,
    use_crash_guard: bool = True,
    channel: Optional[ChannelSnapshot] = None,
) -> Optional[ArmedBandOrder]:
    """Evaluate closed bar containment & arm a limit touch trigger on LinB or LinC."""
    if not candles:
        return None
    bands = compute_linreg_bands(candles, window=channel_window, sigma_mult=channel_sigma)
    if bands is None or bands["std"] <= 1e-12:
        return None
    atr_val = compute_atr_simple(candles, period=atr_period)
    if atr_val <= 1e-12:
        return None

    last = candles[-1]
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])
    epoch = int(last.get("epoch") or last.get("time") or 0)

    lin_upper = bands["upper"]
    lin_mid = bands["mid"]
    lin_lower = bands["lower"]

    # Containment guard exact from video: (High < LinC) and (Low > LinB)
    if not (h < lin_upper and l > lin_lower):
        return None

    mode = (direction_mode or "BOTH").upper()
    direction: Optional[str] = None
    trigger_price: float = 0.0

    if c < lin_mid and mode in ("LONG_ONLY", "BOTH"):
        direction = "MULTUP"
        trigger_price = lin_lower
    elif c > lin_mid and mode in ("SHORT_ONLY", "BOTH"):
        direction = "MULTDOWN"
        trigger_price = lin_upper
    else:
        return None

    # Optional Crash Guard protection
    if use_crash_guard and channel is not None:
        if direction == "MULTUP" and getattr(channel, "crash_guard_long", False):
            return None
        if direction == "MULTDOWN" and getattr(channel, "crash_guard_short", False):
            return None

    return ArmedBandOrder(
        direction=direction,
        trigger_price=trigger_price,
        atr_value=atr_val,
        lin_upper=lin_upper,
        lin_mid=lin_mid,
        lin_lower=lin_lower,
        r2=bands["r2"],
        slope=bands["slope"],
        armed_at_epoch=epoch,
        symbol=str(getattr(channel, "symbol", "") or ""),
    )


def check_band_touch(armed: Optional[ArmedBandOrder], live_spot: float) -> bool:
    """Return True if live tick touches/crosses the armed limit band price."""
    if armed is None or live_spot <= 0.0:
        return False
    if armed.direction == "MULTUP":
        return live_spot <= armed.trigger_price
    if armed.direction == "MULTDOWN":
        return live_spot >= armed.trigger_price
    return False


def compute_atr_exit_levels(
    direction: str,
    avg_entry_price: float,
    first_entry_price: float,
    atr_value: float,
    tp_atr_mult: float = 0.75,
    sl_atr_mult: float = 1.90,
) -> tuple[float, float]:
    """Return (tp_price, sl_price) scaled dynamically by ATR."""
    tp_dist = max(0.0, float(tp_atr_mult) * float(atr_value))
    sl_dist = max(0.0, float(sl_atr_mult) * float(atr_value))
    if direction == "MULTUP":
        return avg_entry_price + tp_dist, first_entry_price - sl_dist
    return avg_entry_price - tp_dist, first_entry_price + sl_dist


def evaluate_atr_price_exit(
    direction: str,
    live_spot: float,
    tp_price: float,
    sl_price: float,
) -> Optional[str]:
    """Check dynamic ATR exit levels conservatively (SL checked before TP)."""
    if live_spot <= 0.0:
        return None
    if direction == "MULTUP":
        if sl_price > 0.0 and live_spot <= sl_price:
            return "SL_ATR"
        if tp_price > 0.0 and live_spot >= tp_price:
            return "TP_ATR"
    elif direction == "MULTDOWN":
        if sl_price > 0.0 and live_spot >= sl_price:
            return "SL_ATR"
        if tp_price > 0.0 and live_spot <= tp_price:
            return "TP_ATR"
    return None
