"""Handy-Nachrichten über ntfy (https://ntfy.sh): Die ntfy-App (iOS, Android) zeigt Nachrichten an ein geheimes Thema sofort an.

Das Thema ist wie ein Passwort: Wer es kennt, kann mitlesen und Nachrichten schicken. Es wird zufällig und lang erzeugt
und liegt nur in data/notify.json (nur für root lesbar).
"""

import asyncio
import json
import os
import re
import secrets
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_SERVER = "https://ntfy.sh"
TOPIC = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EVENTS = {
    "ssh_neue_adresse": ("SSH-Anmeldung von einer neuen Adresse", True),
    "ssh_passwort": ("SSH-Anmeldung mit Passwort (sollte nie vorkommen)", True),
    "ssh_alle": ("Jede SSH-Anmeldung, auch von bekannten Adressen", False),
    "web_anmeldung": ("Anmeldung an diesem Server-Dashboard", True),
    "terminal": ("Terminal im Browser geöffnet", True),
    "web_gesperrt": ("Viele fehlgeschlagene Anmeldungen am Dashboard", True),
}


def new_topic() -> str:
    return "server-" + secrets.token_urlsafe(18)


def default_settings() -> dict:
    return {
        "aktiv": False,
        "server": DEFAULT_SERVER,
        "topic": "",
        "ereignisse": {key: default for key, (_, default) in EVENTS.items()},
    }


def validate_server(server: str) -> str:
    server = server.strip().rstrip("/")
    parts = urlsplit(server)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query:
        raise ValueError("Der Server muss eine Adresse wie https://ntfy.sh sein.")
    return server


class NotifySettings:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        settings = default_settings()
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise ValueError(f"{self.path} kann nicht gelesen werden: {error}") from error
            settings.update({k: stored[k] for k in ("aktiv", "server", "topic") if k in stored})
            settings["ereignisse"].update({k: bool(v) for k, v in stored.get("ereignisse", {}).items() if k in EVENTS})
        return settings

    def save(self, aktiv: bool, server: str, topic: str, ereignisse: dict) -> dict:
        server = validate_server(server)
        if topic and not TOPIC.match(topic):
            raise ValueError("Das Thema darf nur Buchstaben, Ziffern, - und _ enthalten (8 bis 64 Zeichen).")
        if aktiv and not topic:
            raise ValueError("Zum Aktivieren wird ein Thema gebraucht. Bitte zuerst eines erzeugen.")
        settings = {
            "aktiv": bool(aktiv),
            "server": server,
            "topic": topic,
            "ereignisse": {k: bool(ereignisse.get(k, EVENTS[k][1])) for k in EVENTS},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
            handle.write("\n")
        return settings


def publish(
    server: str, topic: str, title: str, message: str, priority: int = 3, tags: tuple[str, ...] = (), click: str | None = None
) -> None:
    """Sendet eine Nachricht. Wirft OSError bei Netzfehlern."""
    body = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": list(tags)}
    if click:
        body["click"] = click
    request = urllib.request.Request(
        server, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


class Notifier:
    """Schickt Nachrichten, wenn sie aktiv und für das Ereignis eingeschaltet sind. Fehler gehen ins Protokoll, nie nach oben."""

    def __init__(self, settings: NotifySettings, audit=None, send=publish, click_url: str | None = None):
        self.settings = settings
        self.audit = audit
        self.send = send
        self.click_url = click_url

    def enabled(self, event: str) -> bool:
        settings = self.settings.read()
        return bool(settings["aktiv"] and settings["topic"] and settings["ereignisse"].get(event))

    async def notify(self, event: str, title: str, message: str, priority: int = 3, tags: tuple[str, ...] = ()) -> bool:
        if not self.enabled(event):
            return False
        return await self._deliver(title, message, priority, tags)

    async def test(self) -> None:
        """Testnachricht, auch wenn Ereignisse ausgeschaltet sind. Fehler werden weitergegeben."""
        settings = self.settings.read()
        if not settings["topic"]:
            raise ValueError("Es ist noch kein Thema festgelegt.")
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self.send(
                settings["server"],
                settings["topic"],
                "Test vom Server",
                "Wenn Sie das lesen, funktionieren die Nachrichten.",
                3,
                ("white_check_mark",),
                self.click_url,
            ),
        )

    async def _deliver(self, title: str, message: str, priority: int, tags: tuple[str, ...]) -> bool:
        settings = self.settings.read()
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self.send(settings["server"], settings["topic"], title, message, priority, tags, self.click_url)
            )
        except OSError as error:
            if self.audit:
                self.audit.add("benachrichtigung_fehlgeschlagen", "", str(error))
            return False
        return True
