import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet


def _fernet() -> Optional[Fernet]:
    secret = os.getenv("INTEGRATION_TOKEN_SECRET") or os.getenv("INTEGRATION_SECRET")
    if not secret:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(plain: str) -> str:
    f = _fernet()
    if not f:
        return plain
    return f.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_token(stored: str) -> str:
    f = _fernet()
    if not f:
        return stored
    try:
        return f.decrypt(stored.encode("utf-8")).decode("utf-8")
    except Exception:
        return stored
