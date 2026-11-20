import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.redis_client import publish_control_event, publish_user_event
from app.core.security import get_current_user
from app.services.subscription import (
    compute_days_remaining,
    ensure_bot_config,
    is_user_expired,
    iso_utc,
    serialize_basket,
    serialize_bot_config,
    serialize_deriv_account,
    serialize_event_log,
)
from shared.database import get_db
from shared.models import Basket, BotEventLog, DerivAccount, User

router = APIRouter(prefix="/api/bot", tags=["Bot"])

ALLOWED_TIMEFRAMES = {60, 180, 300, 900, 1800, 3600}


class BotConfigUpdateRequest(BaseModel):
    symbol: Optional[str] = Field(default=None, min_length=2, max_length=64)
    timeframe_sec: Optional[int] = None
    direction_mode: Optional[Literal["LONG_ONLY", "SHORT_ONLY", "BOTH"]] = None
    stake_usd: Optional[float] = None
    multiplier: Optional[int] = Field(default=None, ge=1, le=5000)
    max_layers: Optional[int] = None
    layer_multipliers: Optional[list[float]] = None
    layer_multipliers_json: Optional[str] = None
    basket_tp_usd: Optional[float] = None
    trailing_enabled: Optional[bool] = None
    max_basket_loss_usd: Optional[float] = None
    max_daily_loss_usd: Optional[float] = None
    daily_profit_target_usd: Optional[float] = None
    max_consecutive_losses: Optional[int] = Field(default=None, ge=0, le=20)
    max_hold_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    use_binance_filter: Optional[bool] = None
    strategy_mode: Optional[str] = None
    channel_window: Optional[int] = Field(default=None, ge=20, le=150)
    channel_sigma: Optional[float] = Field(default=None, ge=1.0, le=3.5)
    atr_period: Optional[int] = Field(default=None, ge=5, le=50)
    tp_atr_mult: Optional[float] = Field(default=None, gt=0.1, le=5.0)
    sl_atr_mult: Optional[float] = Field(default=None, gt=0.2, le=8.0)
    use_crash_guard: Optional[bool] = None
    multi_pair_enabled: Optional[bool] = None
    scanner_symbols_json: Optional[str] = None
    session_profit_usd: Optional[float] = None
    session_cycles_count: Optional[int] = None
    grid_step_pct: Optional[float] = None


class BotToggleRequest(BaseModel):
    enabled: bool


@router.get("/config")
def get_bot_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = ensure_bot_config(db, current_user.id)

    active_account = (
        db.query(DerivAccount)
        .filter(
            DerivAccount.user_id == current_user.id,
            DerivAccount.is_active_selected.is_(True),
        )
        .first()
    )

    open_basket = (
        db.query(Basket)
        .filter(Basket.user_id == current_user.id, Basket.status == "OPEN")
        .order_by(Basket.opened_at.desc())
        .first()
    )

    recent_events = (
        db.query(BotEventLog)
        .filter(BotEventLog.user_id == current_user.id)
        .order_by(BotEventLog.created_at.desc())
        .limit(20)
        .all()
    )

    live_state = None
    try:
        from app.core.redis_client import get_sync_redis
        from shared.redis_keys import key_user_state
        r = get_sync_redis()
        if r is not None:
            raw_s = r.get(key_user_state(current_user.id))
            if raw_s:
                live_state = json.loads(raw_s)
    except Exception:
        pass

    return {
        "config": serialize_bot_config(cfg),
        "active_account": serialize_deriv_account(active_account) if active_account else None,
        "open_basket": serialize_basket(open_basket, include_contracts=True) if open_basket else None,
        "recent_events": [serialize_event_log(e) for e in recent_events],
        "live_state": live_state,
        "subscription": {
            "status": current_user.status,
            "expires_at": iso_utc(current_user.expires_at),
            "days_remaining": compute_days_remaining(current_user),
            "is_expired": is_user_expired(current_user),
            "max_stake_usd": float(current_user.max_stake_usd),
        },
    }


