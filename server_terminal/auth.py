"""Zugang: ein Benutzer mit Passwort (scrypt) und Zwei-Faktor-Code (TOTP).

Das Konto wird beim ersten Besuch angelegt. Dafür braucht es den Einrichtungscode, den der Dienst beim Start ausgibt,
sonst könnte im Internet der erste Besucher den Server übernehmen. Zurücksetzen: account.json löschen und den Dienst
neu starten.

Sitzungen sind signierte Cookies ohne Zustand auf dem Server. Für gefährliche Aktionen (Terminal, Updates, Docker,
Neustart) gilt zusätzlich eine kurze „erhöhte“ Stufe, die ein frischer Zwei-Faktor-Code freischaltet.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from server_terminal import totp

SESSION_SECONDS = 8 * 3600
ELEVATED_SECONDS = 15 * 60
MIN_PASSWORD_LENGTH = 12
MAX_NAME_LENGTH = 64
SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}
MAX_FAILURES = 5
LOCKOUT_SECONDS = 600


class AccountStore:
    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def _read(self) -> dict:
        try:
            account = json.loads(self.path.read_text(encoding="utf-8"))
            if not {"benutzer", "salt", "hash", "totp", "schluessel"} <= account.keys():
                raise ValueError("Felder fehlen")
            return account
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"Zugangsdatei {self.path} ist beschädigt: {error}") from error

    def _write(self, account: dict, create: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if create else os.O_TRUNC)
        descriptor = os.open(self.path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(account, handle, indent=2)
            handle.write("\n")

    @staticmethod
    def check_new(user: str, password: str, repeated: str) -> str | None:
        if not user.strip() or len(user) > MAX_NAME_LENGTH:
            return f"Der Benutzername muss 1 bis {MAX_NAME_LENGTH} Zeichen lang sein."
        if len(password) < MIN_PASSWORD_LENGTH:
            return f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein."
        if password != repeated:
            return "Die beiden Passwörter sind nicht gleich."
        return None

    def create(self, user: str, password: str, totp_secret: str) -> None:
        salt = secrets.token_bytes(16)
        self._write(
            {
                "benutzer": user.strip(),
                "salt": salt.hex(),
                "hash": _hash(password, salt).hex(),
                "totp": totp_secret,
                "letzter_totp_schritt": 0,
                "schluessel": secrets.token_hex(32),
            },
            create=True,
        )

    def verify_password(self, user: str, password: str) -> bool:
        if not self.exists():
            return False
        account = self._read()
        # Beides immer prüfen, damit die Antwortzeit nichts verrät
        password_ok = hmac.compare_digest(_hash(password, bytes.fromhex(account["salt"])), bytes.fromhex(account["hash"]))
        user_ok = hmac.compare_digest(user.strip().encode(), account["benutzer"].encode())
        return password_ok and user_ok

    def verify_totp(self, code: str, now: float | None = None) -> bool:
        """Prüft den Code und merkt sich den Schritt, damit er nicht noch einmal gilt."""
        account = self._read()
        step = totp.verify(account["totp"], code, now if now is not None else time.time(), account["letzter_totp_schritt"])
        if step is None:
            return False
        account["letzter_totp_schritt"] = step
        self._write(account)
        return True

    @property
    def user(self) -> str:
        return self._read()["benutzer"]

    @property
    def totp_secret(self) -> str:
        return self._read()["totp"]

    # --- Sitzungen

    def new_session(self, now: float, elevated: bool = False) -> str:
        return self._sign({"exp": int(now + SESSION_SECONDS), "elev": int(now + ELEVATED_SECONDS) if elevated else 0})

    def read_session(self, token: str, now: float) -> dict | None:
        """`{"exp": ..., "elev": ...}` bei gültigem Cookie, sonst None."""
        if not self.exists():
            return None
        body, _, signature = token.partition(".")
        try:
            expected = self._signature(body)
            if not hmac.compare_digest(signature, expected):
                return None
            data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, json.JSONDecodeError):
            return None
        return data if data.get("exp", 0) > now else None

    def _signature(self, body: str) -> str:
        key = bytes.fromhex(self._read()["schluessel"])
        return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()

    def _sign(self, data: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
        return f"{body}.{self._signature(body)}"


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)


def new_setup_code() -> str:
    return secrets.token_hex(5)


class Lockout:
    """Sperrt Anmeldeversuche nach zu vielen Fehlern, je Adresse und insgesamt."""

    def __init__(self, max_failures: int = MAX_FAILURES, seconds: int = LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.seconds = seconds
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.seconds]
        self._failures[key] = recent
        return recent

    def blocked(self, ip: str, now: float) -> bool:
        return len(self._recent(ip, now)) >= self.max_failures or len(self._recent("*", now)) >= self.max_failures * 4

    def fail(self, ip: str, now: float) -> None:
        self._recent(ip, now).append(now)
        self._recent("*", now).append(now)

    def succeed(self, ip: str) -> None:
        self._failures.pop(ip, None)
