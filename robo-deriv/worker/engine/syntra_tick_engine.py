"""SyntraTickEngine - High-Frequency Tick Trading Engine for Deriv.

Replicates the exact Syntra "Deep Analysis" tick trading model:
- Targets Jump 25 Index (JD25) & Bull Market Index (RDBULL) or Volatility Indices.
- Evaluates tick stream at 1Hz (1 tick per second).
- Executes 5-tick Rise/Fall contracts with STRICT FIXED RISK (Zero DCA, Zero Multipliers).
- 1 trade per symbol at a time; auto-settles in 5 ticks via Deriv API.
"""

from collections import deque
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Optional

logger = logging.getLogger("worker.syntra_tick_engine")

SUPPORTED_SYMBOLS = ["JD25", "RDBULL", "1HZ100V", "R_100"]


@dataclass
class SymbolTickState:
    symbol: str
    ticks: deque = field(default_factory=lambda: deque(maxlen=120))
    ema20: Optional[float] = None
    std60: Optional[float] = None
    z_score: Optional[float] = None
    last_signal_time: float = 0.0
    active_contract_id: Optional[str] = None
    active_contract_type: Optional[str] = None
    active_entry_spot: Optional[float] = None
    active_start_time: float = 0.0
    status_text: str = "ANALISANDO"


@dataclass
class SyntraSignal:
    symbol: str
    direction: str  # "CALL" or "PUT"
    stake_usd: float
    duration_ticks: int = 5
    reason: str = ""
    current_spot: float = 0.0
    z_score: float = 0.0


