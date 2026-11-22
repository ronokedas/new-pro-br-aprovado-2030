"""V100 Multi-Profile Strategy Engine for Deriv (Volatility 100 Index: R_100 / 1HZ100V).

Implements 3 isolated risk profiles:
1. AGGRESSIVE (Dígitos Over 0 / Under 9 - 1 Tick, ~90% winrate, Split Martingale)
2. MODERATE (Dígitos Over 2 / Under 7 - 1 Tick, ~70% winrate, Recovery 2.7x)
3. CONSERVATIVE (Multipliers M1/M5 - EMA 9/21 + RSI 14, Payoff 1:2, Mão Fixa 1%)

Strictly anti-repaint, mathematically disciplined, and isolated.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("worker.v100_engine")


def extract_last_digit(price: float) -> int:
    """Extract final decimal digit from 2-decimal synthetic index price."""
    price_str = f"{price:.2f}"
    return int(price_str[-1])


def compute_ema(values: List[float], period: int) -> Optional[float]:
    """Compute Exponential Moving Average (causal, anti-repaint)."""
    if not values or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / float(period)
    for v in values[period:]:
        ema = (v * k) + (ema * (1.0 - k))
    return ema


def compute_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Compute Relative Strength Index over closed bars."""
    if not closes or len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(-period, 0)]
    gains = [d for d in diffs if d > 0]
    losses = [abs(d) for d in diffs if d < 0]
    avg_gain = sum(gains) / float(period)
    avg_loss = sum(losses) / float(period)
    if avg_loss <= 1e-8:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass
class V100Signal:
    symbol: str
    mode: str  # "AGGRESSIVE" | "MODERATE" | "CONSERVATIVE"
    contract_type: str  # "DIGITOVER" | "DIGITUNDER" | "MULTUP" | "MULTDOWN"
    stake_usd: float
    duration: int = 1
    duration_unit: str = "t"  # "t" (tick) or "m" (minutes)
    barrier: Optional[str] = None  # "0", "9", "2", "7"
    multiplier: Optional[int] = 100
    stop_loss_usd: Optional[float] = None
    take_profit_usd: Optional[float] = None
    reason: str = ""
    current_spot: float = 0.0
    last_digit: Optional[int] = None
    level_index: int = 0


@dataclass
class V100MarketState:
    symbol: str = "1HZ100V"
    # Digits stream: ring buffer of (digit, price, epoch)
    digits: Deque[Tuple[int, float, float]] = field(default_factory=lambda: deque(maxlen=40))
    # Closed M1 Candles for conservative mode
    candles_m1: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=300))
    current_forming_m1: Optional[Dict[str, Any]] = None
    
    # Conservative Indicators
    ema9: Optional[float] = None
    ema21: Optional[float] = None
    prev_ema9: Optional[float] = None
    prev_ema21: Optional[float] = None
    rsi14: Optional[float] = None
    
    # Execution Tracking
    in_flight_contract_id: Optional[str] = None
    last_traded_epoch: float = 0.0
    consecutive_losses: int = 0
    recovery_level: int = 0
    
    # Active mode
    mode: str = "AGGRESSIVE"  # "AGGRESSIVE" | "MODERATE" | "CONSERVATIVE"


