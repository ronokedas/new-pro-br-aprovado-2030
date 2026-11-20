import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.redis_client import get_sync_redis
from app.services.subscription import is_user_expired
from shared.config import get_settings
from shared.database import get_db
from shared.models import User

_password_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)

bearer_scheme = HTTPBearer(auto_error=False)


def extract_client_ip(request: Optional[Request]) -> str:
    if request is None:
        return "127.0.0.1"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not plain_password or not password_hash:
        return False
    try:
        return _password_hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def create_access_token(
    user_id: int,
    role: str,
    session_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if session_id:
        payload["sid"] = session_id
    if client_ip:
        payload["ip"] = client_ip
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    user_id: int,
    role: str,
    session_id: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if session_id:
        payload["sid"] = session_id
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, expected_type: Optional[str] = "access") -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado. Faça login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if expected_type is not None and payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tipo de token inválido.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token malformado.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


class LoginRateLimiter:
    """Brute-force protection for login using Redis with graceful in-memory fallback."""

    def __init__(self, max_attempts: int = 10, window_seconds: int = 900):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._memory_store: dict[str, list[float]] = defaultdict(list)

    def _redis_key(self, identifier: str) -> str:
        return f"auth:ratelimit:{identifier}"

    def _prune_memory(self, identifier: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        attempts = [ts for ts in self._memory_store[identifier] if ts > cutoff]
        self._memory_store[identifier] = attempts
        return attempts

    def check_allowed(self, identifier: str) -> None:
        client = get_sync_redis()
        if client is not None:
            try:
                count = int(client.get(self._redis_key(identifier)) or 0)
                if count >= self.max_attempts:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Muitas tentativas de login. Aguarde alguns minutos antes de tentar novamente.",
                    )
                return
            except HTTPException:
                raise
            except Exception:
                pass

        attempts = self._prune_memory(identifier, time.monotonic())
        if len(attempts) >= self.max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Muitas tentativas de login. Aguarde alguns minutos antes de tentar novamente.",
            )

    def record_failure(self, identifier: str) -> None:
        client = get_sync_redis()
        if client is not None:
            try:
                key = self._redis_key(identifier)
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, self.window_seconds)
                pipe.execute()
                return
            except Exception:
                pass

        now = time.monotonic()
        self._prune_memory(identifier, now)
        self._memory_store[identifier].append(now)

    def reset(self, identifier: str) -> None:
        client = get_sync_redis()
        if client is not None:
            try:
                client.delete(self._redis_key(identifier))
            except Exception:
                pass
        self._memory_store.pop(identifier, None)

    def clear_memory(self) -> None:
        self._memory_store.clear()


login_rate_limiter = LoginRateLimiter(max_attempts=10, window_seconds=900)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials, expected_type="access")
    try:
        user_id = int(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado.",
        )

    if user.status == "SUSPENDED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta suspensa pelo administrador.",
        )

    # Multi-device & IP concurrent session validation
    token_sid = payload.get("sid")
    token_ip = payload.get("ip")
    db_sid = getattr(user, "current_session_id", None)
    db_ip = getattr(user, "current_session_ip", None)

    if db_sid and token_sid and token_sid != db_sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão encerrada: sua conta foi conectada em outro dispositivo.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    client_ip = extract_client_ip(request)
    if (
        db_ip
        and token_ip
        and client_ip not in ("127.0.0.1", "localhost", "testclient")
        and token_ip not in ("127.0.0.1", "localhost", "testclient")
        and client_ip != token_ip
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acesso bloqueado: endereço IP divergente da sessão ativa.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_module(module_name: str):
    """Dependency factory checking if current user has permission to access a specific module."""
    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role == "ADMIN":
            return current_user
        if module_name == "radar_btc":
            if not bool(getattr(current_user, "can_access_btc_radar", True)):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Acesso negado: você não tem permissão para o módulo Radar BTC M1.",
                )
        elif module_name == "robo_deriv":
            if not bool(getattr(current_user, "can_access_robo_deriv", True)):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Acesso negado: você não tem permissão para o módulo Robô Deriv.",
                )
        return current_user

    return _dependency


def get_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Require an ACTIVE and non-expired user."""
    if current_user.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta inativa ou suspensa.",
        )
    if is_user_expired(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Assinatura expirada. Renove sua licença para continuar operando.",
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require ADMIN role."""
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores.",
        )
    return current_user
