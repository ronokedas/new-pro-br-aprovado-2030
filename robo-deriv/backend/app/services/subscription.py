import json
import math
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from shared.models import Basket, BotConfig, BotEventLog, Contract, DerivAccount, User, utcnow


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize naive datetimes (e.g., from SQLite) to timezone-aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    norm = ensure_utc(dt)
    return norm.isoformat() if norm is not None else None


def is_user_expired(user: User, now: Optional[datetime] = None) -> bool:
    ref = ensure_utc(now) if now is not None else utcnow()
    exp = ensure_utc(user.expires_at)
    if exp is None:
        return True
    return ref > exp


def compute_days_remaining(user: User, now: Optional[datetime] = None) -> int:
    ref = ensure_utc(now) if now is not None else utcnow()
    exp = ensure_utc(user.expires_at)
    if exp is None or ref >= exp:
        return 0
    delta_seconds = (exp - ref).total_seconds()
    return max(0, math.ceil(delta_seconds / 86400.0))


def serialize_user(user: User) -> dict[str, Any]:
    """Serialize User safely — NEVER includes password_hash or tokens."""
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "expires_at": iso_utc(user.expires_at),
        "days_remaining": compute_days_remaining(user),
        "is_expired": is_user_expired(user),
        "max_stake_usd": float(user.max_stake_usd),
        "can_access_btc_radar": bool(getattr(user, "can_access_btc_radar", True)),
        "must_change_password": bool(user.must_change_password),
        "created_at": iso_utc(user.created_at),
        "updated_at": iso_utc(user.updated_at),
    }


def serialize_deriv_account(acc: DerivAccount) -> dict[str, Any]:
    """Serialize DerivAccount safely — NEVER exposes token_encrypted or plaintext token."""
    return {
        "id": acc.id,
        "loginid": acc.loginid,
        "account_type": acc.account_type,
        "currency": acc.currency,
        "balance": round(float(acc.balance or 0.0), 4),
        "is_active_selected": bool(acc.is_active_selected),
        "last_synced_at": iso_utc(acc.last_synced_at),
        "created_at": iso_utc(acc.created_at),
    }


def serialize_bot_config(cfg: BotConfig) -> dict[str, Any]:
    try:
        layer_multipliers = json.loads(cfg.layer_multipliers_json)
        if not isinstance(layer_multipliers, list):
            layer_multipliers = [1.0, 1.0, 1.5]
    except Exception:
        layer_multipliers = [1.0, 1.0, 1.5]

    return {
        "id": cfg.id,
        "user_id": cfg.user_id,
        "enabled": bool(cfg.enabled),
        "symbol": cfg.symbol,
        "timeframe_sec": int(cfg.timeframe_sec),
        "direction_mode": cfg.direction_mode,
        "stake_usd": float(cfg.stake_usd),
        "multiplier": int(cfg.multiplier),
        "max_layers": int(cfg.max_layers),
        "layer_multipliers": [float(x) for x in layer_multipliers],
        "layer_multipliers_json": cfg.layer_multipliers_json,
        "basket_tp_usd": float(cfg.basket_tp_usd),
        "trailing_enabled": bool(cfg.trailing_enabled),
        "max_basket_loss_usd": float(cfg.max_basket_loss_usd),
        "max_daily_loss_usd": float(cfg.max_daily_loss_usd),
        "daily_profit_target_usd": float(cfg.daily_profit_target_usd),
        "max_consecutive_losses": int(cfg.max_consecutive_losses),
        "max_hold_minutes": int(cfg.max_hold_minutes),
        "use_binance_filter": bool(cfg.use_binance_filter),
        "strategy_mode": str(getattr(cfg, "strategy_mode", "SMART_CHANNEL_DCA") or "SMART_CHANNEL_DCA"),
        "channel_window": int(getattr(cfg, "channel_window", 60) or 60),
        "channel_sigma": float(getattr(cfg, "channel_sigma", 2.0) or 2.0),
        "atr_period": int(getattr(cfg, "atr_period", 10) or 10),
        "tp_atr_mult": float(getattr(cfg, "tp_atr_mult", 0.75) or 0.75),
        "sl_atr_mult": float(getattr(cfg, "sl_atr_mult", 1.90) or 1.90),
        "use_crash_guard": bool(getattr(cfg, "use_crash_guard", True)),
        "multi_pair_enabled": bool(getattr(cfg, "multi_pair_enabled", False)),
        "scanner_symbols_json": str(getattr(cfg, "scanner_symbols_json", "") or '["cryBTCUSD", "cryETHUSD", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "1HZ100V", "R_75"]'),
        "session_profit_usd": float(getattr(cfg, "session_profit_usd", 0.0) or 0.0),
        "session_cycles_count": int(getattr(cfg, "session_cycles_count", 0) or 0),
        "grid_step_pct": float(getattr(cfg, "grid_step_pct", 0.25) or 0.25),
        "status_text": cfg.status_text,
        "updated_at": iso_utc(cfg.updated_at),
    }


