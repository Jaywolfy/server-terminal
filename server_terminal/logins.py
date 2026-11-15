"""Anmeldeversuche per SSH: gescheiterte und erfolgreiche, aus dem Journal des SSH-Dienstes.

Auch die Herkunft wird angezeigt (Land und Stadt), siehe geo.py. Gezählt werden nur Versuche mit Passwort oder
unbekanntem Benutzer, also das, was Angreifer tun. Verbindungen, die ohne Anmeldeversuch abbrechen, zählen nicht.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime

from server_terminal.system import run

TIMESTAMP = r"(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d)"
FAILED = re.compile(TIMESTAMP + r" \S+ sshd\S*: Failed (?P<method>\w+) for (?:invalid user )?(?P<user>\S+) from (?P<ip>\S+) port")
INVALID = re.compile(TIMESTAMP + r" \S+ sshd\S*: Invalid user (?P<user>\S*) from (?P<ip>\S+) port")
ACCEPTED = re.compile(TIMESTAMP + r" \S+ sshd\S*: Accepted (?P<method>\S+) for (?P<user>\S+) from (?P<ip>\S+) port")


def parse_events(text: str) -> list[dict]:
    """Ereignisse aus der Ausgabe von `journalctl -u ssh -o short-iso`. Je Versuch ein Ereignis.

    „Failed password for invalid user X“ und „Invalid user X“ gehören zum selben Versuch. Gezählt wird nur die erste Zeile.
    """
    events = []
    for line in text.splitlines():
        if (match := ACCEPTED.search(line)) is not None:
            events.append({**match.groupdict(), "art": "erfolgreich"})
        elif (match := FAILED.search(line)) is not None:
            events.append({**match.groupdict(), "art": "fehlgeschlagen"})
        elif (match := INVALID.search(line)) is not None:
            events.append({**match.groupdict(), "method": "unbekannter Benutzer", "art": "fehlgeschlagen"})
    for event in events:
        event["ts"] = datetime.fromisoformat(event["ts"]).isoformat()
    return events


def summarize(events: list[dict], geo: dict[str, dict], recent_limit: int = 40, top_limit: int = 15) -> dict:
    failed = [e for e in events if e["art"] == "fehlgeschlagen"]
    succeeded = [e for e in events if e["art"] == "erfolgreich"]
    by_ip: dict[str, list[dict]] = defaultdict(list)
    for event in failed:
        by_ip[event["ip"]].append(event)
    top = [
        {
            "ip": ip,
            "anzahl": len(items),
            "zuletzt": max(e["ts"] for e in items),
            "zuerst": min(e["ts"] for e in items),
            "benutzer": [name for name, _ in Counter(e["user"] for e in items).most_common(5)],
            **_origin(geo.get(ip)),
        }
        for ip, items in by_ip.items()
    ]
    top.sort(key=lambda item: item["anzahl"], reverse=True)
    countries = Counter(_origin(geo.get(e["ip"]))["land"] or "Unbekannt" for e in failed)
    return {
        "fehlgeschlagen": len(failed),
        "erfolgreich": len(succeeded),
        "ips": len(by_ip),
        "laender": [{"land": land, "anzahl": count} for land, count in countries.most_common(6)],
        "haeufigste": top[:top_limit],
        "zuletzt": [_with_origin(e, geo) for e in sorted(failed, key=lambda e: e["ts"], reverse=True)[:recent_limit]],
        "erfolgreiche": [_with_origin(e, geo) for e in sorted(succeeded, key=lambda e: e["ts"], reverse=True)[:20]],
    }


def _origin(info: dict | None) -> dict:
    info = info or {}
    return {
        "land": info.get("land", ""),
        "flagge": info.get("flagge", ""),
        "stadt": info.get("stadt", ""),
        "anbieter": info.get("anbieter", ""),
    }


def _with_origin(event: dict, geo: dict[str, dict]) -> dict:
    return {
        "ts": event["ts"],
        "benutzer": event["user"],
        "ip": event["ip"],
        "methode": event["method"],
        **_origin(geo.get(event["ip"])),
    }


async def read_journal(hours: int) -> str:
    code, output = await run(
        "journalctl", "-u", "ssh", "--since", f"{int(hours)} hours ago", "-o", "short-iso", "--no-pager", timeout=60
    )
    return output if code == 0 else ""


async def banned_ips() -> list[str]:
    """Von fail2ban aktuell gesperrte Adressen (Jail sshd)."""
    code, output = await run("fail2ban-client", "status", "sshd", timeout=10)
    if code != 0:
        return []
    for line in output.splitlines():
        if "Banned IP list" in line:
            return line.split(":", 1)[1].split()
    return []
