import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from shared.config import get_settings


def _get_fernet() -> Fernet:
    raw_key = get_settings().ENCRYPTION_KEY.strip()
    if not raw_key:
        # Derive deterministic valid Fernet key from JWT_SECRET only if ENCRYPTION_KEY is empty (dev fallback)
        digest = hashlib.sha256(get_settings().JWT_SECRET.encode("utf-8")).digest()
        key_bytes = base64.urlsafe_b64encode(digest)
    else:
        try:
            # Validate if already a valid Fernet key
            Fernet(raw_key.encode("utf-8"))
            key_bytes = raw_key.encode("utf-8")
        except Exception:
            digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
            key_bytes = base64.urlsafe_b64encode(digest)
    return Fernet(key_bytes)


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt stored credential (invalid ENCRYPTION_KEY)") from exc
