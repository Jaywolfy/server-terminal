"""Docker-Container anzeigen, starten, stoppen, neu starten und ihre Logs lesen."""

import json
import re

from server_terminal.system import run

NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
ACTIONS = {"start", "stop", "restart"}
LOG_LINES = 300


def parse_ps(text: str) -> list[dict]:
    """Zeilen von `docker ps -a --format json`."""
    containers = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        item = json.loads(line)
        containers.append(
            {
                "name": item.get("Names", ""),
                "image": item.get("Image", ""),
                "zustand": item.get("State", ""),
                "status": item.get("Status", ""),
                "ports": item.get("Ports", ""),
            }
        )
    return sorted(containers, key=lambda c: c["name"])


def parse_stats(text: str) -> dict[str, dict]:
    """Zeilen von `docker stats --no-stream --format json`: Name → CPU und Speicher."""
    stats = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            item = json.loads(line)
            stats[item.get("Name", "")] = {"cpu": item.get("CPUPerc", ""), "speicher": item.get("MemUsage", "").split(" / ")[0]}
    return stats


async def containers() -> list[dict]:
    code, output = await run("docker", "ps", "-a", "--format", "json", timeout=20)
    if code != 0:
        raise RuntimeError(output.strip() or "docker ps ist fehlgeschlagen")
    items = parse_ps(output)
    code, output = await run("docker", "stats", "--no-stream", "--format", "json", timeout=30)
    stats = parse_stats(output) if code == 0 else {}
    return [{**item, **stats.get(item["name"], {"cpu": "", "speicher": ""})} for item in items]


async def act(name: str, action: str) -> str:
    if not NAME.match(name):
        raise ValueError(f"Ungültiger Containername: {name!r}")
    if action not in ACTIONS:
        raise ValueError(f"Ungültige Aktion: {action!r}")
    code, output = await run("docker", action, name, timeout=90)
    if code != 0:
        raise RuntimeError(output.strip())
    return output.strip()


async def logs(name: str, lines: int = LOG_LINES) -> str:
    if not NAME.match(name):
        raise ValueError(f"Ungültiger Containername: {name!r}")
    code, output = await run("docker", "logs", "--tail", str(min(lines, 2000)), name, timeout=20)
    return output
