"""Herkunft von IP-Adressen (Land, Stadt, Anbieter) über ip-api.com, mit dauerhaftem Zwischenspeicher.

Es werden nur öffentliche IP-Adressen abgefragt, jede höchstens einmal. Das sind Adressen von Angreifern oder die eigene.
Mit `enabled=False` bleibt alles lokal und es gibt keine Herkunft.
"""

import ipaddress
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

API = "http://ip-api.com/batch?fields=status,query,country,countryCode,regionName,city,isp"
BATCH = 100
MAX_AGE = 30 * 24 * 3600


def is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def flag(country_code: str) -> str:
    if len(country_code) != 2 or not country_code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


def fetch_batch(ips: list[str], timeout: float = 15) -> list[dict]:
    body = json.dumps(ips).encode()
    request = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class GeoCache:
    def __init__(self, path: Path, enabled: bool = True, fetch=fetch_batch, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS geo "
            "(ip TEXT PRIMARY KEY, land TEXT, code TEXT, region TEXT, stadt TEXT, anbieter TEXT, abgerufen REAL)"
        )
        self.connection.commit()
        self.enabled = enabled
        self.fetch = fetch
        self.clock = clock

    def lookup(self, ips: list[str]) -> dict[str, dict]:
        """Herkunft für alle bekannten Adressen. Unbekannte öffentliche werden (gesammelt) nachgefragt."""
        unique = sorted(set(ips))
        known = self._cached(unique)
        if self.enabled:
            missing = [ip for ip in unique if ip not in known and is_public(ip)]
            for start in range(0, len(missing), BATCH):
                self._store(self.fetch(missing[start : start + BATCH]))
            known = self._cached(unique)
        return known

    def _cached(self, ips: list[str]) -> dict[str, dict]:
        result = {}
        oldest = self.clock() - MAX_AGE
        for ip in ips:
            row = self.connection.execute(
                "SELECT land, code, region, stadt, anbieter FROM geo WHERE ip = ? AND abgerufen > ?", (ip, oldest)
            ).fetchone()
            if row:
                result[ip] = {
                    "land": row[0],
                    "code": row[1],
                    "flagge": flag(row[1]),
                    "region": row[2],
                    "stadt": row[3],
                    "anbieter": row[4],
                }
        return result

    def _store(self, answers: list[dict]) -> None:
        now = self.clock()
        for answer in answers:
            ip = answer.get("query")
            if not ip:
                continue
            ok = answer.get("status") == "success"
            self.connection.execute(
                "INSERT OR REPLACE INTO geo VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ip,
                    answer.get("country", "") if ok else "",
                    answer.get("countryCode", "") if ok else "",
                    answer.get("regionName", "") if ok else "",
                    answer.get("city", "") if ok else "",
                    answer.get("isp", "") if ok else "",
                    now,
                ),
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