class SyntraTickEngine:
    """Quantitative tick-based engine for high-frequency Deriv trading."""

    def __init__(
        self,
        symbols: Optional[list[str]] = None,
        duration_ticks: int = 5,
        z_threshold: float = 1.30,
        cooldown_sec: float = 6.0,
    ) -> None:
        self.symbols = symbols or ["JD25", "RDBULL"]
        self.duration_ticks = duration_ticks
        self.z_threshold = z_threshold
        self.cooldown_sec = cooldown_sec

        self.states: dict[str, SymbolTickState] = {
            sym: SymbolTickState(symbol=sym) for sym in self.symbols
        }

        # Session tracking
        self.total_wins: int = 0
        self.total_losses: int = 0
        self.session_profit_usd: float = 0.0
        self.recent_trades: deque = deque(maxlen=50)

    def on_tick(self, symbol: str, price: float, epoch: float) -> Optional[SyntraSignal]:
        """Process an incoming live tick and check for Deep Analysis entry signals."""
        if symbol not in self.states:
            self.states[symbol] = SymbolTickState(symbol=symbol)

        state = self.states[symbol]
        state.ticks.append((price, epoch))

        # Update EMA-20
        alpha = 2.0 / (20.0 + 1.0)
        if state.ema20 is None:
            state.ema20 = price
        else:
            state.ema20 = alpha * price + (1.0 - alpha) * state.ema20

        # Update Std-60 & Z-Score
        prices_window = [p for p, _ in list(state.ticks)[-60:]]
        if len(prices_window) >= 15:
            mean = sum(prices_window) / len(prices_window)
            variance = sum((p - mean) ** 2 for p in prices_window) / len(prices_window)
            state.std60 = math.sqrt(variance)
            if state.std60 > 1e-6 and state.ema20 is not None:
                state.z_score = (price - state.ema20) / state.std60
            else:
                state.z_score = 0.0

        # If a contract is currently active for this symbol, do not open another
        if state.active_contract_id:
            state.status_text = "OPERACAO_ABERTA"
            return None

        state.status_text = "ANALISANDO"

        # Check cooldown
        if epoch - state.last_signal_time < self.cooldown_sec:
            return None

        # Deep Analysis Entry Rules:
        # Need at least 20 ticks of history
        if len(state.ticks) < 20 or state.z_score is None:
            return None

        p_curr = prices_window[-1]
        p_prev = prices_window[-2]
        p_3ago = prices_window[-3] if len(prices_window) >= 3 else p_prev

        # 1. Bullish Signal (CALL):
        # Micro pullback exhaustion (Z <= -z_threshold) and turning positive (p_curr > p_prev)
        # OR Micro-momentum breakout in Bull Market (p_curr > p_prev > p_3ago with z > 0.5)
        if state.z_score <= -self.z_threshold and p_curr > p_prev:
            state.last_signal_time = epoch
            return SyntraSignal(
                symbol=symbol,
                direction="CALL",
                stake_usd=0.60,
                duration_ticks=self.duration_ticks,
                reason=f"Exaustão de baixa revertendo (Z={state.z_score:.2f} <= -{self.z_threshold})",
                current_spot=price,
                z_score=state.z_score,
            )

        # 2. Bearish Signal (PUT):
        # Micro rally exhaustion (Z >= +z_threshold) and turning down (p_curr < p_prev)
        if state.z_score >= self.z_threshold and p_curr < p_prev:
            state.last_signal_time = epoch
            return SyntraSignal(
                symbol=symbol,
                direction="PUT",
                stake_usd=0.60,
                duration_ticks=self.duration_ticks,
                reason=f"Exaustão de alta revertendo (Z={state.z_score:.2f} >= +{self.z_threshold})",
                current_spot=price,
                z_score=state.z_score,
            )

        # 3. Momentum Continuation for Bull Market Index (RDBULL):
        if symbol == "RDBULL" and p_curr > p_prev > p_3ago and state.z_score > 0.50:
            state.last_signal_time = epoch
            return SyntraSignal(
                symbol=symbol,
                direction="CALL",
                stake_usd=0.60,
                duration_ticks=self.duration_ticks,
                reason="RDBULL Impulso de Alta Confirmado (3 ticks consecutivos)",
                current_spot=price,
                z_score=state.z_score,
            )

        return None

    def on_contract_opened(self, symbol: str, contract_id: str, contract_type: str, entry_spot: float, epoch: float) -> None:
        """Mark contract as open for the given symbol."""
        if symbol in self.states:
            state = self.states[symbol]
            state.active_contract_id = contract_id
            state.active_contract_type = contract_type
            state.active_entry_spot = entry_spot
            state.active_start_time = epoch
            state.status_text = "OPERACAO_ABERTA"

    def on_contract_closed(self, symbol: str, contract_id: str, profit_usd: float, exit_spot: float, epoch: float) -> dict[str, Any]:
        """Record trade completion and update stats."""
        is_win = profit_usd > 0
        if is_win:
            self.total_wins += 1
        else:
            self.total_losses += 1

        self.session_profit_usd += profit_usd

        trade_info = {
            "symbol": symbol,
            "contract_id": contract_id,
            "profit_usd": round(profit_usd, 2),
            "is_win": is_win,
            "exit_spot": exit_spot,
            "epoch": epoch,
        }
        self.recent_trades.appendleft(trade_info)

        if symbol in self.states:
            state = self.states[symbol]
            if state.active_contract_id == contract_id:
                state.active_contract_id = None
                state.active_contract_type = None
                state.active_entry_spot = None
                state.status_text = "ANALISANDO"

        return trade_info

    def get_telemetry(self) -> dict[str, Any]:
        """Return real-time state for dashboard rendering."""
        markets = {}
        for sym, st in self.states.items():
            last_px = st.ticks[-1][0] if st.ticks else 0.0
            markets[sym] = {
                "status": st.status_text,
                "spot": last_px,
                "z_score": round(st.z_score, 2) if st.z_score is not None else None,
                "ema20": round(st.ema20, 2) if st.ema20 is not None else None,
                "has_open_trade": st.active_contract_id is not None,
            }

        total_trades = self.total_wins + self.total_losses
        win_rate = (self.total_wins / total_trades * 100.0) if total_trades > 0 else 0.0

        return {
            "markets": markets,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate": round(win_rate, 1),
            "session_profit_usd": round(self.session_profit_usd, 2),
            "recent_trades": list(self.recent_trades)[:20],
        }
