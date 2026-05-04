import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional, Tuple

_MAX_AGE_SEC = 900


def _secret() -> bytes:
    s = os.getenv("INTEGRATION_STATE_SECRET") or os.getenv("INTEGRATION_TOKEN_SECRET") or os.getenv("INTEGRATION_SECRET")
    if not s:
        return b"dev-only-change-me"
    return s.encode("utf-8")


def sign_oauth_state(company_id: int, provider: str) -> str:
    payload = {"c": company_id, "p": provider, "t": int(time.time())}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_secret(), raw, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(raw + b"|" + sig.encode("ascii")).decode("ascii")
    return token


def verify_oauth_state(token: str) -> Optional[Tuple[int, str]]:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii"))
        raw, sig = decoded.rsplit(b"|", 1)
        expect = hmac.new(_secret(), raw, hashlib.sha256).hexdigest().encode("ascii")
        if not hmac.compare_digest(expect, sig):
            return None
        payload = json.loads(raw.decode("utf-8"))
        if int(time.time()) - int(payload["t"]) > _MAX_AGE_SEC:
            return None
        return int(payload["c"]), str(payload["p"])
    except Exception:
        return None
