"""High-Frequency 1Hz Tick-by-Tick Mean Reversion Engine with Inventory Asymmetry.

Quantitative Rules:
1. Microstructure Ring Buffer: Evaluates on every tick (1Hz) from Deriv WebSocket for 1HZ100V.
2. Direction & Exhaustion Filter (Z-Score + EMA-20):
   - EMA of 20 ticks: alpha = 2 / (20 + 1) = 0.095238.
   - Sample Standard Deviation (sigma_60) over rolling 60 ticks.
   - Z-Score = (Price_t - EMA_20) / sigma_60.
   - Anti-Trend Rule:
     * COMPRA (MULTUP): Z <= -2.0 AND Price_t > Price_{t-1} (positive inflection, exhaustion of drop).
     * VENDA (MULTDOWN): Z >= +2.0 AND Price_t < Price_{t-1} (negative inflection, exhaustion of rally).
3. Dynamic DCA Grid Spacing by Volatility (sigma_60):
   - Only triggers next layer if adverse price excursion >= 1.8 * sigma_60 from previous layer.
   - Must have exhaustion tick turn in favor of position.
   - Maximum basket size: strictly 3 layers (1x, 1.5x, 2x).
4. Dynamic Take Profit with Deriv Commission Protection ($0.05 per $1.00):
   - Multiplier 100x has ~5% entry commission ($0.05 per $1.00 stake).
   - Minimum net profit target = max(base_tp, 2.5 * total_commissions_paid).
     * 1 layer ($1.00 stake): comm=$0.05 -> net TP target >= $0.20
     * 2 layers ($2.50 stake): comm=$0.13 -> net TP target >= $0.325
     * 3 layers ($4.50 stake): comm=$0.23 -> net TP target >= $0.575
   - Exit triggered when net PnL reaches target OR mean reversion crossed EMA20 with net profit >= min target.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class TickSnapshot:
    price: float
    epoch: int


@dataclass
class ReversionSignal:
    direction: str  # "MULTUP" | "MULTDOWN"
    price: float
    z_score: float
    ema_20: float
    std_60: float
    reason: str


@dataclass
class DcaLayerSignal:
    level_index: int  # 2 or 3
    stake_usd: float
    price: float
    delta_sigma: float
    reason: str


@dataclass
class ExitSignal:
    should_close: bool
    reason: str
    net_pnl_usd: float
    min_required_tp_usd: float


class TickMeanReversionEngine:
    def __init__(
        self,
        base_stake_usd: float = 1.0,
        multiplier: int = 100,
        max_layers: int = 3,
        layer_multipliers: Optional[Sequence[float]] = None,
        z_threshold: float = 2.0,
        dca_sigma_mult: float = 1.8,
        commission_buffer_mult: float = 2.5,
        base_tp_usd: float = 0.20,
    ) -> None:
        self.base_stake_usd = float(base_stake_usd)
        self.multiplier = int(multiplier)
        self.max_layers = min(3, max(1, int(max_layers)))
        self.layer_multipliers = list(layer_multipliers or [1.0, 1.5, 2.0])[: self.max_layers]
        self.z_threshold = float(z_threshold)
        self.dca_sigma_mult = float(dca_sigma_mult)
        self.commission_buffer_mult = float(commission_buffer_mult)
        self.base_tp_usd = float(base_tp_usd)

        # Ring buffer for the last 120 ticks
        self._ticks: deque[TickSnapshot] = deque(maxlen=120)
        self._ema_20: Optional[float] = None
        self._ema_alpha: float = 2.0 / (20.0 + 1.0)  # ~0.095238
        self._prev_z: Optional[float] = None
        self._current_z: Optional[float] = None
        self._current_sigma: Optional[float] = None

    def update_tick(self, price: float, epoch: int) -> None:
        """Feed a new 1Hz tick into the microstructure ring buffer and update EMA-20."""
        if price <= 0.0:
            return

        self._ticks.append(TickSnapshot(price=price, epoch=epoch))

        # Update EMA-20
        if self._ema_20 is None:
            self._ema_20 = price
        else:
            self._ema_20 = (self._ema_alpha * price) + ((1.0 - self._ema_alpha) * self._ema_20)

        # Update rolling Z-score
        self._prev_z = self._current_z
        _, self._current_sigma, self._current_z = self.compute_stats()

    @property
    def ticks_count(self) -> int:
        return len(self._ticks)

    @property
    def ema_20(self) -> float:
        return self._ema_20 or 0.0

    def compute_stats(self) -> tuple[float, float, float]:
        """Compute (EMA-20, sigma_60, Z-Score) on the current tick buffer.
        
        Returns (price, 0.0, 0.0) if buffer has fewer than 2 ticks.
        """
        n = len(self._ticks)
        if n < 1 or self._ema_20 is None:
            return 0.0, 0.0, 0.0

        current_price = self._ticks[-1].price
        if n < 2:
            return self._ema_20, 0.0, 0.0

        sample_window = list(self._ticks)[-min(60, n) :]
        k = len(sample_window)
        prices = [t.price for t in sample_window]
        mean_px = sum(prices) / k
        variance = sum((p - mean_px) ** 2 for p in prices) / max(1, k - 1)
        sigma = math.sqrt(max(1e-8, variance))

        z_score = (current_price - self._ema_20) / sigma if sigma > 1e-6 else 0.0
        return self._ema_20, sigma, z_score

    def evaluate_entry(self, direction_mode: str = "BOTH") -> Optional[ReversionSignal]:
        """Evaluate Layer #1 Entry Signal with Z-Score + Exhaustion Inflection.
        
        Rules:
        - COMPRA (MULTUP): Z <= -2.0 AND Price_t > Price_{t-1}
        - VENDA (MULTDOWN): Z >= +2.0 AND Price_t < Price_{t-1}
        """
        if len(self._ticks) < 20 or self._ema_20 is None:
            return None

        current_price = self._ticks[-1].price
        prev_price = self._ticks[-2].price
        ema, sigma, z = self.compute_stats()

        if sigma <= 0.0:
            return None

        allow_long = direction_mode.upper() in ("BOTH", "LONG_ONLY")
        allow_short = direction_mode.upper() in ("BOTH", "SHORT_ONLY")

        oversold = (z <= -self.z_threshold) or (self._prev_z is not None and self._prev_z <= -self.z_threshold)
        overbought = (z >= self.z_threshold) or (self._prev_z is not None and self._prev_z >= self.z_threshold)

        # 1) Oversold Reversal (COMPRA / MULTUP)
        if allow_long and oversold:
            # Positive inflection: price stopped dropping and ticked up
            if current_price > prev_price:
                return ReversionSignal(
                    direction="MULTUP",
                    price=current_price,
                    z_score=z,
                    ema_20=ema,
                    std_60=sigma,
                    reason=(
                        f"Z-Score={z:.2f} (prev={self._prev_z or 0.0:.2f} <= -{self.z_threshold:.1f}, "
                        f"EMA20={ema:.2f}, σ={sigma:.2f}) com inflexão altista (+{current_price - prev_price:.4f})"
                    ),
                )

        # 2) Overbought Reversal (VENDA / MULTDOWN)
        if allow_short and overbought:
            # Negative inflection: price stopped climbing and ticked down
            if current_price < prev_price:
                return ReversionSignal(
                    direction="MULTDOWN",
                    price=current_price,
                    z_score=z,
                    ema_20=ema,
                    std_60=sigma,
                    reason=(
                        f"Z-Score=+{z:.2f} (prev={self._prev_z or 0.0:.2f} >= +{self.z_threshold:.1f}, "
                        f"EMA20={ema:.2f}, σ={sigma:.2f}) com inflexão baixista ({current_price - prev_price:.4f})"
                    ),
                )

        return None

    def evaluate_dca_layer(
        self,
        direction: str,
        current_layer_count: int,
        last_layer_entry_spot: float,
    ) -> Optional[DcaLayerSignal]:
        """Evaluate DCA Re-entry trigger for Layers 2 and 3.
        
        Rules:
        - Max 3 layers total.
        - Distance must exceed 1.8 * sigma_60 from last layer.
        - Must show exhaustion inflection tick.
        """
        if current_layer_count >= self.max_layers:
            return None
        if len(self._ticks) < 10 or last_layer_entry_spot <= 0.0:
            return None

        current_price = self._ticks[-1].price
        prev_price = self._ticks[-2].price
        ema, sigma, z = self.compute_stats()
        if sigma <= 0.0:
            return None

        min_distance = self.dca_sigma_mult * sigma
        next_level = current_layer_count + 1
        mult = self.layer_multipliers[current_layer_count] if current_layer_count < len(self.layer_multipliers) else 1.5
        stake = round(self.base_stake_usd * mult, 2)

        if direction == "MULTUP":
            adverse_excursion = last_layer_entry_spot - current_price
            if adverse_excursion >= min_distance and current_price > prev_price:
                delta_sigma = adverse_excursion / sigma
                return DcaLayerSignal(
                    level_index=next_level,
                    stake_usd=stake,
                    price=current_price,
                    delta_sigma=delta_sigma,
                    reason=(
                        f"Recuo adverso de {adverse_excursion:.4f} ({delta_sigma:.2f}σ >= {self.dca_sigma_mult:.1f}σ) "
                        f"com tick de virada altista"
                    ),
                )

        elif direction == "MULTDOWN":
            adverse_excursion = current_price - last_layer_entry_spot
            if adverse_excursion >= min_distance and current_price < prev_price:
                delta_sigma = adverse_excursion / sigma
                return DcaLayerSignal(
                    level_index=next_level,
                    stake_usd=stake,
                    price=current_price,
                    delta_sigma=delta_sigma,
                    reason=(
                        f"Recuo adverso de +{adverse_excursion:.4f} ({delta_sigma:.2f}σ >= {self.dca_sigma_mult:.1f}σ) "
                        f"com tick de virada baixista"
                    ),
                )

        return None

    def compute_min_net_tp_target(self, total_commission_usd: float) -> float:
        """Compute the dynamic net profit take-profit target protected against commissions.
        
        Formula: max(base_tp, 2.5 * total_commissions_paid).
        """
        comm_shield = self.commission_buffer_mult * max(0.0, total_commission_usd)
        return max(self.base_tp_usd, round(comm_shield, 4))

    def evaluate_exit(
        self,
        direction: str,
        current_net_pnl_usd: float,
        total_commission_usd: float,
    ) -> ExitSignal:
        """Evaluate Take Profit Exit condition.
        
        Rules:
        - Net PnL must be >= min_net_tp_target (protected against 2.5x commissions).
        - Additional bonus exit: if net PnL >= min_net_tp_target AND price has reverted across EMA-20.
        """
        min_tp = self.compute_min_net_tp_target(total_commission_usd)

        if current_net_pnl_usd >= min_tp:
            # Check if mean reversion was achieved across EMA-20
            if len(self._ticks) > 0 and self._ema_20 is not None:
                curr_px = self._ticks[-1].price
                if (direction == "MULTUP" and curr_px >= self._ema_20) or (
                    direction == "MULTDOWN" and curr_px <= self._ema_20
                ):
                    return ExitSignal(
                        should_close=True,
                        reason="MEAN_REVERSION_EMA20",
                        net_pnl_usd=current_net_pnl_usd,
                        min_required_tp_usd=min_tp,
                    )

            # Target reached directly
            return ExitSignal(
                should_close=True,
                reason="TARGET_REACHED_SHIELDED",
                net_pnl_usd=current_net_pnl_usd,
                min_required_tp_usd=min_tp,
            )

        return ExitSignal(
            should_close=False,
            reason="HOLDING",
            net_pnl_usd=current_net_pnl_usd,
            min_required_tp_usd=min_tp,
        )
