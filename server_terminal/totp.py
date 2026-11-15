"""Einmalcodes nach RFC 6238 (TOTP), kompatibel mit Google Authenticator, Aegis, 2FAS und ähnlichen Apps."""

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # Toleranz: ein Schritt davor und danach (Uhrenabweichung)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def current_step(now: float) -> int:
    return int(now // STEP_SECONDS)


def verify(secret: str, code: str, now: float, last_used_step: int = 0) -> int | None:
    """Gibt den Zeitschritt des Codes zurück, wenn er stimmt und nicht schon benutzt wurde, sonst None.

    Ein Code gilt nur einmal: Schritte bis einschließlich `last_used_step` werden abgelehnt.
    """
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step_now = current_step(now)
    for step in range(step_now - WINDOW, step_now + WINDOW + 1):
        if step > last_used_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