@router.put("/config")
def update_bot_config(
    body: BotConfigUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = ensure_bot_config(db, current_user.id)

    if body.stake_usd is not None:
        min_allowed = 0.35 if (body.strategy_mode == "SYNTRA_TICKS" or getattr(cfg, "strategy_mode", "") == "SYNTRA_TICKS") else 0.50
        if body.stake_usd < min_allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"O stake mínimo permitido é ${min_allowed:.2f} USD.",
            )
        if body.stake_usd > float(current_user.max_stake_usd):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"O stake não pode exceder seu limite máximo de ${current_user.max_stake_usd:.2f} USD.",
            )
        cfg.stake_usd = round(float(body.stake_usd), 2)

    if body.max_layers is not None:
        if body.max_layers < 1 or body.max_layers > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O número máximo de camadas DCA deve estar entre 1 e 5.",
            )
        cfg.max_layers = int(body.max_layers)

    if body.timeframe_sec is not None:
        if body.timeframe_sec not in ALLOWED_TIMEFRAMES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Timeframe inválido. Valores aceitos (segundos): {sorted(ALLOWED_TIMEFRAMES)}.",
            )
        cfg.timeframe_sec = int(body.timeframe_sec)

    if body.basket_tp_usd is not None:
        if body.basket_tp_usd <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A meta de lucro do cesto (basket_tp_usd) deve ser maior que zero.",
            )
        cfg.basket_tp_usd = round(float(body.basket_tp_usd), 4)

    if body.max_basket_loss_usd is not None:
        if body.max_basket_loss_usd <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O stop loss global do cesto (max_basket_loss_usd) deve ser maior que zero.",
            )
        cfg.max_basket_loss_usd = round(float(body.max_basket_loss_usd), 4)

    if body.max_daily_loss_usd is not None:
        if body.max_daily_loss_usd <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O stop loss diário (max_daily_loss_usd) deve ser maior que zero.",
            )
        cfg.max_daily_loss_usd = round(float(body.max_daily_loss_usd), 4)

    if body.daily_profit_target_usd is not None:
        if body.daily_profit_target_usd <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A meta diária de lucro (daily_profit_target_usd) deve ser maior que zero.",
            )
        cfg.daily_profit_target_usd = round(float(body.daily_profit_target_usd), 4)

    raw_mults = body.layer_multipliers
    if raw_mults is None and body.layer_multipliers_json is not None:
        try:
            parsed = json.loads(body.layer_multipliers_json)
            if isinstance(parsed, list):
                raw_mults = [float(x) for x in parsed]
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON de multiplicadores de camada inválido.",
            )

    if raw_mults is not None:
        if not raw_mults or len(raw_mults) > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Progressão de camadas inválida (1 a 5 fatores).",
            )
        cleaned_mults = []
        for m in raw_mults:
            val = round(float(m), 2)
            if val < 1.0 or val > 1.8:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cada multiplicador de camada DCA deve estar entre 1.0x e 1.8x (Martingale agressivo é proibido).",
                )
            cleaned_mults.append(val)
        cfg.layer_multipliers_json = json.dumps(cleaned_mults)

    if body.symbol is not None:
        cfg.symbol = body.symbol.strip()
    if body.direction_mode is not None:
        cfg.direction_mode = body.direction_mode
    if body.multiplier is not None:
        cfg.multiplier = int(body.multiplier)
    if body.trailing_enabled is not None:
        cfg.trailing_enabled = bool(body.trailing_enabled)
    if body.max_consecutive_losses is not None:
        cfg.max_consecutive_losses = int(body.max_consecutive_losses)
    if body.max_hold_minutes is not None:
        cfg.max_hold_minutes = int(body.max_hold_minutes)
    if body.use_binance_filter is not None:
        cfg.use_binance_filter = bool(body.use_binance_filter)
    if body.strategy_mode is not None:
        cfg.strategy_mode = body.strategy_mode
    if body.channel_window is not None:
        cfg.channel_window = int(body.channel_window)
    if body.channel_sigma is not None:
        cfg.channel_sigma = round(float(body.channel_sigma), 2)
    if body.atr_period is not None:
        cfg.atr_period = int(body.atr_period)
    if body.tp_atr_mult is not None:
        cfg.tp_atr_mult = round(float(body.tp_atr_mult), 2)
    if body.sl_atr_mult is not None:
        cfg.sl_atr_mult = round(float(body.sl_atr_mult), 2)
    if body.use_crash_guard is not None:
        cfg.use_crash_guard = bool(body.use_crash_guard)
    if body.multi_pair_enabled is not None:
        cfg.multi_pair_enabled = bool(body.multi_pair_enabled)
    if body.scanner_symbols_json is not None:
        cfg.scanner_symbols_json = str(body.scanner_symbols_json)
    if body.session_profit_usd is not None:
        cfg.session_profit_usd = round(float(body.session_profit_usd), 4)
    if body.session_cycles_count is not None:
        cfg.session_cycles_count = int(body.session_cycles_count)
    if body.grid_step_pct is not None:
        cfg.grid_step_pct = round(float(body.grid_step_pct), 4)

    if cfg.strategy_mode == "SYNTRA_TICKS":
        mode_lbl = f"Robô Syntra • Deep Analysis Ticks (Meta ${cfg.daily_profit_target_usd:.2f} / Stop ${cfg.max_daily_loss_usd:.2f})"
    elif cfg.strategy_mode in ("SWING_GRID_RESPIROS", "BOTH_ROBOTS"):
        mode_lbl = f"Swing Grid • Acumulador de Respiros (Meta ${cfg.daily_profit_target_usd:.2f} / Stop ${cfg.max_daily_loss_usd:.2f})"
    elif cfg.strategy_mode == "GRADIENTE_LINEAR":
        mode_lbl = f"Gradiente Linear M1 (Meta ${cfg.daily_profit_target_usd:.2f} / Stop ${cfg.max_daily_loss_usd:.2f})"
    elif cfg.strategy_mode == "TICK_MEAN_REVERSION":
        mode_lbl = f"Reversão Tick 1Hz (Z±2.0 + EMA20, Meta ${cfg.daily_profit_target_usd:.2f} / Stop ${cfg.max_daily_loss_usd:.2f})"
    elif cfg.strategy_mode == "LINREG_ATR_VIDEO":
        mode_lbl = f"Regressão+ATR ({cfg.channel_window}±{cfg.channel_sigma}σ, TP {cfg.tp_atr_mult}x/SL {cfg.sl_atr_mult}x ATR{cfg.atr_period})"
    else:
        mode_lbl = f"Smart Channel DCA (TP ${cfg.basket_tp_usd:.2f})"
    db.add(
        BotEventLog(
            user_id=current_user.id,
            level="INFO",
            symbol=cfg.symbol,
            message=(
                f"Configuração atualizada [{mode_lbl}]: {cfg.symbol} | Stake ${cfg.stake_usd:.2f} | "
                f"Mult {cfg.multiplier}x | Camadas {cfg.max_layers}"
            ),
        )
    )
    db.commit()
    db.refresh(cfg)

    publish_control_event({"action": "RELOAD_CONFIG", "user_id": current_user.id})
    return {"config": serialize_bot_config(cfg)}


