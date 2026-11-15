"""Beobachtet das SSH-Journal und meldet erfolgreiche Anmeldungen, vor allem von bisher unbekannten Adressen.

Beim ersten Start werden die Adressen der letzten 30 Tage als bekannt gespeichert, damit es keine Flut gibt.
Danach löst jede Anmeldung von einer neuen Adresse eine dringende Nachricht aus.
"""

import asyncio
import contextlib
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path

from server_terminal.logins import ACCEPTED, parse_events
from server_terminal.notify import Notifier

DEDUPE_SECONDS = 120
SEED_DAYS = 30


class KnownAddresses:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS adressen (ip TEXT PRIMARY KEY, zuerst REAL, zuletzt REAL, anzahl INTEGER)"
        )
        self.connection.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, wert TEXT)")
        self.connection.commit()

    def seeded(self) -> bool:
        return self.connection.execute("SELECT 1 FROM meta WHERE name = 'befuellt'").fetchone() is not None

    def mark_seeded(self) -> None:
        self.connection.execute("INSERT OR REPLACE INTO meta VALUES ('befuellt', '1')")
        self.connection.commit()

    def known(self, ip: str) -> bool:
        return self.connection.execute("SELECT 1 FROM adressen WHERE ip = ?", (ip,)).fetchone() is not None

    def touch(self, ip: str, when: float) -> None:
        self.connection.execute(
            "INSERT INTO adressen VALUES (?, ?, ?, 1) "
            "ON CONFLICT(ip) DO UPDATE SET zuletzt = excluded.zuletzt, anzahl = anzahl + 1",
            (ip, when, when),
        )
        self.connection.commit()

    def count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM adressen").fetchone()[0]

    def close(self) -> None:
        self.connection.close()


def parse_accepted(line: str) -> dict | None:
    match = ACCEPTED.search(line)
    if not match:
        return None
    event = match.groupdict()
    event["ts"] = datetime.fromisoformat(event["ts"]).isoformat()
    return event


Follower = Callable[[], AsyncIterator[str]]


async def journal_follower() -> AsyncIterator[str]:
    """Neue Zeilen des SSH-Dienstes, solange `journalctl -f` läuft."""
    process = await asyncio.create_subprocess_exec(
        "journalctl",
        "-u",
        "ssh",
        "-f",
        "-n",
        "0",
        "-o",
        "short-iso",
        "--no-pager",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert process.stdout is not None
        async for raw in process.stdout:
            yield raw.decode("utf-8", "replace").rstrip("\n")
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


class SshWatcher:
    def __init__(
        self,
        notifier: Notifier,
        known: KnownAddresses,
        lookup_origin: Callable[[str], dict],
        history: Callable[[], str],
        follow: Follower = journal_follower,
        clock=time.time,
    ):
        self.notifier = notifier
        self.known = known
        self.lookup_origin = lookup_origin  # ip -> {"land", "stadt", "flagge", "anbieter"} oder {}
        self.history = history  # Text des SSH-Journals der letzten Tage (für das erste Befüllen)
        self.follow = follow
        self.clock = clock
        self._recent: dict[tuple, float] = {}
        self._task: asyncio.Task | None = None

    async def seed(self) -> int:
        """Beim ersten Start alle Adressen erfolgreicher Anmeldungen der letzten Tage als bekannt eintragen."""
        if self.known.seeded():
            return 0
        text = await asyncio.get_running_loop().run_in_executor(None, self.history)
        ips = {event["ip"] for event in parse_events(text) if event["art"] == "erfolgreich"}
        for ip in ips:
            self.known.touch(ip, self.clock())
        self.known.mark_seeded()
        return len(ips)

    async def handle_line(self, line: str) -> bool:
        """Verarbeitet eine Journalzeile. True, wenn eine Nachricht gesendet wurde."""
        event = parse_accepted(line)
        if event is None:
            return False
        now = self.clock()
        key = (event["ip"], event["user"], event["method"])
        self._recent = {k: t for k, t in self._recent.items() if now - t < DEDUPE_SECONDS}
        duplicate = key in self._recent
        self._recent[key] = now
        is_new = not self.known.known(event["ip"])
        self.known.touch(event["ip"], now)
        if duplicate:
            return False
        return await self._announce(event, is_new)

    async def _announce(self, event: dict, is_new: bool) -> bool:
        origin = {}
        try:
            origin = await asyncio.get_running_loop().run_in_executor(None, self.lookup_origin, event["ip"])
        except OSError:
            pass
        place = ", ".join(part for part in (origin.get("stadt"), origin.get("land")) if part)
        where = f"{event['ip']}" + (
            f" ({origin.get('flagge', '')} {place}, {origin.get('anbieter', '')})".replace("  ", " ").replace(", )", ")")
            if place
            else ""
        )
        time_text = datetime.fromisoformat(event["ts"]).astimezone().strftime("%H:%M")
        how = "mit Passwort" if event["method"] == "password" else "per Schlüssel"
        message = f"{event['user']} hat sich {how} von {where} angemeldet ({time_text} Uhr)."
        if event["method"] == "password":
            return await self.notifier.notify("ssh_passwort", "SSH-Anmeldung mit Passwort", message, 5, ("rotating_light", "key"))
        if is_new and await self.notifier.notify(
            "ssh_neue_adresse", "Neue SSH-Anmeldung: unbekannte Adresse", message, 5, ("rotating_light", "warning")
        ):
            return True
        return await self.notifier.notify("ssh_alle", "SSH-Anmeldung", message, 3, ("key",))

    async def run(self) -> None:
        await self.seed()
        while True:
            try:
                async for line in self.follow():
                    await self.handle_line(line)
            except OSError:
                pass
            await asyncio.sleep(5)  # journalctl endet bei Rotation; neu verbinden

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.known.close()
