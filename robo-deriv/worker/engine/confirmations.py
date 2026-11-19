"""100% Causal Entry and Smart DCA Re-entry Confirmations.

Evaluates:
1. Price Action rejection: lower/upper wick ratio >= 0.40 or strong close past candle midpoint
   after testing the channel boundary.
2. Indicator Turn: RSI(14) turning up from oversold (< 35) / down from overbought (> 65),
   OR MACD(12,26,9) histogram decelerating for 2 consecutive bars.
3. Optional Binance Order Flow Boost (for cryBTCUSD): positive/negative 15s delta or absorption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence
import numpy as np

from engine.channel_detector import ChannelSnapshot, compute_ema


def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute 100% causal Wilder's RSI(14)."""
    n = len(closes)
    if n == 0:
        return np.array([], dtype=np.float64)
    rsi = np.full(n, 50.0, dtype=np.float64)
    if n <= period:
        return rsi

    deltas = np.diff(closes)
    gains = np.where(deltas > 0.0, deltas, 0.0)
    losses = np.where(deltas < 0.0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    def _calc(ag: float, al: float) -> float:
        if al <= 1e-12:
            return 100.0 if ag > 1e-12 else 50.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    rsi[period] = _calc(avg_gain, avg_loss)
    for i in range(period + 1, n):
        g = float(gains[i - 1])
        l = float(losses[i - 1])
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        rsi[i] = _calc(avg_gain, avg_loss)
    return rsi


def compute_macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute 100% causal MACD(12, 26, 9) line, signal line, and histogram."""
    if len(closes) == 0:
        empty = np.array([], dtype=np.float64)
        return empty, empty, empty
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def wick_ratios(candle: Mapping[str, Any]) -> tuple[float, float]:
    """Return (lower_wick_ratio, upper_wick_ratio) relative to total candle range."""
    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])
    rng = h - l
    if rng <= 1e-12:
        return 0.0, 0.0
    lower_wick = max(0.0, min(o, c) - l)
    upper_wick = max(0.0, h - max(o, c))
    return lower_wick / rng, upper_wick / rng


@dataclass
class SignalDecision:
    should_enter: bool
    direction: Optional[str] = None  # "MULTUP" | "MULTDOWN" | None
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    rsi: float = 50.0
    rsi_prev: float = 50.0
    macd_hist: float = 0.0
    wick_ratio: float = 0.0
    blocked_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_enter": self.should_enter,
            "direction": self.direction,
            "score": self.score,
            "reasons": list(self.reasons),
            "rsi": round(float(self.rsi), 2),
            "rsi_prev": round(float(self.rsi_prev), 2),
            "macd_hist": round(float(self.macd_hist), 6),
            "wick_ratio": round(float(self.wick_ratio), 4),
            "blocked_reason": self.blocked_reason,
        }


def _evaluate_confirmations(
    direction: str,
    closed: Sequence[Mapping[str, Any]],
    touched_band: bool,
    binance_state: Optional[Mapping[str, Any]] = None,
    wick_threshold: float = 0.40,
) -> tuple[int, list[str], float, float, float, float, bool, bool]:
    """Evaluate causal confirmation criteria for MULTUP or MULTDOWN.

    Returns:
        (score, reasons, rsi_curr, rsi_prev, macd_hist_curr, wick_ratio, price_action_ok, indicator_ok)
    """
    closes = np.array([float(c["close"]) for c in closed], dtype=np.float64)
    rsi_arr = compute_rsi(closes, period=14)
    _macd, _sig, hist_arr = compute_macd(closes, fast=12, slow=26, signal=9)

    last = closed[-1]
    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])
    mid = (h + l) / 2.0
    lower_wick, upper_wick = wick_ratios(last)

    rsi_curr = float(rsi_arr[-1])
    rsi_prev = float(rsi_arr[-2]) if len(rsi_arr) >= 2 else rsi_curr
    hist_curr = float(hist_arr[-1])
    hist_prev1 = float(hist_arr[-2]) if len(hist_arr) >= 2 else hist_curr
    hist_prev2 = float(hist_arr[-3]) if len(hist_arr) >= 3 else hist_prev1

    reasons: list[str] = []
    score = 0

    if direction == "MULTUP":
        wick_ratio = lower_wick
        # Criterion 1: Price action rejection
        price_action_ok = (lower_wick >= wick_threshold) or (touched_band and c > mid and c >= o)
        if price_action_ok:
            score += 1
            if lower_wick >= wick_threshold:
                reasons.append(f"Pavio inferior forte ({lower_wick * 100:.0f}%)")
            else:
                reasons.append("Fechamento acima da metade do candle após testar banda inferior")

        # Criterion 2: Indicator turn (RSI < 35 turning up OR MACD histogram decelerating 2 bars)
        rsi_turn = (rsi_prev < 35.0 and rsi_curr > rsi_prev) or (rsi_curr < 35.0 and rsi_curr > rsi_prev)
        macd_decel = (
            len(hist_arr) >= 3
            and hist_prev2 < 0.0
            and hist_prev2 < hist_prev1 < hist_curr
        )
        indicator_ok = rsi_turn or macd_decel
        if indicator_ok:
            score += 1
            if rsi_turn:
                reasons.append(f"RSI(14) virando para cima ({rsi_prev:.1f} -> {rsi_curr:.1f})")
            if macd_decel:
                reasons.append("MACD histograma desacelerando queda por 2 velas")

        # Criterion 3: Optional Binance lead flow confirmation
        if binance_state and binance_state.get("available"):
            delta_15s = float(binance_state.get("delta_15s", 0.0))
            absorption_bull = bool(binance_state.get("absorption_bullish", False))
            if delta_15s > 0.0 or absorption_bull:
                score += 1
                if absorption_bull:
                    reasons.append("Fluxo Binance com absorção compradora")
                else:
                    reasons.append(f"Delta Binance 15s positivo (+${delta_15s:,.0f})")
    else:
        wick_ratio = upper_wick
        # Criterion 1: Price action rejection
        price_action_ok = (upper_wick >= wick_threshold) or (touched_band and c < mid and c <= o)
        if price_action_ok:
            score += 1
            if upper_wick >= wick_threshold:
                reasons.append(f"Pavio superior forte ({upper_wick * 100:.0f}%)")
            else:
                reasons.append("Fechamento abaixo da metade do candle após testar banda superior")

        # Criterion 2: Indicator turn (RSI > 65 turning down OR MACD histogram decelerating 2 bars)
        rsi_turn = (rsi_prev > 65.0 and rsi_curr < rsi_prev) or (rsi_curr > 65.0 and rsi_curr < rsi_prev)
        macd_decel = (
            len(hist_arr) >= 3
            and hist_prev2 > 0.0
            and hist_prev2 > hist_prev1 > hist_curr
        )
        indicator_ok = rsi_turn or macd_decel
        if indicator_ok:
            score += 1
            if rsi_turn:
                reasons.append(f"RSI(14) virando para baixo ({rsi_prev:.1f} -> {rsi_curr:.1f})")
            if macd_decel:
                reasons.append("MACD histograma desacelerando alta por 2 velas")

        # Criterion 3: Optional Binance lead flow confirmation
        if binance_state and binance_state.get("available"):
            delta_15s = float(binance_state.get("delta_15s", 0.0))
            absorption_bear = bool(binance_state.get("absorption_bearish", False))
            if delta_15s < 0.0 or absorption_bear:
                score += 1
                if absorption_bear:
                    reasons.append("Fluxo Binance com absorção vendedora")
                else:
                    reasons.append(f"Delta Binance 15s negativo (-${abs(delta_15s):,.0f})")

    return score, reasons, rsi_curr, rsi_prev, hist_curr, wick_ratio, price_action_ok, indicator_ok


def entry_signal(
    direction_mode: str,
    candles: Sequence[Mapping[str, Any]],
    channel: Optional[ChannelSnapshot],
    binance_state: Optional[Mapping[str, Any]] = None,
    band_tolerance_atr: float = 0.15,
    min_score: int = 2,
) -> SignalDecision:
    """Evaluate causal Entry #1 signal at the lower (LONG) or upper (SHORT) channel boundary."""
    closed = [c for c in candles if c.get("is_closed", True)]
    if channel is None or len(closed) < 30:
        return SignalDecision(should_enter=False, blocked_reason="Dados insuficientes para confirmação")

    last = closed[-1]
    low = float(last["low"])
    high = float(last["high"])
    close = float(last["close"])
    mode = (direction_mode or "LONG_ONLY").upper()

    # Check LONG (MULTUP)
    if mode in ("LONG_ONLY", "BOTH") and channel.is_bull_channel:
        if channel.crash_guard_long:
            return SignalDecision(
                should_enter=False,
                blocked_reason=f"Crash Guard bloqueou compra: {channel.crash_guard_reason}",
            )
        touched_lower = low <= (channel.lower + band_tolerance_atr * channel.atr)
        not_broken = close >= (channel.lower - 1.0 * channel.atr)
        if touched_lower and not_broken:
            score, reasons, rsi_c, rsi_p, hist_c, wr, _pa, _ind = _evaluate_confirmations(
                "MULTUP", closed, touched_lower, binance_state
            )
            if score >= min_score:
                return SignalDecision(
                    should_enter=True,
                    direction="MULTUP",
                    score=score,
                    reasons=reasons,
                    rsi=rsi_c,
                    rsi_prev=rsi_p,
                    macd_hist=hist_c,
                    wick_ratio=wr,
                )
            return SignalDecision(
                should_enter=False,
                direction="MULTUP",
                score=score,
                reasons=reasons,
                rsi=rsi_c,
                rsi_prev=rsi_p,
                macd_hist=hist_c,
                wick_ratio=wr,
                blocked_reason=f"Apenas {score}/{min_score} confirmações na banda inferior",
            )

    # Check SHORT (MULTDOWN)
    if mode in ("SHORT_ONLY", "BOTH") and channel.is_bear_channel:
        if channel.crash_guard_short:
            return SignalDecision(
                should_enter=False,
                blocked_reason=f"Crash Guard bloqueou venda: {channel.crash_guard_reason}",
            )
        touched_upper = high >= (channel.upper - band_tolerance_atr * channel.atr)
        not_broken = close <= (channel.upper + 1.0 * channel.atr)
        if touched_upper and not_broken:
            score, reasons, rsi_c, rsi_p, hist_c, wr, _pa, _ind = _evaluate_confirmations(
                "MULTDOWN", closed, touched_upper, binance_state
            )
            if score >= min_score:
                return SignalDecision(
                    should_enter=True,
                    direction="MULTDOWN",
                    score=score,
                    reasons=reasons,
                    rsi=rsi_c,
                    rsi_prev=rsi_p,
                    macd_hist=hist_c,
                    wick_ratio=wr,
                )
            return SignalDecision(
                should_enter=False,
                direction="MULTDOWN",
                score=score,
                reasons=reasons,
                rsi=rsi_c,
                rsi_prev=rsi_p,
                macd_hist=hist_c,
                wick_ratio=wr,
                blocked_reason=f"Apenas {score}/{min_score} confirmações na banda superior",
            )

    return SignalDecision(should_enter=False, blocked_reason="Sem toque confirmado na zona do canal")


def dca_reentry_signal(
    direction: str,
    candles: Sequence[Mapping[str, Any]],
    channel: Optional[ChannelSnapshot],
    last_entry_price: float,
    min_step_pct: float,
    binance_state: Optional[Mapping[str, Any]] = None,
) -> SignalDecision:
    """Evaluate causal Smart DCA re-entry signal for layers 2..N.

    Never opens in the middle of a panic candle or after structural channel break.
    Requires:
      1. No Crash Guard active against direction.
      2. No structural break beyond `band +/- 1.0 * ATR`.
      3. Adverse move from `last_entry_price` >= `min_step_pct` (fraction, e.g. 0.0025 = 0.25%).
      4. Fresh confirmation (price action rejection OR indicator turn) and not a panic marubozu.
    """
    closed = [c for c in candles if c.get("is_closed", True)]
    if channel is None or len(closed) < 30 or last_entry_price <= 0.0:
        return SignalDecision(should_enter=False, blocked_reason="Dados insuficientes para reentrada DCA")

    last = closed[-1]
    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])
    rng = max(h - l, 1e-12)
    dir_norm = direction.upper()

    if dir_norm == "MULTUP":
        if channel.crash_guard_long:
            return SignalDecision(
                should_enter=False,
                blocked_reason=f"Reentrada suspensa pelo Crash Guard: {channel.crash_guard_reason}",
            )
        if c < (channel.lower - 1.0 * channel.atr):
            return SignalDecision(
                should_enter=False,
                blocked_reason="Rompimento estrutural abaixo de lower_band - 1.0*ATR",
            )
        adverse_pct = (last_entry_price - c) / last_entry_price
        if adverse_pct < min_step_pct:
            return SignalDecision(
                should_enter=False,
                blocked_reason=f"Recuo insuficiente ({adverse_pct * 100:.3f}% < {min_step_pct * 100:.3f}%)",
            )
        # Reject panic bearish candle closing at bottom 15% of range with large red body
        panic_candle = (c < o) and ((c - l) / rng < 0.15) and ((o - c) / rng > 0.65)
        if panic_candle:
            return SignalDecision(
                should_enter=False,
                blocked_reason="Vela de pânico vendedora — aguardando rejeição",
            )
        touched_zone = l <= (channel.lower + 0.25 * channel.atr)
    else:
        if channel.crash_guard_short:
            return SignalDecision(
                should_enter=False,
                blocked_reason=f"Reentrada suspensa pelo Crash Guard: {channel.crash_guard_reason}",
            )
        if c > (channel.upper + 1.0 * channel.atr):
            return SignalDecision(
                should_enter=False,
                blocked_reason="Rompimento estrutural acima de upper_band + 1.0*ATR",
            )
        adverse_pct = (c - last_entry_price) / last_entry_price
        if adverse_pct < min_step_pct:
            return SignalDecision(
                should_enter=False,
                blocked_reason=f"Avanço adverso insuficiente ({adverse_pct * 100:.3f}% < {min_step_pct * 100:.3f}%)",
            )
        panic_candle = (c > o) and ((h - c) / rng < 0.15) and ((c - o) / rng > 0.65)
        if panic_candle:
            return SignalDecision(
                should_enter=False,
                blocked_reason="Vela de pânico compradora — aguardando rejeição",
            )
        touched_zone = h >= (channel.upper - 0.25 * channel.atr)

    score, reasons, rsi_c, rsi_p, hist_c, wr, pa_ok, ind_ok = _evaluate_confirmations(
        dir_norm, closed, touched_zone, binance_state
    )

    if pa_ok or ind_ok:
        return SignalDecision(
            should_enter=True,
            direction=dir_norm,
            score=score,
            reasons=reasons,
            rsi=rsi_c,
            rsi_prev=rsi_p,
            macd_hist=hist_c,
            wick_ratio=wr,
        )

    return SignalDecision(
        should_enter=False,
        direction=dir_norm,
        score=score,
        reasons=reasons,
        rsi=rsi_c,
        rsi_prev=rsi_p,
        macd_hist=hist_c,
        wick_ratio=wr,
        blocked_reason="Sem nova confirmação de rejeição ou virada de indicador para DCA",
    )
