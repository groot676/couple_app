"""The door: one shared household passcode, remembered per browser with a
long-lived HMAC-signed cookie. stdlib only — no accounts, no user management."""
import hashlib
import hmac

from fastapi import Request

from shared.config import APP_PASSCODE, APP_SECRET

COOKIE_NAME = "together_door"
COOKIE_MAX_AGE = 180 * 24 * 3600  # 180 days


class NotAuthed(Exception):
    pass


def make_token() -> str:
    return hmac.new(APP_SECRET.encode(), b"door-open", hashlib.sha256).hexdigest()


def verify_token(token: str) -> bool:
    return bool(APP_SECRET) and hmac.compare_digest(token, make_token())


def check_passcode(candidate: str) -> bool:
    return bool(APP_PASSCODE) and hmac.compare_digest(candidate.strip(), APP_PASSCODE)


def require_auth(request: Request) -> None:
    # Local dev with no passcode configured: the door stays open.
    if not APP_PASSCODE:
        return
    if not verify_token(request.cookies.get(COOKIE_NAME, "")):
        raise NotAuthed()
