"""Per-User Risk Circuit Breakers & Safe Subscription Expiration Guard.

CRITICAL SAFETY RULE (Safe Subscription Expiration):
- When `now() > user.expires_at` OR `user.status != "ACTIVE"`:
  * If an open basket exists: returns `EXPIRED_DRAINING`
    -> BLOCKS new entries and DCA re-entries immediately,
    -> ALLOWS exit management (TP / Trailing / SL / Channel Break / Timeout)
       until the basket closes cleanly, then flags `auto_disable_after_close = True`.
  * If no open basket exists: returns `EXPIRED_STOPPED`
    -> BLOCKS everything and flags `should_disable_bot = True`.
Never abandon an open real-money position unmanaged!
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass
class RiskConfig:
    max_daily_loss_usd: float = 10.0
    daily_profit_target_usd: float = 15.0
    max_consecutive_losses: int = 3
    cooldown_candles_after_close: int = 2


@dataclass
class UserAccessState:
    status: str  # "ACTIVE" | "SUSPENDED"
    expires_at: datetime


@dataclass
class RiskVerdict:
    code: str  # ALLOWED | EXPIRED_DRAINING | EXPIRED_STOPPED | DAILY_LOSS_STOP | DAILY_PROFIT_STOP | CONSECUTIVE_LOSS_STOP | COOLDOWN
    allow_new_entry: bool
    allow_reentry: bool
    allow_exit: bool
    should_disable_bot: bool = False
    auto_disable_after_close: bool = False
    reason: Optional[str] = None
    status_text: str = "LIGADO"


class RiskGuard:
    """Tracks daily realized + floating PnL, consecutive losses, cooldowns, and subscription validity."""

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config or RiskConfig()
        self.utc_day: date = datetime.now(timezone.utc).date()
        self.realized_pnl_today: float = 0.0
        self.floating_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.cooldown_bars_left: int = 0

    def update_config(self, config: RiskConfig) -> None:
        self.config = config

    def _roll_utc_day_if_needed(self, now: datetime) -> None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        today = now.astimezone(timezone.utc).date()
        if today != self.utc_day:
            self.utc_day = today
            self.realized_pnl_today = 0.0
            self.consecutive_losses = 0
            self.cooldown_bars_left = 0

    def load_daily_stats(
        self,
        realized_pnl_today: float,
        consecutive_losses: int,
        now: Optional[datetime] = None,
    ) -> None:
        ts_now = now or datetime.now(timezone.utc)
        if ts_now.tzinfo is None:
            ts_now = ts_now.replace(tzinfo=timezone.utc)
        self.utc_day = ts_now.astimezone(timezone.utc).date()
        self.realized_pnl_today = float(realized_pnl_today)
        self.consecutive_losses = max(0, int(consecutive_losses))

    def set_floating_pnl(self, floating_pnl: float) -> None:
        self.floating_pnl = float(floating_pnl)

    @property
    def total_daily_pnl(self) -> float:
        return round(self.realized_pnl_today + self.floating_pnl, 6)

    def record_closed_basket(self, realized_pnl_usd: float, now: Optional[datetime] = None) -> None:
        ts_now = now or datetime.now(timezone.utc)
        self._roll_utc_day_if_needed(ts_now)
        pnl = float(realized_pnl_usd)
        self.realized_pnl_today = round(self.realized_pnl_today + pnl, 6)
        self.floating_pnl = 0.0
        if pnl < 0.0:
            self.consecutive_losses += 1
        elif pnl > 0.0:
            self.consecutive_losses = 0
        self.cooldown_bars_left = max(0, int(self.config.cooldown_candles_after_close))

    def on_candle_close(self, now: Optional[datetime] = None) -> None:
        ts_now = now or datetime.now(timezone.utc)
        self._roll_utc_day_if_needed(ts_now)
        if self.cooldown_bars_left > 0:
            self.cooldown_bars_left -= 1

    def evaluate(
        self,
        user_access: UserAccessState,
        has_open_basket: bool,
        now: Optional[datetime] = None,
    ) -> RiskVerdict:
        """Evaluate subscription validity and all circuit breakers."""
        ts_now = now or datetime.now(timezone.utc)
        if ts_now.tzinfo is None:
            ts_now = ts_now.replace(tzinfo=timezone.utc)
        self._roll_utc_day_if_needed(ts_now)

        expires_at = user_access.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        is_suspended = (user_access.status or "").upper() != "ACTIVE"
        is_expired = ts_now > expires_at

        # 1. Safe Subscription / Status Expiration Guard (HIGHEST PRIORITY)
        if is_suspended or is_expired:
            reason = (
                "Usuário suspenso pelo administrador"
                if is_suspended
                else "Assinatura expirada"
            )
            if has_open_basket:
                return RiskVerdict(
                    code="EXPIRED_DRAINING",
                    allow_new_entry=False,
                    allow_reentry=False,
                    allow_exit=True,
                    should_disable_bot=False,
                    auto_disable_after_close=True,
                    reason=f"{reason} — gerenciando saída segura do cesto aberto",
                    status_text="ENCERRANDO CESTO",
                )
            return RiskVerdict(
                code="EXPIRED_STOPPED",
                allow_new_entry=False,
                allow_reentry=False,
                allow_exit=False,
                should_disable_bot=True,
                auto_disable_after_close=False,
                reason=f"{reason} — robô desligado automaticamente",
                status_text="ASSINATURA EXPIRADA" if is_expired else "SUSPENSO",
            )

        # 2. Daily Loss Circuit Breaker (realized + floating)
        total_pnl = self.total_daily_pnl
        if self.config.max_daily_loss_usd > 0.0 and total_pnl <= -abs(self.config.max_daily_loss_usd):
            return RiskVerdict(
                code="DAILY_LOSS_STOP",
                allow_new_entry=False,
                allow_reentry=False,
                allow_exit=True,
                should_disable_bot=not has_open_basket,
                auto_disable_after_close=has_open_basket,
                reason=(
                    f"Stop diário atingido (PnL dia: ${total_pnl:.2f} "
                    f"<= -${abs(self.config.max_daily_loss_usd):.2f})"
                ),
                status_text="STOP DIÁRIO",
            )

        # 3. Daily Profit Target
        if (
            self.config.daily_profit_target_usd > 0.0
            and self.realized_pnl_today >= abs(self.config.daily_profit_target_usd)
        ):
            return RiskVerdict(
                code="DAILY_PROFIT_STOP",
                allow_new_entry=False,
                allow_reentry=False,
                allow_exit=True,
                should_disable_bot=False,
                auto_disable_after_close=False,
                reason=(
                    f"Meta de lucro diária atingida (+${self.realized_pnl_today:.2f} "
                    f">= +${abs(self.config.daily_profit_target_usd):.2f})"
                ),
                status_text="META DIÁRIA",
            )

        # 4. Max Consecutive Losses
        if (
            self.config.max_consecutive_losses > 0
            and self.consecutive_losses >= self.config.max_consecutive_losses
        ):
            return RiskVerdict(
                code="CONSECUTIVE_LOSS_STOP",
                allow_new_entry=False,
                allow_reentry=False,
                allow_exit=True,
                should_disable_bot=False,
                auto_disable_after_close=False,
                reason=(
                    f"Pausa por sequência de perdas "
                    f"({self.consecutive_losses}/{self.config.max_consecutive_losses} ciclos negativos)"
                ),
                status_text="STOP SEQUÊNCIA",
            )

        # 5. Cooldown between cycles (only blocks new Entry #1)
        if not has_open_basket and self.cooldown_bars_left > 0:
            return RiskVerdict(
                code="COOLDOWN",
                allow_new_entry=False,
                allow_reentry=False,
                allow_exit=True,
                reason=f"Aguardando cooldown ({self.cooldown_bars_left} velas restantes)",
                status_text="COOLDOWN",
            )

        return RiskVerdict(
            code="ALLOWED",
            allow_new_entry=True,
            allow_reentry=True,
            allow_exit=True,
            reason=None,
            status_text="EM OPERAÇÃO" if has_open_basket else "LIGADO",
        )
