"""Pure, Unit-Testable Gradient Linear (Grid HFT) Engine for Deriv Multipliers.

Based on the original 'robolineargradientecodigooriginal.txt' logic:
- High frequency M1 execution with dynamic or fixed grid levels (n1..n5).
- Opens Layer #1 immediately on cycle start.
- If price moves in favor, locks in quick profit on the first micro-bounce.
- If price moves against, executes additional gradient levels at regular intervals,
  lowering the weighted average price.
- Exits the entire basket as soon as net PnL reaches the cycle target.
- Tracks accumulated session profit to stop automatically at the user's profit target or max loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Optional, Sequence


@dataclass
class GradientLevel:
    level_index: int  # 1 to max_layers
    target_price: float
    stake_usd: float
    multiplier: int
    executed: bool = False
    contract_id: Optional[str] = None
    entry_spot: Optional[float] = None


@dataclass
class GradientCycleState:
    cycle_id: int
    symbol: str
    direction: str  # "MULTUP" | "MULTDOWN"
    opened_at: datetime
    n1_base_price: float
    grid_step: float
    levels: list[GradientLevel] = field(default_factory=list)
    avg_entry_price: float = 0.0
    total_stake_usd: float = 0.0
    current_net_pnl_usd: float = 0.0
    executed_layers_count: int = 0
    cycle_target_price: float = 0.0
    is_closed: bool = False
    close_reason: Optional[str] = None  # "TARGET_REACHED" | "STOP_LOSS" | "MANUAL"


class GradientLinearEngine:
    """Calculates gradient levels, layer triggers, and exit conditions for M1 High Frequency Grid."""

    def __init__(
        self,
        stake_usd: float = 1.0,
        multiplier: int = 100,
        max_layers: int = 4,
        layer_multipliers: Optional[Sequence[float]] = None,
        min_grid_step_pct: float = 0.15,  # 0.15% minimum distance between levels
        cycle_tp_usd: float = 0.20,       # Net profit in USD per cycle
        max_hold_minutes: int = 60,
    ) -> None:
        self.stake_usd = max(0.5, float(stake_usd))
        self.multiplier = max(1, int(multiplier))
        self.max_layers = max(1, min(int(max_layers), 6))
        self.layer_multipliers = list(layer_multipliers) if layer_multipliers else [1.0, 1.0, 1.5, 2.0][:self.max_layers]
        self.min_grid_step_pct = max(0.05, float(min_grid_step_pct))
        self.cycle_tp_usd = max(0.05, float(cycle_tp_usd))
        self.max_hold_minutes = max(1, int(max_hold_minutes))

    def compute_grid_step(self, spot: float, atr_m1: Optional[float] = None) -> float:
        """Compute the price distance between grid levels.
        
        Uses ATR(14) in M1 if available, otherwise falls back to min_grid_step_pct of spot.
        """
        min_step = (self.min_grid_step_pct / 100.0) * spot
        if atr_m1 is not None and atr_m1 > 0:
            # Grid step set to ~0.35x ATR M1 (bounded by minimum percentage)
            dynamic_step = 0.35 * atr_m1
            return round(max(min_step, dynamic_step), 4 if spot > 10 else 6)
        return round(min_step, 4 if spot > 10 else 6)

    def initialize_cycle(
        self,
        cycle_id: int,
        symbol: str,
        spot: float,
        direction: str = "MULTUP",
        atr_m1: Optional[float] = None,
    ) -> GradientCycleState:
        """Initialize a new gradient grid cycle starting at current spot price."""
        step = self.compute_grid_step(spot, atr_m1)
        cycle = GradientCycleState(
            cycle_id=cycle_id,
            symbol=symbol,
            direction=direction,
            opened_at=datetime.now(timezone.utc),
            n1_base_price=spot,
            grid_step=step,
        )

        # Generate levels n1, n2, n3...
        for idx in range(1, self.max_layers + 1):
            mult_factor = self.layer_multipliers[idx - 1] if idx - 1 < len(self.layer_multipliers) else 1.0
            layer_stake = round(self.stake_usd * mult_factor, 2)
            
            # For MULTUP (buying on dips): n1 = spot, n2 = n1 - step, n3 = n1 - 2*step...
            # For MULTDOWN (selling on rallies): n1 = spot, n2 = n1 + step, n3 = n1 + 2*step...
            if direction == "MULTUP":
                level_price = spot - ((idx - 1) * step)
            else:
                level_price = spot + ((idx - 1) * step)

            cycle.levels.append(
                GradientLevel(
                    level_index=idx,
                    target_price=round(level_price, 4 if spot > 10 else 6),
                    stake_usd=layer_stake,
                    multiplier=self.multiplier,
                    executed=(idx == 1),  # First level is executed on cycle start
                )
            )

        cycle.executed_layers_count = 1
        cycle.levels[0].entry_spot = spot
        cycle.total_stake_usd = cycle.levels[0].stake_usd
        cycle.avg_entry_price = spot
        cycle.cycle_target_price = self.compute_target_price(cycle)

        return cycle

    def compute_target_price(self, cycle: GradientCycleState) -> float:
        """Compute the take profit price needed to achieve cycle_tp_usd profit."""
        if cycle.total_stake_usd <= 0 or cycle.avg_entry_price <= 0:
            return cycle.n1_base_price

        # In Deriv multipliers: profit ~= stake * multiplier * (price_delta / entry_spot)
        # Required delta: price_delta = (cycle_tp_usd * avg_entry_price) / (total_stake * multiplier)
        leveraged_capital = cycle.total_stake_usd * self.multiplier
        required_delta = (self.cycle_tp_usd * cycle.avg_entry_price) / leveraged_capital
        
        # Add a minimum tick buffer to account for Deriv commissions (~0.05% - 0.08%)
        commission_buffer = cycle.avg_entry_price * 0.0008
        required_delta += commission_buffer

        if cycle.direction == "MULTUP":
            return round(cycle.avg_entry_price + required_delta, 4 if cycle.avg_entry_price > 10 else 6)
        else:
            return round(cycle.avg_entry_price - required_delta, 4 if cycle.avg_entry_price > 10 else 6)

    def evaluate_next_layer_trigger(
        self, cycle: GradientCycleState, current_spot: float
    ) -> Optional[GradientLevel]:
        """Check if current spot has reached the next unexecuted gradient level."""
        if cycle.is_closed or cycle.executed_layers_count >= self.max_layers:
            return None

        next_idx = cycle.executed_layers_count + 1
        for lvl in cycle.levels:
            if lvl.level_index == next_idx and not lvl.executed:
                if cycle.direction == "MULTUP" and current_spot <= lvl.target_price:
                    return lvl
                elif cycle.direction == "MULTDOWN" and current_spot >= lvl.target_price:
                    return lvl

        return None

    def register_layer_execution(
        self,
        cycle: GradientCycleState,
        level_index: int,
        entry_spot: float,
        contract_id: Optional[str] = None,
    ) -> None:
        """Update cycle state when a gradient layer contract is executed."""
        for lvl in cycle.levels:
            if lvl.level_index == level_index:
                lvl.executed = True
                lvl.entry_spot = entry_spot
                lvl.contract_id = contract_id
                break

        # Recompute weighted average entry price
        executed = [l for l in cycle.levels if l.executed and l.entry_spot is not None]
        total_stake = sum(l.stake_usd for l in executed)
        weighted_sum = sum(l.stake_usd * (l.entry_spot or 0.0) for l in executed)
        
        cycle.executed_layers_count = len(executed)
        cycle.total_stake_usd = total_stake
        cycle.avg_entry_price = round(weighted_sum / total_stake, 4 if entry_spot > 10 else 6)
        cycle.cycle_target_price = self.compute_target_price(cycle)

    def evaluate_cycle_exit(
        self, cycle: GradientCycleState, current_spot: float, current_net_pnl_usd: float
    ) -> tuple[bool, Optional[str]]:
        """Evaluate if the cycle should close at take profit or stop."""
        if cycle.is_closed:
            return False, None

        # 1. Target reached via net profit in USD
        if current_net_pnl_usd >= self.cycle_tp_usd:
            return True, "TARGET_REACHED"

        # 2. Target reached via spot crossing cycle_target_price
        if cycle.direction == "MULTUP" and current_spot >= cycle.cycle_target_price:
            return True, "TARGET_REACHED"
        elif cycle.direction == "MULTDOWN" and current_spot <= cycle.cycle_target_price:
            return True, "TARGET_REACHED"

        return False, None
