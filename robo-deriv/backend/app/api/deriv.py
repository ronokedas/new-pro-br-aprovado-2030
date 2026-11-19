from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.redis_client import publish_control_event
from app.core.security import get_current_user
from app.services.deriv_service import (
    DerivAPIError,
    DerivScopeError,
    fetch_account_balance,
    verify_deriv_token,
)
from app.services.subscription import ensure_bot_config, serialize_deriv_account
from shared.config import get_settings
from shared.crypto_vault import decrypt_secret, encrypt_secret
from shared.database import get_db
from shared.models import Basket, BotEventLog, DerivAccount, User, utcnow

router = APIRouter(prefix="/api/deriv", tags=["Deriv"])


class OAuthAccountInput(BaseModel):
    account: str = Field(..., min_length=2, max_length=64)
    token: str = Field(..., min_length=8, max_length=512)
    currency: str = Field(default="USD", max_length=16)


class ConnectOAuthRequest(BaseModel):
    accounts: list[OAuthAccountInput] = Field(..., min_length=1)


class ConnectTokenRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=512)


class SelectAccountRequest(BaseModel):
    account_id: int
    confirm_real_risk: bool = False


class SyncBalanceRequest(BaseModel):
    account_id: Optional[int] = None


def _upsert_verified_account(
    db: Session,
    user_id: int,
    raw_token: str,
    verified: dict,
) -> DerivAccount:
    encrypted = encrypt_secret(raw_token.strip())
    loginid = verified["loginid"]
    account = (
        db.query(DerivAccount)
        .filter(DerivAccount.user_id == user_id, DerivAccount.loginid == loginid)
        .first()
    )
    now = utcnow()
    if account is None:
        account = DerivAccount(
            user_id=user_id,
            loginid=loginid,
            account_type=verified["account_type"],
            currency=verified["currency"],
            token_encrypted=encrypted,
            balance=float(verified["balance"]),
            is_active_selected=False,
            last_synced_at=now,
        )
        db.add(account)
        db.flush()
    else:
        account.account_type = verified["account_type"]
        account.currency = verified["currency"]
        account.token_encrypted = encrypted
        account.balance = float(verified["balance"])
        account.last_synced_at = now
    return account


def _ensure_default_selected_account(db: Session, user_id: int) -> None:
    accounts = db.query(DerivAccount).filter(DerivAccount.user_id == user_id).all()
    if not accounts:
        return
    if any(a.is_active_selected for a in accounts):
        return
    # Prefer DEMO by default for financial safety
    demo = next((a for a in accounts if a.account_type == "DEMO"), None)
    target = demo if demo is not None else accounts[0]
    target.is_active_selected = True


@router.get("/oauth-url")
def get_oauth_url(_user: User = Depends(get_current_user)):
    settings = get_settings()
    app_id = settings.DERIV_APP_ID.strip()
    is_custom = bool(app_id and app_id != "1089")
    return {
        "oauth_url": f"https://oauth.deriv.com/oauth2/authorize?app_id={app_id}",
        "app_id": app_id,
        "configured": is_custom,
    }