class V100StrategyEngine:
    """Quantitative Multi-Profile Engine for Volatility 100 Index."""

    def __init__(
        self,
        symbol: str = "1HZ100V",
        mode: str = "AGGRESSIVE",
        base_stake_usd: float = 1.00,
        stop_win_usd: float = 15.0,
        stop_loss_usd: float = 10.0,
        max_consecutive_losses: int = 3,
        account_balance: float = 100.0,
    ) -> None:
        self.symbol = symbol if symbol in ("1HZ100V", "R_100") else "1HZ100V"
        self.mode = self._normalize_mode(mode)
        self.base_stake_usd = max(0.50, float(base_stake_usd))
        self.stop_win_usd = abs(float(stop_win_usd))
        self.stop_loss_usd = abs(float(stop_loss_usd))
        self.max_consecutive_losses = max(1, min(10, int(max_consecutive_losses)))
        self.account_balance = max(1.0, float(account_balance))
        
        self.state = V100MarketState(symbol=self.symbol, mode=self.mode)
        
        # Session metrics
        self.total_wins: int = 0
        self.total_losses: int = 0
        self.session_profit_usd: float = 0.0
        self.recent_trades: Deque[Dict[str, Any]] = deque(maxlen=60)
        self.is_locked: bool = False
        self.lock_reason: str = ""

    def _normalize_mode(self, mode_str: str) -> str:
        m = str(mode_str or "").upper().strip()
        if "CONSERV" in m:
            return "CONSERVATIVE"
        if "MODER" in m:
            return "MODERATE"
        return "AGGRESSIVE"

    def update_config(
        self,
        symbol: Optional[str] = None,
        mode: Optional[str] = None,
        base_stake_usd: Optional[float] = None,
        stop_win_usd: Optional[float] = None,
        stop_loss_usd: Optional[float] = None,
        max_consecutive_losses: Optional[int] = None,
        account_balance: Optional[float] = None,
    ) -> None:
        """Update runtime strategy settings."""
        if symbol is not None and symbol.strip():
            self.symbol = symbol.strip()
            self.state.symbol = self.symbol
        if mode is not None:
            self.mode = self._normalize_mode(mode)
            self.state.mode = self.mode
        if base_stake_usd is not None:
            self.base_stake_usd = max(0.50, float(base_stake_usd))
        if stop_win_usd is not None:
            self.stop_win_usd = abs(float(stop_win_usd))
        if stop_loss_usd is not None:
            self.stop_loss_usd = abs(float(stop_loss_usd))
        if max_consecutive_losses is not None:
            self.max_consecutive_losses = max(1, min(10, int(max_consecutive_losses)))
        if account_balance is not None and account_balance > 0:
            self.account_balance = float(account_balance)

    def load_historical_candles(self, candles: list[dict[str, Any]]) -> None:
        """Hydrate historical M1 candles for Conservative Mode indicators."""
        self.state.candles_m1.clear()
        for c in candles:
            t = int(c.get("epoch") or c.get("time") or 0)
            o = float(c.get("open") or 0.0)
            h = float(c.get("high") or 0.0)
            l = float(c.get("low") or 0.0)
            cl = float(c.get("close") or 0.0)
            if cl > 0:
                self.state.candles_m1.append({"time": t, "open": o, "high": h, "low": l, "close": cl})
        self._recalculate_indicators()

    def _recalculate_indicators(self) -> None:
        """Calculate EMA 9, EMA 21, and RSI 14 over closed M1 candles."""
        closes = [c["close"] for c in self.state.candles_m1 if c.get("close", 0) > 0]
        if not closes or len(closes) < 22:
            return

        # Store previous values to detect clean crossover
        self.state.prev_ema9 = self.state.ema9
        self.state.prev_ema21 = self.state.ema21

        self.state.ema9 = compute_ema(closes, 9)
        self.state.ema21 = compute_ema(closes, 21)
        self.state.rsi14 = compute_rsi(closes, 14)

    def calculate_stake(self) -> float:
        """Calculate stake dynamically based on profile recovery math."""
        if self.mode == "AGGRESSIVE":
            # Split Martingale suave em no máximo 3 níveis (1.5x -> 2.0x -> 2.5x)
            lvl = self.state.recovery_level
            if lvl == 0:
                return round(self.base_stake_usd, 2)
            elif lvl == 1:
                return round(self.base_stake_usd * 1.5, 2)
            elif lvl == 2:
                return round(self.base_stake_usd * 2.0, 2)
            elif lvl == 3:
                return round(self.base_stake_usd * 2.5, 2)
            else:
                return round(self.base_stake_usd, 2)

        elif self.mode == "MODERATE":
            # Recuperação controlada de ~2.7x sobre a perda, teto de 3 níveis
            lvl = self.state.recovery_level
            if lvl == 0:
                return round(self.base_stake_usd, 2)
            elif lvl == 1:
                return round(self.base_stake_usd * 2.7, 2)
            elif lvl == 2:
                return round(self.base_stake_usd * 7.29, 2)  # 2.7^2
            else:
                return round(self.base_stake_usd, 2)

        elif self.mode == "CONSERVATIVE":
            # Mão Fixa: 1% da banca. ZERO Martingale.
            risk_1pct = max(0.50, self.account_balance * 0.01)
            # Use base_stake_usd if explicitly higher than 1%
            return round(max(self.base_stake_usd, risk_1pct), 2)

        return round(self.base_stake_usd, 2)

    def on_tick(self, symbol: str, price: float, epoch: float) -> Optional[V100Signal]:
        """Ingest real-time tick, update digit ring buffer and M1 candle, evaluate active profile."""
        if price <= 0:
            return None
        if symbol not in ("1HZ100V", "R_100"):
            return None

        # 1. Digest Digit
        digit = extract_last_digit(price)
        self.state.digits.append((digit, price, epoch))

        # 2. Digest M1 Candle boundary
        candle_epoch = int(epoch // 60) * 60
        just_closed_candle = None
        if self.state.current_forming_m1 is None or self.state.current_forming_m1["time"] != candle_epoch:
            if self.state.current_forming_m1 is not None:
                just_closed_candle = dict(self.state.current_forming_m1)
                self.state.candles_m1.append(just_closed_candle)
                self._recalculate_indicators()
            self.state.current_forming_m1 = {
                "time": candle_epoch,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        else:
            fc = self.state.current_forming_m1
            fc["high"] = max(fc["high"], price)
            fc["low"] = min(fc["low"], price)
            fc["close"] = price

        # 3. Global Risk Circuit Breakers
        if self.session_profit_usd >= self.stop_win_usd:
            self.is_locked = True
            self.lock_reason = f"META DIÁRIA ATINGIDA 🎯 (+${self.session_profit_usd:.2f} USD)"
            return None

        if self.session_profit_usd <= -self.stop_loss_usd:
            self.is_locked = True
            self.lock_reason = f"STOP LOSS DIÁRIO ATINGIDO 🛑 (-${abs(self.session_profit_usd):.2f} USD)"
            return None

        if self.state.consecutive_losses >= self.max_consecutive_losses:
            self.is_locked = True
            self.lock_reason = f"LIMITE DE {self.max_consecutive_losses} PERDAS CONSECUTIVAS ATINGIDO"
            return None

        # Atomic Latency Lock: Never fire while a contract is in flight
        if self.state.in_flight_contract_id:
            return None

        # Anti-flood cooldown: minimum 1.5s between consecutive signals
        if (epoch - self.state.last_traded_epoch) < 1.5:
            return None

        # 4. Route evaluation to active risk profile
        if self.mode == "AGGRESSIVE":
            return self._evaluate_aggressive(price, epoch, digit)
        elif self.mode == "MODERATE":
            return self._evaluate_moderate(price, epoch, digit)
        elif self.mode == "CONSERVATIVE":
            return self._evaluate_conservative(price, epoch, just_closed_candle)

        return None

    def _evaluate_aggressive(self, price: float, epoch: float, last_digit: int) -> Optional[V100Signal]:
        """Modo Agressivo: Dígitos Over 0 / Under 9 em 1 Tick (~90% Winrate).
        
        Gatilho Over 0: Disparar após 1 ou 2 ocorrências consecutivas do dígito 0.
        Gatilho Under 9: Disparar após 1 ou 2 ocorrências consecutivas do dígito 9.
        """
        recent_digits = [d for d, _, _ in list(self.state.digits)[-20:]]
        if len(recent_digits) < 5:
            return None

        stake = self.calculate_stake()

        # Gatilho OVER 0: Último dígito foi 0 (ou últimos 2 foram 0)
        if recent_digits[-1] == 0:
            self.state.last_traded_epoch = epoch
            return V100Signal(
                symbol=self.symbol,
                mode="AGGRESSIVE",
                contract_type="DIGITOVER",
                stake_usd=stake,
                duration=1,
                duration_unit="t",
                barrier="0",
                reason=f"Modo Agressivo OVER 0: Dígito 0 detectado (Win em 1-9). Recuperação Nível #{self.state.recovery_level} (Stake ${stake:.2f})",
                current_spot=price,
                last_digit=last_digit,
                level_index=self.state.recovery_level,
            )

        # Gatilho UNDER 9: Último dígito foi 9 (ou últimos 2 foram 9)
        if recent_digits[-1] == 9:
            self.state.last_traded_epoch = epoch
            return V100Signal(
                symbol=self.symbol,
                mode="AGGRESSIVE",
                contract_type="DIGITUNDER",
                stake_usd=stake,
                duration=1,
                duration_unit="t",
                barrier="9",
                reason=f"Modo Agressivo UNDER 9: Dígito 9 detectado (Win em 0-8). Recuperação Nível #{self.state.recovery_level} (Stake ${stake:.2f})",
                current_spot=price,
                last_digit=last_digit,
                level_index=self.state.recovery_level,
            )

        return None

    def _evaluate_moderate(self, price: float, epoch: float, last_digit: int) -> Optional[V100Signal]:
        """Modo Moderado: Dígitos Over 2 / Under 7 em 1 Tick (~70% Winrate).
        
        Gatilho Over 2: Disparar após sequência de 2 ou 3 dígitos <= 2 consecutivos.
        Gatilho Under 7: Disparar após sequência de 2 ou 3 dígitos >= 7 consecutivos.
        """
        recent_digits = [d for d, _, _ in list(self.state.digits)[-25:]]
        if len(recent_digits) < 6:
            return None

        stake = self.calculate_stake()

        # Checar sequência de 2 dígitos <= 2 (ex: [..., d[-2] <= 2, d[-1] <= 2])
        if recent_digits[-1] <= 2 and recent_digits[-2] <= 2:
            self.state.last_traded_epoch = epoch
            return V100Signal(
                symbol=self.symbol,
                mode="MODERATE",
                contract_type="DIGITOVER",
                stake_usd=stake,
                duration=1,
                duration_unit="t",
                barrier="2",
                reason=f"Modo Moderado OVER 2: Sequência de dígitos baixos [{recent_digits[-2]}, {recent_digits[-1]}] <= 2. Recuperação Nível #{self.state.recovery_level} (Stake ${stake:.2f})",
                current_spot=price,
                last_digit=last_digit,
                level_index=self.state.recovery_level,
            )

        # Checar sequência de 2 dígitos >= 7 (ex: [..., d[-2] >= 7, d[-1] >= 7])
        if recent_digits[-1] >= 7 and recent_digits[-2] >= 7:
            self.state.last_traded_epoch = epoch
            return V100Signal(
                symbol=self.symbol,
                mode="MODERATE",
                contract_type="DIGITUNDER",
                stake_usd=stake,
                duration=1,
                duration_unit="t",
                barrier="7",
                reason=f"Modo Moderado UNDER 7: Sequência de dígitos altos [{recent_digits[-2]}, {recent_digits[-1]}] >= 7. Recuperação Nível #{self.state.recovery_level} (Stake ${stake:.2f})",
                current_spot=price,
                last_digit=last_digit,
                level_index=self.state.recovery_level,
            )

        return None

    def _evaluate_conservative(
        self, price: float, epoch: float, just_closed_candle: Optional[Dict[str, Any]]
    ) -> Optional[V100Signal]:
        """Modo Conservador: Multipliers M1 (Payoff 1:2, Stop 1%, TP 2%, Zero Martingale).
        
        Compra (MULTUP): EMA 9 cruza acima da EMA 21 E RSI(14) > 50 e < 70.
        Venda (MULTDOWN): EMA 9 cruza abaixo da EMA 21 E RSI(14) < 50 e > 30.
        """
        # Multipliers operam estritamente no fechamento de vela M1 para evitar ruído de tick
        if just_closed_candle is None:
            return None

        if (
            self.state.ema9 is None
            or self.state.ema21 is None
            or self.state.rsi14 is None
        ):
            return None

        prev9 = self.state.prev_ema9 or self.state.ema9
        prev21 = self.state.prev_ema21 or self.state.ema21
        ema9 = self.state.ema9
        ema21 = self.state.ema21
        rsi = self.state.rsi14

        # Risk parameters (1% risco, 2% ganho)
        stake = self.calculate_stake()
        stop_loss_usd = round(stake, 2)  # Risco 1%
        take_profit_usd = round(stake * 2.0, 2)  # Ganho 2x (Payoff 1:2)

        # COMPRA (MULTUP):
        # 1. EMA 9 > EMA 21 (ou acabou de cruzar acima)
        # 2. RSI 14 entre 50 e 70
        is_buy_cross = (prev9 <= prev21 and ema9 > ema21) or (ema9 > ema21 and rsi > 50.0 and rsi < 70.0)
        if is_buy_cross and 50.0 < rsi < 70.0:
            self.state.last_traded_epoch = epoch
            return V100Signal(
                symbol=self.symbol,
                mode="CONSERVATIVE",
                contract_type="MULTUP",
                stake_usd=stake,
                duration=1,
                duration_unit="m",
                multiplier=100,
                stop_loss_usd=stop_loss_usd,
                take_profit_usd=take_profit_usd,
                reason=f"Modo Conservador COMPRA: EMA9 ({ema9:.2f}) > EMA21 ({ema21:.2f}) + RSI14 ({rsi:.1f}) entre 50 e 70. Payoff 1:2 (SL: ${stop_loss_usd:.2f}, TP: ${take_profit_usd:.2f})",
                current_spot=price,
            )

        # VENDA (MULTDOWN):
        # 1. EMA 9 < EMA 21 (ou acabou de cruzar abaixo)
        # 2. RSI 14 entre 30 e 50
        is_sell_cross = (prev9 >= prev21 and ema9 < ema21) or (ema9 < ema21 and rsi < 50.0 and rsi > 30.0)
        if is_sell_cross and 30.0 < rsi < 50.0:
            self.state.last_traded_epoch = epoch
            return V100Signal(
                symbol=self.symbol,
                mode="CONSERVATIVE",
                contract_type="MULTDOWN",
                stake_usd=stake,
                duration=1,
                duration_unit="m",
                multiplier=100,
                stop_loss_usd=stop_loss_usd,
                take_profit_usd=take_profit_usd,
                reason=f"Modo Conservador VENDA: EMA9 ({ema9:.2f}) < EMA21 ({ema21:.2f}) + RSI14 ({rsi:.1f}) entre 30 e 50. Payoff 1:2 (SL: ${stop_loss_usd:.2f}, TP: ${take_profit_usd:.2f})",
                current_spot=price,
            )

        return None

    def on_contract_opened(self, contract_id: str) -> None:
        """Lock engine atomically until this contract is settled."""
        self.state.in_flight_contract_id = str(contract_id)

    def on_contract_settled(self, contract_id: str, profit_usd: float, is_win: bool) -> Dict[str, Any]:
        """Release atomic lock, update recovery progression and session metrics."""
        self.state.in_flight_contract_id = None
        pnl = float(profit_usd)
        self.session_profit_usd = round(self.session_profit_usd + pnl, 4)

        if is_win:
            self.total_wins += 1
            self.state.consecutive_losses = 0
            self.state.recovery_level = 0  # Reset back to base stake
        else:
            self.total_losses += 1
            self.state.consecutive_losses += 1

            # Progression logic
            if self.mode == "AGGRESSIVE":
                # Split Martingale 3 levels max
                if self.state.recovery_level < 3:
                    self.state.recovery_level += 1
                else:
                    self.state.recovery_level = 0  # Hard stop reset to base
            elif self.mode == "MODERATE":
                # 3 levels max
                if self.state.recovery_level < 2:
                    self.state.recovery_level += 1
                else:
                    self.state.recovery_level = 0  # Hard stop reset to base
            elif self.mode == "CONSERVATIVE":
                # Fixed stake: zero multiplier progression
                self.state.recovery_level = 0

        next_stake = self.calculate_stake()
        result_info = {
            "pnl": pnl,
            "is_win": is_win,
            "recovery_level": self.state.recovery_level,
            "next_stake_usd": next_stake,
            "session_profit_usd": self.session_profit_usd,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
        }
        self.recent_trades.append(result_info)
        return result_info

    def get_telemetry(self) -> Dict[str, Any]:
        """Telemetry snapshot for WebSocket streaming to frontend."""
        digits_list = [d for d, _, _ in self.state.digits]
        return {
            "symbol": self.symbol,
            "mode": self.mode,
            "last_digit": digits_list[-1] if digits_list else None,
            "recent_digits": digits_list[-25:],
            "recovery_level": self.state.recovery_level,
            "current_stake_usd": self.calculate_stake(),
            "next_stake": self.calculate_stake(),
            "in_flight": bool(self.state.in_flight_contract_id),
            "in_flight_contract_id": self.state.in_flight_contract_id,
            "session_profit_usd": self.session_profit_usd,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "consecutive_losses": self.state.consecutive_losses,
            "ema9": round(self.state.ema9, 2) if self.state.ema9 else None,
            "ema21": round(self.state.ema21, 2) if self.state.ema21 else None,
            "rsi14": round(self.state.rsi14, 1) if self.state.rsi14 else None,
            "is_locked": self.is_locked,
            "lock_reason": self.lock_reason,
        }
