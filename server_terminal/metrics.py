"""Auslastung des Servers: CPU, Arbeitsspeicher, Last, Netzwerk, Festplatte und deren Verlauf.

Ein Hintergrund-Task liest alle 2 Sekunden /proc. Die letzten Minuten bleiben im Speicher (für die kurzen Ansichten),
alle 15 Sekunden wird ein Mittelwert in SQLite geschrieben (für 1 Stunde bis 7 Tage).
"""

import asyncio
import re
import shutil
import sqlite3
import time
from collections import deque
from pathlib import Path

SAMPLE_SECONDS = 2
STORE_SECONDS = 15
RETENTION_SECONDS = 8 * 24 * 3600
FIELDS = ["cpu", "iowait", "load1", "mem", "swap", "disk", "rx", "tx", "read", "write"]

# Ansicht -> (Zeitraum in Sekunden, Quelle, Größe eines Zeitfensters in Sekunden)
RANGES = {
    "1min": (60, "memory", 2),
    "5min": (300, "memory", 2),
    "1h": (3600, "store", 15),
    "12h": (12 * 3600, "store", 60),
    "1d": (24 * 3600, "store", 120),
    "7d": (7 * 24 * 3600, "store", 900),
}
VIRTUAL_INTERFACE = re.compile(r"^(lo|veth|docker|br-|virbr|tun|tap)")
WHOLE_DISK = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")


def parse_cpu(stat: str) -> tuple[int, int, int]:
    """(gesamt, untätig, auf I/O wartend) aus der ersten Zeile von /proc/stat, in Ticks."""
    values = [int(v) for v in stat.splitlines()[0].split()[1:]]
    values += [0] * (8 - len(values))
    idle, iowait = values[3], values[4]
    return sum(values[:8]), idle + iowait, iowait


def parse_meminfo(text: str) -> tuple[float, float]:
    """(Speicher belegt in %, Swap belegt in %)"""
    info = {line.split(":")[0]: int(line.split()[1]) for line in text.splitlines() if ":" in line}
    total, available = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    swap_total, swap_free = info.get("SwapTotal", 0), info.get("SwapFree", 0)
    mem = 100 * (total - available) / total if total else 0.0
    swap = 100 * (swap_total - swap_free) / swap_total if swap_total else 0.0
    return mem, swap


def parse_net(text: str) -> tuple[int, int]:
    """(empfangene, gesendete Bytes) aller echten Netzwerkkarten. Docker-Brücken und virtuelle Geräte zählen nicht."""
    rx = tx = 0
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        if VIRTUAL_INTERFACE.match(name.strip()):
            continue
        fields = rest.split()
        rx += int(fields[0])
        tx += int(fields[8])
    return rx, tx


def parse_diskstats(text: str) -> tuple[int, int]:
    """(gelesene, geschriebene Bytes) aller ganzen Festplatten (ohne Partitionen und Loop-Geräte)."""
    read = write = 0
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 10 and WHOLE_DISK.match(fields[2]):
            read += int(fields[5]) * 512
            write += int(fields[9]) * 512
    return read, write


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


class Sampler:
    """Berechnet aus zwei Messungen die Werte pro Sekunde."""

    def __init__(self, reader=read_text, disk_path: str = "/", clock=time.time):
        self.reader = reader
        self.disk_path = disk_path
        self.clock = clock
        self._last: dict | None = None

    def _snapshot(self) -> dict:
        total, idle, iowait = parse_cpu(self.reader("/proc/stat"))
        rx, tx = parse_net(self.reader("/proc/net/dev"))
        read, write = parse_diskstats(self.reader("/proc/diskstats"))
        return {"t": self.clock(), "cpu": (total, idle, iowait), "net": (rx, tx), "io": (read, write)}

    def sample(self) -> dict | None:
        """Ein Messwert oder None bei der allerersten Messung (es fehlt der Vergleich)."""
        now = self._snapshot()
        previous, self._last = self._last, now
        if previous is None:
            return None
        seconds = max(now["t"] - previous["t"], 0.001)
        total = now["cpu"][0] - previous["cpu"][0]
        busy = total - (now["cpu"][1] - previous["cpu"][1])
        wait = now["cpu"][2] - previous["cpu"][2]
        mem, swap = parse_meminfo(self.reader("/proc/meminfo"))
        usage = shutil.disk_usage(self.disk_path)
        return {
            "t": now["t"],
            "cpu": _percent(busy, total),
            "iowait": _percent(wait, total),
            "load1": float(self.reader("/proc/loadavg").split()[0]),
            "mem": mem,
            "swap": swap,
            "disk": 100 * usage.used / usage.total if usage.total else 0.0,
            "rx": max(now["net"][0] - previous["net"][0], 0) / seconds,
            "tx": max(now["net"][1] - previous["net"][1], 0) / seconds,
            "read": max(now["io"][0] - previous["io"][0], 0) / seconds,
            "write": max(now["io"][1] - previous["io"][1], 0) / seconds,
        }


def _percent(part: float, whole: float) -> float:
    return max(0.0, min(100.0, 100 * part / whole)) if whole > 0 else 0.0


class MetricsStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        columns = ", ".join(f"{name} REAL" for name in FIELDS)
        self.connection.execute(f"CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, {columns})")
        self.connection.commit()

    def add(self, ts: int, values: dict) -> None:
        placeholders = ", ".join("?" for _ in FIELDS)
        self.connection.execute(
            f"INSERT OR REPLACE INTO samples (ts, {', '.join(FIELDS)}) VALUES (?, {placeholders})",
            (ts, *[values[name] for name in FIELDS]),
        )
        self.connection.execute("DELETE FROM samples WHERE ts < ?", (ts - RETENTION_SECONDS,))
        self.connection.commit()

    def query(self, since: int, bucket: int) -> list[dict]:
        averages = ", ".join(f"AVG({name})" for name in FIELDS)
        rows = self.connection.execute(
            f"SELECT (ts / ?) * ? AS slot, {averages} FROM samples WHERE ts >= ? GROUP BY slot ORDER BY slot",
            (bucket, bucket, since),
        ).fetchall()
        return [{"t": row[0], **dict(zip(FIELDS, row[1:], strict=True))} for row in rows]

    def close(self) -> None:
        self.connection.close()


class MetricsService:
    def __init__(self, store: MetricsStore, sampler: Sampler | None = None, clock=time.time):
        self.store = store
        self.sampler = sampler or Sampler()
        self.clock = clock
        self.recent: deque = deque(maxlen=int(20 * 60 / SAMPLE_SECONDS))
        self._pending: list[dict] = []
        self._task: asyncio.Task | None = None

    def tick(self) -> dict | None:
        """Eine Messung. Alle `STORE_SECONDS` geht der Mittelwert in die Datenbank."""
        sample = self.sampler.sample()
        if sample is None:
            return None
        self.recent.append(sample)
        self._pending.append(sample)
        if sample["t"] - self._pending[0]["t"] >= STORE_SECONDS:
            averaged = {name: sum(s[name] for s in self._pending) / len(self._pending) for name in FIELDS}
            self.store.add(int(sample["t"]), averaged)
            self._pending = []
        return sample

    def series(self, view: str) -> dict:
        if view not in RANGES:
            raise ValueError(f"Unbekannte Ansicht: {view!r}")
        seconds, source, bucket = RANGES[view]
        since = self.clock() - seconds
        if source == "memory":
            points = [s for s in self.recent if s["t"] >= since]
        else:
            points = self.store.query(int(since), bucket)
        return {"ansicht": view, "von": int(since), "bis": int(self.clock()), "intervall": bucket, "punkte": points}

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.tick)
            except OSError:
                pass  # Eine fehlgeschlagene Messung darf den Dienst nicht beenden
            await asyncio.sleep(SAMPLE_SECONDS)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.store.close()
