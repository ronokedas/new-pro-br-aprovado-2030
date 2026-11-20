import re
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, text
from sqlalchemy.orm import Session

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

from app.core.redis_client import ping_redis, publish_control_event, redis_get_json
from app.core.security import hash_password, require_admin
from app.services.subscription import (
    ensure_bot_config,
    ensure_utc,
    is_user_expired,
    serialize_event_log,
    serialize_user,
)
from shared.database import get_db
from shared.models import Basket, BotConfig, BotEventLog, DerivAccount, User, utcnow
from shared.redis_keys import KEY_WORKER_HEARTBEAT

router = APIRouter(prefix="/api/admin", tags=["Admin"])


class AdminCreateUserRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    role: Literal["ADMIN", "USER"] = "USER"
    status: Literal["ACTIVE", "SUSPENDED"] = "ACTIVE"
    expires_at: Optional[datetime] = None
    valid_days: Optional[int] = Field(default=30, ge=1, le=3650)
    max_stake_usd: float = Field(default=50.0, ge=1.0, le=10000.0)
    can_access_btc_radar: bool = True
    can_access_robo_deriv: bool = True
    allowed_modules: Optional[list[str]] = None
    must_change_password: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError("Endereço de e-mail inválido.")
        return cleaned


class AdminUpdateUserRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    status: Optional[Literal["ACTIVE", "SUSPENDED"]] = None
    expires_at: Optional[datetime] = None
    max_stake_usd: Optional[float] = Field(default=None, ge=1.0, le=10000.0)
    can_access_btc_radar: Optional[bool] = None
    can_access_robo_deriv: Optional[bool] = None
    allowed_modules: Optional[list[str]] = None
    role: Optional[Literal["ADMIN", "USER"]] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    must_change_password: Optional[bool] = None


class AdminExtendRequest(BaseModel):
    days: int = Field(..., ge=1, le=3650)


def _serialize_admin_user(db: Session, user: User) -> dict:
    base = serialize_user(user)

    accounts = db.query(DerivAccount).filter(DerivAccount.user_id == user.id).all()
    selected = next((a for a in accounts if a.is_active_selected), None)
    if selected is None and accounts:
        selected = accounts[0]

    bot_cfg = db.query(BotConfig).filter(BotConfig.user_id == user.id).first()
    open_baskets_count = (
        db.query(func.count(Basket.id))
        .filter(Basket.user_id == user.id, Basket.status == "OPEN")
        .scalar()
        or 0
    )

    base.update(
        {
            "deriv_connected": len(accounts) > 0,
            "deriv_accounts_count": len(accounts),
            "active_account_type": selected.account_type if selected else None,
            "active_loginid": selected.loginid if selected else None,
            "bot_enabled": bool(bot_cfg.enabled) if bot_cfg else False,
            "bot_status": bot_cfg.status_text if bot_cfg else "OFF",
            "bot_symbol": bot_cfg.symbol if bot_cfg else None,
            "open_baskets_count": int(open_baskets_count),
        }
    )
    return base