def serialize_contract(c: Contract) -> dict[str, Any]:
    return {
        "id": c.id,
        "basket_id": c.basket_id,
        "layer_index": int(c.layer_index),
        "deriv_contract_id": c.deriv_contract_id,
        "contract_type": c.contract_type,
        "symbol": c.symbol,
        "stake_usd": float(c.stake_usd),
        "multiplier": int(c.multiplier),
        "commission_usd": float(c.commission_usd),
        "entry_spot": float(c.entry_spot),
        "stop_out_spot": float(c.stop_out_spot) if c.stop_out_spot is not None else None,
        "current_spot": float(c.current_spot) if c.current_spot is not None else None,
        "current_profit_usd": float(c.current_profit_usd),
        "exit_spot": float(c.exit_spot) if c.exit_spot is not None else None,
        "final_profit_usd": float(c.final_profit_usd) if c.final_profit_usd is not None else None,
        "status": c.status,
        "opened_at": iso_utc(c.opened_at),
        "closed_at": iso_utc(c.closed_at),
    }


def serialize_basket(basket: Basket, include_contracts: bool = True) -> dict[str, Any]:
    opened = ensure_utc(basket.opened_at)
    closed = ensure_utc(basket.closed_at)
    duration_sec: Optional[int] = None
    if opened is not None and closed is not None:
        duration_sec = max(0, int((closed - opened).total_seconds()))

    data: dict[str, Any] = {
        "id": basket.id,
        "user_id": basket.user_id,
        "deriv_account_id": basket.deriv_account_id,
        "account_type": basket.account_type,
        "symbol": basket.symbol,
        "direction": basket.direction,
        "status": basket.status,
        "layers_count": int(basket.layers_count),
        "total_stake_usd": round(float(basket.total_stake_usd), 4),
        "total_commission_usd": round(float(basket.total_commission_usd), 4),
        "avg_entry_price": float(basket.avg_entry_price),
        "current_pnl_usd": round(float(basket.current_pnl_usd), 4),
        "realized_pnl_usd": round(float(basket.realized_pnl_usd), 4),
        "peak_pnl_usd": round(float(basket.peak_pnl_usd), 4),
        "close_reason": basket.close_reason,
        "opened_at": iso_utc(basket.opened_at),
        "closed_at": iso_utc(basket.closed_at),
        "duration_sec": duration_sec,
    }
    if include_contracts:
        contracts = sorted(basket.contracts or [], key=lambda x: x.layer_index)
        data["contracts"] = [serialize_contract(c) for c in contracts]
    return data


def serialize_event_log(ev: BotEventLog) -> dict[str, Any]:
    details = None
    if ev.details_json:
        try:
            details = json.loads(ev.details_json)
        except Exception:
            details = ev.details_json
    return {
        "id": ev.id,
        "user_id": ev.user_id,
        "level": ev.level,
        "symbol": ev.symbol,
        "message": ev.message,
        "details": details,
        "created_at": iso_utc(ev.created_at),
    }


def ensure_bot_config(db: Session, user_id: int) -> BotConfig:
    """Return the user's BotConfig, creating a default one if absent."""
    cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
    if cfg is None:
        cfg = BotConfig(user_id=user_id)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg
