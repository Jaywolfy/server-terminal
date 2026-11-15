"""Protokoll aller wichtigen Aktionen: Anmeldungen, Freischaltungen, Terminal, Updates, Docker."""

import sqlite3
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("CREATE TABLE IF NOT EXISTS audit (ts REAL, ereignis TEXT, ip TEXT, detail TEXT)")
        self.connection.commit()
        self.clock = clock

    def add(self, event: str, ip: str = "", detail: str = "") -> None:
        self.connection.execute("INSERT INTO audit VALUES (?, ?, ?, ?)", (self.clock(), event, ip, detail[:500]))
        self.connection.commit()

    def recent(self, limit: int = 100) -> list[dict]:
        rows = self.connection.execute(
            "SELECT ts, ereignis, ip, detail FROM audit ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)
        )
        return [{"zeit": ts, "ereignis": event, "ip": ip, "detail": detail} for ts, event, ip, detail in rows]

    def close(self) -> None:
        self.connection.close()
