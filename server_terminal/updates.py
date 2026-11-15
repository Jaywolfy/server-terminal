"""Paketupdates (apt): verfügbare Updates, Verlauf und der Auftrag „jetzt aktualisieren“."""

import gzip
import re
from pathlib import Path

from server_terminal.jobs import JobRunner
from server_terminal.system import run

HISTORY = Path("/var/log/apt/history.log")
UPDATE_ENV = {"DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "a", "NEEDRESTART_SUSPEND": "1"}
UPDATE_STEPS = [
    ["apt-get", "update"],
    ["apt-get", "-y", "-o", "Dpkg::Options::=--force-confold", "dist-upgrade"],
]
UPGRADABLE = re.compile(r"^(?P<name>[^/\s]+)/(?P<repo>\S+) (?P<new>\S+) \S+(?: \[upgradable from: (?P<old>[^\]]+)\])?")


def parse_upgradable(text: str) -> list[dict]:
    """Ausgabe von `apt list --upgradable`."""
    packages = []
    for line in text.splitlines():
        match = UPGRADABLE.match(line)
        if match:
            packages.append(
                {
                    "name": match["name"],
                    "neu": match["new"],
                    "alt": match["old"] or "",
                    "quelle": match["repo"],
                    "sicherheit": "-security" in match["repo"] or "esm" in match["repo"],
                }
            )
    return sorted(packages, key=lambda p: (not p["sicherheit"], p["name"]))


def parse_history(text: str) -> list[dict]:
    """Läufe aus /var/log/apt/history.log, neueste zuerst."""
    runs = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "Start-Date" not in fields:
            continue
        upgraded = fields.get("Upgrade", "")
        installed = fields.get("Install", "")
        runs.append(
            {
                "start": fields["Start-Date"].replace("  ", " "),
                "ende": fields.get("End-Date", "").replace("  ", " "),
                "befehl": fields.get("Commandline", ""),
                "pakete": _names(upgraded),
                "anzahl": len(_names(upgraded)),
                "neu_installiert": _names(installed),
                "fehler": fields.get("Error", ""),
            }
        )
    return list(reversed(runs))


def _names(field: str) -> list[str]:
    return [item.split(":")[0].strip() for item in re.split(r"\), ", field) if item.strip()]


def read_history(paths: list[Path] | None = None, limit: int = 8) -> list[dict]:
    """Die letzten Läufe aus dem aktuellen und den rotierten Protokollen. Läufe ohne Änderung werden ausgelassen."""
    paths = paths or [HISTORY, HISTORY.with_name("history.log.1.gz"), HISTORY.with_name("history.log.2.gz")]
    runs: list[dict] = []
    for path in paths:
        if len(runs) >= limit:
            break
        try:
            text = gzip.open(path, "rt", encoding="utf-8").read() if path.suffix == ".gz" else path.read_text(encoding="utf-8")
        except OSError:
            continue
        runs += [run_ for run_ in parse_history(text) if run_["pakete"] or run_["neu_installiert"]]
    return runs[:limit]


async def available() -> list[dict]:
    code, output = await run("apt", "list", "--upgradable", timeout=60)
    return parse_upgradable(output) if code == 0 else []


async def check_for_updates() -> tuple[int, str]:
    """Aktualisiert nur die Paketlisten (`apt-get update`), installiert nichts."""
    return await run("apt-get", "update", timeout=180)


def start_update(job: JobRunner) -> None:
    job.start("updates", UPDATE_STEPS, UPDATE_ENV)