@router.post("/toggle")
def toggle_bot(
    body: BotToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = ensure_bot_config(db, current_user.id)

    if body.enabled:
        if current_user.status != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Conta suspensa. Não é possível ligar o robô.",
            )
        if is_user_expired(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Assinatura expirada. Renove sua licença com o administrador para ligar o robô.",
            )

        selected_account = (
            db.query(DerivAccount)
            .filter(
                DerivAccount.user_id == current_user.id,
                DerivAccount.is_active_selected.is_(True),
            )
            .first()
        )
        if selected_account is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Conecte e selecione uma conta Deriv antes de ligar o robô.",
            )

        if float(cfg.stake_usd) > float(current_user.max_stake_usd):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stake configurado (${cfg.stake_usd:.2f}) excede o limite do seu plano (${current_user.max_stake_usd:.2f}).",
            )

        cfg.enabled = True
        cfg.status_text = "STARTING"
        action = "START"
        msg = f"Robô LIGADO em {cfg.symbol} ({selected_account.account_type} - {selected_account.loginid})."
    else:
        cfg.enabled = False
        cfg.status_text = "OFF"
        action = "STOP"
        msg = "Robô DESLIGADO pelo usuário."

    db.add(
        BotEventLog(
            user_id=current_user.id,
            level="INFO",
            symbol=cfg.symbol,
            message=msg,
        )
    )
    db.commit()
    db.refresh(cfg)

    publish_control_event({"action": action, "user_id": current_user.id})
    publish_user_event(
        current_user.id,
        {
            "type": "bot_status",
            "data": {"enabled": cfg.enabled, "status_text": cfg.status_text},
        },
    )

    return {
        "enabled": bool(cfg.enabled),
        "status_text": cfg.status_text,
        "config": serialize_bot_config(cfg),
    }


@router.post("/kill-switch")
def kill_switch(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = ensure_bot_config(db, current_user.id)
    cfg.enabled = False
    cfg.status_text = "KILL_SWITCH"

    event = BotEventLog(
        user_id=current_user.id,
        level="WARN",
        symbol=cfg.symbol,
        message="KILL SWITCH acionado! Comando de encerramento imediato enviado ao motor.",
    )
    db.add(event)
    db.commit()
    db.refresh(cfg)
    db.refresh(event)

    publish_control_event({"action": "KILL_SWITCH", "user_id": current_user.id})
    publish_user_event(
        current_user.id,
        {
            "type": "kill_switch",
            "data": {
                "enabled": False,
                "status_text": "KILL_SWITCH",
                "event": serialize_event_log(event),
            },
        },
    )

    return {
        "status": "KILL_SWITCH",
        "enabled": False,
        "status_text": cfg.status_text,
        "message": "Kill Switch acionado com sucesso. Todas as posições abertas serão encerradas imediatamente.",
    }


@router.post("/reset-session")
def reset_bot_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = ensure_bot_config(db, current_user.id)
    cfg.session_profit_usd = 0.0
    cfg.session_cycles_count = 0
    if cfg.status_text in ("META ATINGIDA 🎯", "STOP LOSS 🛑"):
        cfg.status_text = "OFF"
    db.commit()
    db.refresh(cfg)
    publish_control_event({"action": "RELOAD_CONFIG", "user_id": current_user.id})
    return {
        "status": "SUCCESS",
        "message": "Sessão zerada com sucesso! Você pode ligar o robô novamente para uma nova meta.",
        "config": serialize_bot_config(cfg),
    }


@router.get("/events")
def get_bot_events(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    events = (
        db.query(BotEventLog)
        .filter(BotEventLog.user_id == current_user.id)
        .order_by(BotEventLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "events": [serialize_event_log(e) for e in events],
        "count": len(events),
    }