@router.post("/connect-oauth")
async def connect_oauth(
    body: ConnectOAuthRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    connected_count = 0
    errors: list[str] = []

    for item in body.accounts:
        try:
            verified = await verify_deriv_token(item.token)
        except DerivScopeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except DerivAPIError as exc:
            errors.append(f"{item.account}: {exc}")
            continue

        _upsert_verified_account(db, current_user.id, item.token, verified)
        connected_count += 1

    if connected_count == 0:
        detail = errors[0] if errors else "Nenhuma conta Deriv pôde ser validada."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    _ensure_default_selected_account(db, current_user.id)
    db.add(
        BotEventLog(
            user_id=current_user.id,
            level="INFO",
            message=f"{connected_count} conta(s) Deriv conectada(s) via OAuth.",
        )
    )
    db.commit()

    publish_control_event({"action": "RELOAD_CONFIG", "user_id": current_user.id})

    all_accounts = (
        db.query(DerivAccount)
        .filter(DerivAccount.user_id == current_user.id)
        .order_by(DerivAccount.account_type.asc(), DerivAccount.id.asc())
        .all()
    )
    return {
        "connected": connected_count,
        "warnings": errors,
        "accounts": [serialize_deriv_account(a) for a in all_accounts],
    }


@router.post("/connect-token")
async def connect_token(
    body: ConnectTokenRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        verified = await verify_deriv_token(body.token)
    except DerivScopeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except DerivAPIError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    acc = _upsert_verified_account(db, current_user.id, body.token, verified)
    _ensure_default_selected_account(db, current_user.id)

    db.add(
        BotEventLog(
            user_id=current_user.id,
            level="INFO",
            message=f"Conta Deriv {acc.loginid} ({acc.account_type}) conectada via API Token.",
        )
    )
    db.commit()
    db.refresh(acc)

    publish_control_event({"action": "RELOAD_CONFIG", "user_id": current_user.id})

    all_accounts = (
        db.query(DerivAccount)
        .filter(DerivAccount.user_id == current_user.id)
        .order_by(DerivAccount.account_type.asc(), DerivAccount.id.asc())
        .all()
    )
    return {
        "account": serialize_deriv_account(acc),
        "accounts": [serialize_deriv_account(a) for a in all_accounts],
    }


@router.get("/accounts")
def list_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accounts = (
        db.query(DerivAccount)
        .filter(DerivAccount.user_id == current_user.id)
        .order_by(DerivAccount.account_type.asc(), DerivAccount.id.asc())
        .all()
    )
    return {"accounts": [serialize_deriv_account(a) for a in accounts]}


@router.post("/select-account")
def select_account(
    body: SelectAccountRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = (
        db.query(DerivAccount)
        .filter(
            DerivAccount.id == body.account_id,
            DerivAccount.user_id == current_user.id,
        )
        .first()
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta Deriv não encontrada.")

    if target.account_type == "REAL" and not body.confirm_real_risk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alternar para conta REAL exige confirmação explícita de risco financeiro (confirm_real_risk=true).",
        )

    current_selected = (
        db.query(DerivAccount)
        .filter(
            DerivAccount.user_id == current_user.id,
            DerivAccount.is_active_selected.is_(True),
        )
        .first()
    )

    if current_selected is not None and current_selected.id != target.id:
        open_basket = (
            db.query(Basket)
            .filter(
                Basket.user_id == current_user.id,
                Basket.status == "OPEN",
                Basket.deriv_account_id == current_selected.id,
            )
            .first()
        )
        if open_basket is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Não é possível alternar de conta enquanto houver um cesto aberto na conta atual.",
            )

    db.query(DerivAccount).filter(DerivAccount.user_id == current_user.id).update(
        {"is_active_selected": False}
    )
    target.is_active_selected = True
    db.add(
        BotEventLog(
            user_id=current_user.id,
            level="WARN" if target.account_type == "REAL" else "INFO",
            message=f"Conta ativa alterada para {target.loginid} ({target.account_type}).",
        )
    )
    db.commit()
    db.refresh(target)

    publish_control_event({"action": "RELOAD_CONFIG", "user_id": current_user.id})

    accounts = (
        db.query(DerivAccount)
        .filter(DerivAccount.user_id == current_user.id)
        .order_by(DerivAccount.account_type.asc(), DerivAccount.id.asc())
        .all()
    )
    return {
        "selected_account": serialize_deriv_account(target),
        "accounts": [serialize_deriv_account(a) for a in accounts],
    }


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = (
        db.query(DerivAccount)
        .filter(DerivAccount.id == account_id, DerivAccount.user_id == current_user.id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta Deriv não encontrada.")

    open_basket = (
        db.query(Basket)
        .filter(
            Basket.user_id == current_user.id,
            Basket.status == "OPEN",
            Basket.deriv_account_id == target.id,
        )
        .first()
    )
    if open_basket is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não é possível remover uma conta com cesto aberto. Encerre as posições primeiro.",
        )

    was_selected = bool(target.is_active_selected)
    loginid = target.loginid
    db.delete(target)
    db.flush()

    if was_selected:
        # Disable bot for safety whenever the active trading account is removed
        cfg = ensure_bot_config(db, current_user.id)
        cfg.enabled = False
        cfg.status_text = "OFF"
        _ensure_default_selected_account(db, current_user.id)

    db.add(
        BotEventLog(
            user_id=current_user.id,
            level="INFO",
            message=f"Conta Deriv {loginid} removida pelo usuário.",
        )
    )
    db.commit()

    publish_control_event({"action": "RELOAD_CONFIG", "user_id": current_user.id})

    accounts = (
        db.query(DerivAccount)
        .filter(DerivAccount.user_id == current_user.id)
        .order_by(DerivAccount.account_type.asc(), DerivAccount.id.asc())
        .all()
    )
    return {
        "detail": "Conta Deriv removida com sucesso.",
        "accounts": [serialize_deriv_account(a) for a in accounts],
    }


@router.post("/sync-balance")
async def sync_balance(
    body: Optional[SyncBalanceRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(DerivAccount).filter(DerivAccount.user_id == current_user.id)
    if body and body.account_id is not None:
        query = query.filter(DerivAccount.id == body.account_id)

    accounts = query.all()
    if not accounts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma conta Deriv conectada para sincronizar.",
        )

    errors: list[str] = []
    synced = 0
    for acc in accounts:
        try:
            raw_token = decrypt_secret(acc.token_encrypted)
            live = await fetch_account_balance(raw_token)
            acc.balance = float(live["balance"])
            acc.currency = live["currency"]
            acc.last_synced_at = utcnow()
            synced += 1
        except Exception as exc:
            errors.append(f"{acc.loginid}: {exc}")

    db.commit()

    all_accounts = (
        db.query(DerivAccount)
        .filter(DerivAccount.user_id == current_user.id)
        .order_by(DerivAccount.account_type.asc(), DerivAccount.id.asc())
        .all()
    )
    return {
        "synced": synced,
        "errors": errors,
        "accounts": [serialize_deriv_account(a) for a in all_accounts],
    }
