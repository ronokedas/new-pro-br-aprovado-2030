"""Pure, Unit-Testable Smart DCA & Basket Exit Manager.

Responsibilities:
- Dynamic layer spacing bounded strictly inside real contract `stop_out_pct`
  (`step_total <= 0.75 * abs(stop_out_pct)`).
- Strict conservative sizing validation (rejects exponential Martingale >= 2.0x per step).
- Real net basket PnL aggregation (`sum(c.current_profit_usd)`) and stake-weighted
  average entry price.
- Exit evaluation: STOP_LOSS, CHANNEL_BREAK, TP (direct target), TRAILING (profit floor
  with 30% pullback from peak), and TIMEOUT (`max_hold_minutes`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from engine.channel_detector import ChannelSnapshot


MAX_SINGLE_STEP_GROWTH = 2.0  # Strictly forbidden: ratio >= 2.0x per step (Martingale)
MAX_ALLOWED_LAYERS = 5


@dataclass
class ContractSnapshot:
    layer_index: int
    contract_type: str  # "MULTUP" | "MULTDOWN"
    stake_usd: float
    multiplier: int
    entry_spot: float
    current_profit_usd: float = 0.0
    commission_usd: float = 0.0
    stop_out_spot: Optional[float] = None
    deriv_contract_id: Optional[str] = None
    status: str = "OPEN"  # "OPEN" | "SOLD" | "STOPPED_OUT"


@dataclass
class BasketState:
    symbol: str
    direction: str  # "MULTUP" | "MULTDOWN"
    opened_at: datetime
    contracts: list[ContractSnapshot] = field(default_factory=list)
    peak_pnl_usd: float = 0.0
    trailing_armed: bool = False
    trailing_floor_usd: Optional[float] = None


@dataclass
class BasketExitDecision:
    should_close: bool
    reason: Optional[str] = None  # TP | TRAILING | STOP_LOSS | CHANNEL_BREAK | TIMEOUT
    net_pnl_usd: float = 0.0
    peak_pnl_usd: float = 0.0
    trailing_armed: bool = False
    trailing_floor_usd: Optional[float] = None
    details: str = ""


class SmartDCABasketManager:
    """Stateless/pure calculator for Smart DCA sizing, spacing, metrics, and basket exits."""

    def __init__(
        self,
        max_layers: int = 3,
        layer_multipliers: Optional[Sequence[float]] = None,
        basket_tp_usd: float = 0.15,
        trailing_enabled: bool = True,
        trailing_pullback_pct: float = 0.30,
        max_basket_loss_usd: float = 2.50,
        max_hold_minutes: int = 180,
        channel_break_atr_mult: float = 1.2,
        stop_out_safety_ratio: float = 0.75,
    ) -> None:
        self.max_layers = max(1, min(int(max_layers), MAX_ALLOWED_LAYERS))
        mults = list(layer_multipliers) if layer_multipliers is not None else [1.0, 1.0, 1.5]
        self.layer_multipliers = self.validate_layer_multipliers(mults, self.max_layers)
        self.basket_tp_usd = float(basket_tp_usd)
        self.trailing_enabled = bool(trailing_enabled)
        self.trailing_pullback_pct = float(trailing_pullback_pct)
        self.max_basket_loss_usd = abs(float(max_basket_loss_usd))
        self.max_hold_minutes = max(1, int(max_hold_minutes))
        self.channel_break_atr_mult = float(channel_break_atr_mult)
        self.stop_out_safety_ratio = float(stop_out_safety_ratio)

    @staticmethod
    def parse_multipliers_json(raw_json: str, max_layers: int = 3) -> list[float]:
        """Parse and validate layer multipliers JSON from BotConfig."""
        try:
            parsed = json.loads(raw_json)
            if not isinstance(parsed, list):
                raise ValueError("layer_multipliers_json deve ser uma lista JSON")
            mults = [float(x) for x in parsed]
        except Exception as exc:
            raise ValueError(f"Multiplicadores de camadas inválidos: {exc}") from exc
        return SmartDCABasketManager.validate_layer_multipliers(mults, max_layers)

    @staticmethod
    def validate_layer_multipliers(multipliers: Sequence[float], max_layers: int = 3) -> list[float]:
        """Validate conservative DCA sizing and strictly reject exponential Martingale (>= 2.0x)."""
        if not multipliers:
            raise ValueError("Lista de multiplicadores de camada não pode estar vazia")

        cleaned = [float(m) for m in multipliers[: max(1, int(max_layers))]]
        while len(cleaned) < max_layers:
            cleaned.append(cleaned[-1])

        if cleaned[0] <= 0.0:
            raise ValueError("Multiplicador da Camada 1 deve ser positivo")

        for idx, m in enumerate(cleaned):
            if m <= 0.0:
                raise ValueError(f"Multiplicador inválido na camada {idx + 1}: {m}")
            if m >= 3.0:
                raise ValueError(
                    f"Progressão agressiva proibida na camada {idx + 1} ({m}x >= 3.0x)"
                )
            if idx > 0:
                step_ratio = m / cleaned[idx - 1]
                if step_ratio >= MAX_SINGLE_STEP_GROWTH:
                    raise ValueError(
                        f"Martingale proibido: salto da camada {idx} para {idx + 1} "
                        f"é {step_ratio:.2f}x (>= 2.0x)"
                    )

        # Also check cumulative progression from layer 1 to prevent disguised Martingale
        if len(cleaned) >= 3 and cleaned[-1] >= cleaned[0] * 2.5:
            raise ValueError(
                f"Progressão excessiva: última camada ({cleaned[-1]}x) >= 2.5x da primeira"
            )

        return cleaned

    def compute_dynamic_step_pct(
        self,
        stop_out_pct: float,
        atr: Optional[float] = None,
        current_price: Optional[float] = None,
        max_layers: Optional[int] = None,
    ) -> float:
        """Compute inter-layer step fraction so total DCA span fits inside `0.75 * abs(stop_out_pct)`.

        Example:
          stop_out_pct = -0.0093 (cryBTCUSD 100x), max_layers = 3 (2 steps):
            max_span = 0.75 * 0.0093 = 0.006975 (0.6975%)
            max_step = 0.006975 / 2 = 0.0034875 (0.34875%)
            desired step ~ 0.0025 (0.25%), total span = 0.50% <= 0.6975%.
        """
        layers = int(max_layers) if max_layers is not None else self.max_layers
        if layers <= 1:
            return 0.0

        so_dist = abs(float(stop_out_pct))
        if so_dist <= 1e-6:
            # Conservative fallback if broker stop-out metadata is missing
            so_dist = 0.0093

        gaps = layers - 1
        max_total_span = self.stop_out_safety_ratio * so_dist
        max_safe_step = max_total_span / gaps

        if atr is not None and current_price is not None and current_price > 0.0 and atr > 0.0:
            atr_step = 0.60 * (float(atr) / float(current_price))
            desired = atr_step
        else:
            # Default ~55% of stop_out split across gaps (gives ~0.00255 for 0.93% & 3 layers)
            desired = min(0.0025, max_safe_step) if so_dist <= 0.015 else (0.55 * so_dist / gaps)

        min_floor = min(0.0010, max_safe_step)
        step = max(min_floor, min(desired, max_safe_step))
        return round(step, 6)

    def compute_layer_stake(
        self,
        base_stake_usd: float,
        layer_index: int,
        max_stake_usd: Optional[float] = None,
    ) -> float:
        """Return USD stake for 1-based `layer_index` respecting user's cap."""
        idx = max(0, min(int(layer_index) - 1, len(self.layer_multipliers) - 1))
        stake = round(float(base_stake_usd) * self.layer_multipliers[idx], 2)
        if max_stake_usd is not None and max_stake_usd > 0.0:
            stake = min(stake, round(float(max_stake_usd), 2))
        return max(0.01, stake)

    @staticmethod
    def compute_net_pnl(contracts: Sequence[ContractSnapshot]) -> float:
        """Compute exact real net basket PnL across all layers in the basket."""
        return round(sum(float(c.current_profit_usd) for c in contracts), 6)

    @staticmethod
    def compute_avg_entry_price(contracts: Sequence[ContractSnapshot]) -> float:
        """Compute stake-weighted average entry spot across open contracts (or all if none open)."""
        open_contracts = [c for c in contracts if c.status == "OPEN" and c.entry_spot > 0.0]
        target = open_contracts if open_contracts else [c for c in contracts if c.entry_spot > 0.0]
        if not target:
            return 0.0
        total_weight = sum(float(c.stake_usd) for c in target)
        if total_weight <= 1e-12:
            return float(target[-1].entry_spot)
        weighted_sum = sum(float(c.stake_usd) * float(c.entry_spot) for c in target)
        return round(weighted_sum / total_weight, 6)

    @staticmethod
    def compute_total_stake(contracts: Sequence[ContractSnapshot]) -> float:
        return round(sum(float(c.stake_usd) for c in contracts), 2)

    @staticmethod
    def compute_total_commission(contracts: Sequence[ContractSnapshot]) -> float:
        return round(sum(float(c.commission_usd) for c in contracts), 4)

    @staticmethod
    def estimate_price_levels(
        contracts: Sequence[ContractSnapshot],
        direction: str,
        current_spot: float,
        target_tp_usd: float,
    ) -> tuple[Optional[float], Optional[float]]:
        """Estimate live-calibrated (breakeven_price, target_tp_price) for chart overlays."""
        open_c = [c for c in contracts if c.status == "OPEN" and c.entry_spot > 0.0]
        if not open_c or current_spot <= 0.0:
            return None, None

        net_pnl = SmartDCABasketManager.compute_net_pnl(contracts)
        sensitivity = sum(
            (float(c.stake_usd) * float(c.multiplier)) / float(c.entry_spot) for c in open_c
        )
        if sensitivity <= 1e-12:
            return None, None

        sign = 1.0 if direction.upper() == "MULTUP" else -1.0
        breakeven_price = current_spot + sign * ((0.0 - net_pnl) / sensitivity)
        target_price = current_spot + sign * ((float(target_tp_usd) - net_pnl) / sensitivity)
        return round(breakeven_price, 6), round(target_price, 6)

    def evaluate_exit(
        self,
        basket: BasketState,
        channel: Optional[ChannelSnapshot] = None,
        latest_closed_close: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> BasketExitDecision:
        """Evaluate all basket exit conditions in strict risk-first priority.

        Updates `basket.peak_pnl_usd`, `basket.trailing_armed`, and `basket.trailing_floor_usd`
        in-place so caller can persist updated trailing state.
        """
        open_contracts = [c for c in basket.contracts if c.status == "OPEN"]
        net_pnl = self.compute_net_pnl(basket.contracts)

        if not open_contracts:
            return BasketExitDecision(
                should_close=False,
                net_pnl_usd=net_pnl,
                peak_pnl_usd=basket.peak_pnl_usd,
                trailing_armed=basket.trailing_armed,
                trailing_floor_usd=basket.trailing_floor_usd,
                details="Nenhum contrato aberto no cesto",
            )

        if net_pnl > basket.peak_pnl_usd:
            basket.peak_pnl_usd = net_pnl

        # 1. Global Basket Stop Loss (hard USD floor)
        if net_pnl <= -self.max_basket_loss_usd:
            return BasketExitDecision(
                should_close=True,
                reason="STOP_LOSS",
                net_pnl_usd=net_pnl,
                peak_pnl_usd=basket.peak_pnl_usd,
                trailing_armed=basket.trailing_armed,
                trailing_floor_usd=basket.trailing_floor_usd,
                details=f"Stop global do cesto atingido (${net_pnl:.2f} <= -${self.max_basket_loss_usd:.2f})",
            )

        # 2. Structural Channel Break Stop Loss (closed candle beyond band +/- 1.2*ATR)
        if channel is not None and latest_closed_close is not None and channel.atr > 0.0:
            dir_norm = basket.direction.upper()
            if dir_norm == "MULTUP":
                break_level = channel.lower - self.channel_break_atr_mult * channel.atr
                if latest_closed_close < break_level:
                    return BasketExitDecision(
                        should_close=True,
                        reason="CHANNEL_BREAK",
                        net_pnl_usd=net_pnl,
                        peak_pnl_usd=basket.peak_pnl_usd,
                        trailing_armed=basket.trailing_armed,
                        trailing_floor_usd=basket.trailing_floor_usd,
                        details=(
                            f"Rompimento estrutural do canal de alta "
                            f"(close={latest_closed_close:.4f} < {break_level:.4f})"
                        ),
                    )
            else:
                break_level = channel.upper + self.channel_break_atr_mult * channel.atr
                if latest_closed_close > break_level:
                    return BasketExitDecision(
                        should_close=True,
                        reason="CHANNEL_BREAK",
                        net_pnl_usd=net_pnl,
                        peak_pnl_usd=basket.peak_pnl_usd,
                        trailing_armed=basket.trailing_armed,
                        trailing_floor_usd=basket.trailing_floor_usd,
                        details=(
                            f"Rompimento estrutural do canal de baixa "
                            f"(close={latest_closed_close:.4f} > {break_level:.4f})"
                        ),
                    )

        # 3. Collective Take Profit / Trailing Profit
        if self.trailing_enabled:
            if basket.peak_pnl_usd >= self.basket_tp_usd:
                basket.trailing_armed = True
                floor = round(basket.peak_pnl_usd * (1.0 - self.trailing_pullback_pct), 6)
                if basket.trailing_floor_usd is None or floor > basket.trailing_floor_usd:
                    basket.trailing_floor_usd = floor
                if net_pnl <= basket.trailing_floor_usd:
                    return BasketExitDecision(
                        should_close=True,
                        reason="TRAILING",
                        net_pnl_usd=net_pnl,
                        peak_pnl_usd=basket.peak_pnl_usd,
                        trailing_armed=True,
                        trailing_floor_usd=basket.trailing_floor_usd,
                        details=(
                            f"Trailing Profit acionado: PnL=${net_pnl:.2f} recuou para piso "
                            f"${basket.trailing_floor_usd:.2f} (pico=${basket.peak_pnl_usd:.2f})"
                        ),
                    )
        else:
            if net_pnl >= self.basket_tp_usd:
                return BasketExitDecision(
                    should_close=True,
                    reason="TP",
                    net_pnl_usd=net_pnl,
                    peak_pnl_usd=basket.peak_pnl_usd,
                    trailing_armed=False,
                    trailing_floor_usd=None,
                    details=f"Meta de lucro do cesto atingida (${net_pnl:.2f} >= ${self.basket_tp_usd:.2f})",
                )

        # 4. Maximum Hold Timeout
        ts_now = now if now is not None else datetime.now(timezone.utc)
        opened_at = basket.opened_at
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        if ts_now.tzinfo is None:
            ts_now = ts_now.replace(tzinfo=timezone.utc)
        elapsed_min = (ts_now - opened_at).total_seconds() / 60.0
        if elapsed_min >= self.max_hold_minutes:
            return BasketExitDecision(
                should_close=True,
                reason="TIMEOUT",
                net_pnl_usd=net_pnl,
                peak_pnl_usd=basket.peak_pnl_usd,
                trailing_armed=basket.trailing_armed,
                trailing_floor_usd=basket.trailing_floor_usd,
                details=f"Tempo máximo do cesto excedido ({elapsed_min:.1f} min >= {self.max_hold_minutes} min)",
            )

        return BasketExitDecision(
            should_close=False,
            reason=None,
            net_pnl_usd=net_pnl,
            peak_pnl_usd=basket.peak_pnl_usd,
            trailing_armed=basket.trailing_armed,
            trailing_floor_usd=basket.trailing_floor_usd,
            details="Cesto dentro dos parâmetros operacionais",
        )
