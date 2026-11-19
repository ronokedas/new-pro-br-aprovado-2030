"""High-Frequency Swing Grid & Breather Accumulation Engine (Robô Acumulador de Respiros).

Quantitative Strategy:
1. Microstructure Accumulation:
   - Evaluates tick flow at 1Hz (Deriv 1HZ100V or Volatility Indices).
   - Starts cycle with Layer #1 stake ($1.00).
2. Dynamic Swing Grid Accumulation:
   - As price moves against the position, accumulates additional layers at volatility-spaced steps (sigma_60 * multiplier).
   - Each layer entry requires tick-exhaustion inflection (e.g. Price_t > Price_{t-1} for MULTUP) ensuring entry at the start of the breather ("respiro").
   - Max layers capped strictly at 3 or 4 layers (1.0x, 1.5x, 2.0x, 2.5x).
   - Preço Médio (Breakeven) drops aggressively closer to current market price with each added layer.
3. Breather Exit ("Lucrar com o Respiro"):
   - Exits the entire accumulated basket on the first relief bounce ("respiro").
   - Net profit target scales with basket size, guaranteeing real net profits after broker commissions:
     * 1 layer ($1.00 stake): Net Target >= +$0.50 USD
     * 2 layers ($2.50 stake): Net Target >= +$0.88 USD
     * 3 layers ($4.50 stake): Net Target >= +$1.58 USD
     * 4 layers ($7.00 stake): Net Target >= +$2.45 USD
4. Capital Protection:
   - Session Target: +$50.00 USD (stops bot when reached).
   - Session Stop Loss: -$100.00 USD (stops bot if hit).
   - Basket Stop Loss: -$8.00 USD (limits single cycle drawdowns).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class TickItem:
    price: float
    epoch: int


@dataclass
class GridLayer:
    layer_index: int
    stake_usd: float
    entry_spot: float
    multiplier: int = 100
    contract_id: Optional[str] = None


@dataclass
class GridCycleState:
    cycle_id: int
    direction: str  # "MULTUP" | "MULTDOWN"
    symbol: str
    layers: list[GridLayer] = field(default_factory=list)
    avg_entry_price: float = 0.0
    total_stake_usd: float = 0.0
    target_net_profit_usd: float = 0.50

    def recalculate(self) -> None:
        if not self.layers:
            self.avg_entry_price = 0.0
            self.total_stake_usd = 0.0
            self.target_net_profit_usd = 0.50
            return

        total_weight = sum(l.stake_usd for l in self.layers)
        weighted_sum = sum(l.stake_usd * l.entry_spot for l in self.layers)
        self.total_stake_usd = round(total_weight, 2)
        self.avg_entry_price = round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0
        # Scaled net target covering commissions and generating real cash
        self.target_net_profit_usd = max(0.50, round(0.35 * self.total_stake_usd, 2))


@dataclass
class GridSignal:
    action: str  # "OPEN_LAYER_1" | "ACCUMULATE_LAYER" | "EXIT_RESPIRO" | "STOP_BASKET" | "WAIT"
    direction: str
    layer_index: int
    stake_usd: float
    price: float
    reason: str
    net_pnl_usd: float = 0.0
    target_net_profit_usd: float = 0.50


class SwingGridRespirosEngine:
    def __init__(
        self,
        base_stake_usd: float = 1.0,
        max_layers: int = 3,
        layer_multipliers: Optional[Sequence[float]] = None,
        sigma_step_mult: float = 1.3,
        min_step_pct: float = 0.0012,
        basket_stop_loss_usd: float = 8.0,
    ) -> None:
        self.base_stake_usd = float(base_stake_usd)
        self.max_layers = min(4, max(1, int(max_layers)))
        self.layer_multipliers = list(layer_multipliers or [1.0, 1.5, 2.0, 2.5])[: self.max_layers]
        self.sigma_step_mult = float(sigma_step_mult)
        self.min_step_pct = float(min_step_pct)
        self.basket_stop_loss_usd = float(basket_stop_loss_usd)

        # Microstructure ring buffer
        self._ticks: deque[TickItem] = deque(maxlen=120)
        self._ema_20: Optional[float] = None
        self._ema_alpha: float = 2.0 / (20.0 + 1.0)
        self._prev_price: Optional[float] = None

    def update_tick(self, price: float, epoch: int) -> None:
        if price <= 0.0:
            return
        if self._ticks:
            self._prev_price = self._ticks[-1].price
        self._ticks.append(TickItem(price=price, epoch=epoch))

        # Update EMA-20
        if self._ema_20 is None:
            self._ema_20 = price
        else:
            self._ema_20 = (self._ema_alpha * price) + ((1.0 - self._ema_alpha) * self._ema_20)

    def compute_stats(self) -> tuple[float, float, float]:
        """Compute EMA-20, sigma_60, and Z-Score."""
        n = len(self._ticks)
        if n < 1 or self._ema_20 is None:
            return 0.0, 0.0, 0.0

        current_price = self._ticks[-1].price
        if n < 2:
            return self._ema_20, 0.0, 0.0

        sample = [t.price for t in list(self._ticks)[-min(60, n) :]]
        k = len(sample)
        mean_px = sum(sample) / k
        variance = sum((p - mean_px) ** 2 for p in sample) / max(1, k - 1)
        sigma = math.sqrt(max(1e-8, variance))
        z = (current_price - self._ema_20) / sigma if sigma > 1e-6 else 0.0
        return self._ema_20, sigma, z

    def initialize_cycle(
        self,
        cycle_id: int,
        symbol: str,
        spot: float,
        direction: str = "MULTUP",
    ) -> GridCycleState:
        stake_1 = round(self.base_stake_usd * self.layer_multipliers[0], 2)
        cycle = GridCycleState(
            cycle_id=cycle_id,
            direction=direction,
            symbol=symbol,
            layers=[
                GridLayer(
                    layer_index=1,
                    stake_usd=stake_1,
                    entry_spot=spot,
                )
            ],
        )
        cycle.recalculate()
        return cycle

    def register_layer(
        self,
        cycle: GridCycleState,
        layer_index: int,
        spot: float,
        contract_id: Optional[str] = None,
    ) -> None:
        mult_idx = min(len(self.layer_multipliers) - 1, layer_index - 1)
        stake = round(self.base_stake_usd * self.layer_multipliers[mult_idx], 2)
        # Check if layer already registered
        for l in cycle.layers:
            if l.layer_index == layer_index:
                l.entry_spot = spot
                if contract_id:
                    l.contract_id = contract_id
                cycle.recalculate()
                return

        cycle.layers.append(
            GridLayer(
                layer_index=layer_index,
                stake_usd=stake,
                entry_spot=spot,
                contract_id=contract_id,
            )
        )
        cycle.recalculate()

    def evaluate_cycle(
        self,
        cycle: Optional[GridCycleState],
        current_spot: float,
        current_net_pnl_usd: float,
        direction_mode: str = "BOTH",
    ) -> GridSignal:
        """Evaluate active cycle or generate new entry signal."""
        # 1. FLAT -> Generate Layer #1 Entry
        if cycle is None or not cycle.layers:
            direction = "MULTUP"
            if direction_mode == "SHORT_ONLY":
                direction = "MULTDOWN"
            elif direction_mode == "BOTH":
                # Prefer MULTUP unless severe overbought Z > 1.8
                _, _, z = self.compute_stats()
                if z >= 1.8:
                    direction = "MULTDOWN"
                else:
                    direction = "MULTUP"

            stake_1 = round(self.base_stake_usd * self.layer_multipliers[0], 2)
            return GridSignal(
                action="OPEN_LAYER_1",
                direction=direction,
                layer_index=1,
                stake_usd=stake_1,
                price=current_spot,
                reason=f"Início de ciclo Swing Grid ({direction}) @ {current_spot:.4f}",
            )

        # 2. IN POSITION -> Check Breather Take Profit ("Lucro no Respiro")
        target_pnl = cycle.target_net_profit_usd
        if current_net_pnl_usd >= target_pnl:
            return GridSignal(
                action="EXIT_RESPIRO",
                direction=cycle.direction,
                layer_index=len(cycle.layers),
                stake_usd=cycle.total_stake_usd,
                price=current_spot,
                net_pnl_usd=current_net_pnl_usd,
                target_net_profit_usd=target_pnl,
                reason=(
                    f"🎯 RESPIRO ATINGIDO! Lucro Líquido: +${current_net_pnl_usd:.2f} "
                    f"(Alvo: +${target_pnl:.2f}) com {len(cycle.layers)} camada(s)"
                ),
            )

        # 3. Check Basket Stop Loss (Proteção de Capital)
        if current_net_pnl_usd <= -self.basket_stop_loss_usd:
            return GridSignal(
                action="STOP_BASKET",
                direction=cycle.direction,
                layer_index=len(cycle.layers),
                stake_usd=cycle.total_stake_usd,
                price=current_spot,
                net_pnl_usd=current_net_pnl_usd,
                reason=f"🛑 Stop Loss do cesto atingido (-${abs(current_net_pnl_usd):.2f})",
            )

        # 4. Check Accumulation Layer Trigger (Acumulação no Recuo)
        current_layer_count = len(cycle.layers)
        if current_layer_count < self.max_layers:
            last_entry = cycle.layers[-1].entry_spot
            _, sigma, _ = self.compute_stats()
            min_delta = max(self.sigma_step_mult * sigma, last_entry * self.min_step_pct)

            next_layer_idx = current_layer_count + 1
            mult_idx = min(len(self.layer_multipliers) - 1, next_layer_idx - 1)
            next_stake = round(self.base_stake_usd * self.layer_multipliers[mult_idx], 2)

            # MULTUP: Price dropped by min_delta AND tick shows inflection up (P_t > P_{t-1})
            if cycle.direction == "MULTUP":
                adverse_move = last_entry - current_spot
                is_inflection = self._prev_price is not None and current_spot > self._prev_price
                if adverse_move >= min_delta and is_inflection:
                    return GridSignal(
                        action="ACCUMULATE_LAYER",
                        direction=cycle.direction,
                        layer_index=next_layer_idx,
                        stake_usd=next_stake,
                        price=current_spot,
                        reason=(
                            f"📐 Acumulando Camada #{next_layer_idx} no recuo "
                            f"(Queda: {adverse_move:.2f} >= {min_delta:.2f}) @ {current_spot:.4f}"
                        ),
                    )

            # MULTDOWN: Price rose by min_delta AND tick shows inflection down (P_t < P_{t-1})
            elif cycle.direction == "MULTDOWN":
                adverse_move = current_spot - last_entry
                is_inflection = self._prev_price is not None and current_spot < self._prev_price
                if adverse_move >= min_delta and is_inflection:
                    return GridSignal(
                        action="ACCUMULATE_LAYER",
                        direction=cycle.direction,
                        layer_index=next_layer_idx,
                        stake_usd=next_stake,
                        price=current_spot,
                        reason=(
                            f"📐 Acumulando Camada #{next_layer_idx} na alta "
                            f"(Subida: {adverse_move:.2f} >= {min_delta:.2f}) @ {current_spot:.4f}"
                        ),
                    )

        return GridSignal(
            action="WAIT",
            direction=cycle.direction,
            layer_index=current_layer_count,
            stake_usd=cycle.total_stake_usd,
            price=current_spot,
            net_pnl_usd=current_net_pnl_usd,
            target_net_profit_usd=target_pnl,
            reason="Aguardando respiro ou próximo nível de acumulação",
        )