@router.get("/users")
def list_users(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(User).order_by(User.created_at.desc())
    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(User.name).like(term) | func.lower(User.email).like(term)
        )

    users = query.all()
    result = []
    for u in users:
        if status_filter:
            sf = status_filter.upper()
            if sf == "EXPIRED" and not is_user_expired(u):
                continue
            elif sf == "ACTIVE" and (u.status != "ACTIVE" or is_user_expired(u)):
                continue
            elif sf == "SUSPENDED" and u.status != "SUSPENDED":
                continue
        result.append(_serialize_admin_user(db, u))

    return {"users": result, "total": len(result)}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: AdminCreateUserRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email_norm = body.email.strip().lower()
    existing = db.query(User).filter(User.email == email_norm).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um usuário cadastrado com este e-mail.",
        )

    if body.expires_at is not None:
        expires_at = ensure_utc(body.expires_at)
    else:
        days = body.valid_days if body.valid_days is not None else 30
        expires_at = utcnow() + timedelta(days=days)

    import json
    allowed_list = body.allowed_modules or []
    if not allowed_list:
        allowed_list = []
        if body.can_access_btc_radar:
            allowed_list.append("radar_btc")
        if body.can_access_robo_deriv:
            allowed_list.append("robo_deriv")

    user = User(
        name=body.name.strip(),
        email=email_norm,
        password_hash=hash_password(body.password),
        role=body.role,
        status=body.status,
        expires_at=expires_at,
        max_stake_usd=float(body.max_stake_usd),
        can_access_btc_radar=body.can_access_btc_radar,
        can_access_robo_deriv=body.can_access_robo_deriv,
        allowed_modules_json=json.dumps(allowed_list),
        must_change_password=body.must_change_password,
    )
    db.add(user)
    db.flush()

    ensure_bot_config(db, user.id)
    db.refresh(user)

    return _serialize_admin_user(db, user)


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    body: AdminUpdateUserRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    if user.id == admin.id:
        if body.status == "SUSPENDED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Você não pode suspender sua própria conta.",
            )
        if body.role is not None and body.role != "ADMIN":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Você não pode remover seu próprio perfil de administrador.",
            )

    if body.name is not None:
        user.name = body.name.strip()
    if body.status is not None:
        user.status = body.status
    if body.expires_at is not None:
        user.expires_at = ensure_utc(body.expires_at)
    if body.max_stake_usd is not None:
        user.max_stake_usd = float(body.max_stake_usd)
    if body.role is not None:
        user.role = body.role
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.must_change_password is not None:
        user.must_change_password = body.must_change_password
    if body.can_access_btc_radar is not None:
        user.can_access_btc_radar = body.can_access_btc_radar
    if body.can_access_robo_deriv is not None:
        user.can_access_robo_deriv = body.can_access_robo_deriv
    if body.allowed_modules is not None:
        user.allowed_modules_json = json.dumps(body.allowed_modules)
    elif body.can_access_btc_radar is not None or body.can_access_robo_deriv is not None:
        updated_mods = []
        if user.can_access_btc_radar:
            updated_mods.append("radar_btc")
        if user.can_access_robo_deriv:
            updated_mods.append("robo_deriv")
        user.allowed_modules_json = json.dumps(updated_mods)

    db.commit()
    db.refresh(user)

    if body.status == "SUSPENDED":
        # Notify worker to stop opening new entries (existing open baskets are managed safely to exit)
        publish_control_event({"action": "RELOAD_CONFIG", "user_id": user.id})

    return _serialize_admin_user(db, user)


@router.post("/users/{user_id}/extend")
def extend_user_subscription(
    user_id: int,
    body: AdminExtendRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    now = utcnow()
    current_exp = ensure_utc(user.expires_at) or now
    base = max(now, current_exp)
    user.expires_at = base + timedelta(days=body.days)
    db.commit()
    db.refresh(user)

    publish_control_event({"action": "RELOAD_CONFIG", "user_id": user.id})
    return _serialize_admin_user(db, user)


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Você não pode excluir sua própria conta.",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    publish_control_event({"action": "STOP", "user_id": user.id})
    db.delete(user)
    db.commit()

    return {"detail": "Usuário excluído com sucesso.", "deleted_user_id": user_id}


@router.get("/health")
def admin_health(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    redis_ok = ping_redis()
    worker_hb = redis_get_json(KEY_WORKER_HEARTBEAT) if redis_ok else None

    worker_alive = False
    worker_last_seen = None
    active_sessions_reported = 0
    if isinstance(worker_hb, dict):
        ts = worker_hb.get("timestamp") or worker_hb.get("ts")
        active_sessions_reported = int(worker_hb.get("active_sessions", 0) or 0)
        if ts is not None:
            try:
                if isinstance(ts, (int, float)):
                    age = utcnow().timestamp() - float(ts)
                    worker_last_seen = datetime.fromtimestamp(float(ts), tz=utcnow().tzinfo).isoformat()
                else:
                    parsed = ensure_utc(datetime.fromisoformat(str(ts)))
                    age = (utcnow() - parsed).total_seconds() if parsed else 9999
                    worker_last_seen = parsed.isoformat() if parsed else None
                worker_alive = age <= 45.0
            except Exception:
                worker_alive = False

    total_users = int(db.query(func.count(User.id)).scalar() or 0)
    active_bots = int(db.query(func.count(BotConfig.id)).filter(BotConfig.enabled.is_(True)).scalar() or 0)
    open_baskets = int(db.query(func.count(Basket.id)).filter(Basket.status == "OPEN").scalar() or 0)

    error_logs = (
        db.query(BotEventLog)
        .filter(BotEventLog.level == "ERROR")
        .order_by(BotEventLog.created_at.desc())
        .limit(25)
        .all()
    )

    return {
        "postgres": {"ok": db_ok},
        "redis": {"ok": redis_ok},
        "worker": {
            "alive": worker_alive,
            "last_seen": worker_last_seen,
            "active_sessions": active_sessions_reported,
            "heartbeat": worker_hb,
        },
        "stats": {
            "total_users": total_users,
            "active_bots": active_bots,
            "open_baskets": open_baskets,
        },
        "recent_errors": [serialize_event_log(e) for e in error_logs],
    }
