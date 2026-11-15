"""Allgemeine Systemdaten: Zeit, Laufzeit, Last, Speicher, Festplatten, Prozesse. Dazu ein Helfer für Befehle."""

import asyncio
import os
import platform
import socket
import time
from pathlib import Path

REAL_FILESYSTEMS = {"ext4", "ext3", "xfs", "btrfs", "zfs", "f2fs", "vfat", "ntfs", "exfat"}


async def run(*command: str, timeout: float = 20, input_text: str | None = None) -> tuple[int, str]:
    """Führt einen Befehl aus (ohne Shell) und gibt (Exit-Code, Ausgabe aus stdout und stderr) zurück."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, f"Befehl nicht gefunden: {command[0]}"
    try:
        output, _ = await asyncio.wait_for(process.communicate(input_text.encode() if input_text is not None else None), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, f"Zeitüberschreitung nach {timeout:.0f} s: {' '.join(command)}"
    return process.returncode or 0, output.decode("utf-8", "replace")


def uptime_seconds(read=lambda: Path("/proc/uptime").read_text()) -> float:
    return float(read().split()[0])


def os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return platform.system()


def parse_mounts(text: str) -> list[str]:
    """Einhängepunkte echter Dateisysteme, ohne Doppelte und ohne Docker-Overlays."""
    seen_devices, mounts = set(), []
    for line in text.splitlines():
        device, mount, filesystem = line.split()[:3]
        if filesystem not in REAL_FILESYSTEMS or device in seen_devices or mount.startswith(("/var/lib/docker", "/snap")):
            continue
        seen_devices.add(device)
        mounts.append(mount)
    return mounts


def disks() -> list[dict]:
    result = []
    for mount in parse_mounts(Path("/proc/mounts").read_text()):
        try:
            stat = os.statvfs(mount)
        except OSError:
            continue
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        if total:
            result.append(
                {"pfad": mount, "gesamt": total, "frei": free, "belegt_prozent": round(100 * (total - free) / total, 1)}
            )
    return result


def overview(now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    uptime = uptime_seconds()
    load = os.getloadavg()
    return {
        "zeit": now,
        "hostname": socket.gethostname(),
        "betriebssystem": os_name(),
        "kernel": platform.release(),
        "cpu_kerne": os.cpu_count(),
        "laufzeit_s": uptime,
        "start_zeit": now - uptime,
        "last": [round(v, 2) for v in load],
        "neustart_noetig": Path("/var/run/reboot-required").exists(),
        "festplatten": disks(),
    }


def parse_top(text: str, limit: int = 12, ignore: tuple[str, ...] = ("top",)) -> list[dict]:
    """Letzte Momentaufnahme aus `top -b -n 2`: die CPU-Werte der zweiten Runde gelten für das Messintervall."""
    frames = text.split("\n\n")
    lines = []
    for frame in frames:
        if "PID USER" in frame:
            lines = frame.splitlines()
    result: list[dict] = []
    header = next((index for index, line in enumerate(lines) if line.lstrip().startswith("PID USER")), None)
    if header is None:
        return result
    for line in lines[header + 1 :]:
        fields = line.split(None, 11)
        if len(fields) < 12 or not fields[0].isdigit() or fields[11].split()[0] in ignore:
            continue
        result.append(
            {
                "pid": int(fields[0]),
                "benutzer": fields[1],
                "cpu": float(fields[8].replace(",", ".")),
                "speicher": float(fields[9].replace(",", ".")),
                "laeuft": fields[10],
                "name": fields[11].strip(),
            }
        )
        if len(result) >= limit:
            break
    return result


async def processes(limit: int = 12) -> list[dict]:
    code, output = await run("top", "-b", "-n", "2", "-d", "0.6", "-o", "%CPU", "-w", "256", timeout=15)
    return parse_top(output, limit) if code == 0 else []


async def failed_services() -> list[str]:
    code, output = await run("systemctl", "--failed", "--no-legend", "--plain", timeout=10)
    if code != 0:
        return []
    return [line.split()[0] for line in output.splitlines() if line.strip()]
