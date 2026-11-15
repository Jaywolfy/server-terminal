"""Befehlsbibliothek für das Terminal: vorgegebene und eigene Befehle, die sich per Klick einfügen lassen."""

import sqlite3
import uuid
from pathlib import Path

DEFAULTS = [
    ("System", "Systemübersicht", "uptime && free -h && df -h /"),
    ("System", "Top-Prozesse nach CPU", "ps -eo pid,user:14,pcpu,pmem,etime,comm --sort=-pcpu | head -15"),
    ("System", "Top-Prozesse nach Speicher", "ps -eo pid,user:14,pcpu,pmem,etime,comm --sort=-pmem | head -15"),
    ("System", "Fehlgeschlagene Dienste", "systemctl --failed"),
    ("System", "Speicherverbrauch der Ordner", "du -h --max-depth=1 /DATA 2>/dev/null | sort -rh | head -15"),
    ("Updates", "Verfügbare Updates", "apt list --upgradable"),
    ("Updates", "Alles aktualisieren", "apt-get update && apt-get -y dist-upgrade"),
    ("Updates", "Neustart nötig?", "ls /var/run/reboot-required 2>/dev/null && cat /var/run/reboot-required.pkgs || echo nein"),
    ("Docker", "Container anzeigen", "docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'"),
    ("Docker", "Container-Auslastung", "docker stats --no-stream"),
    ("Docker", "Speicher von Docker", "docker system df"),
    ("Docker", "Ungenutzte Images löschen", "docker image prune -a"),
    ("Netzwerk", "Offene Ports", "ss -tlnp"),
    ("Netzwerk", "Firewall-Status", "ufw status verbose"),
    ("Netzwerk", "Aktive Verbindungen", "ss -tn state established | head -30"),
    ("Sicherheit", "Gesperrte IPs (fail2ban)", "fail2ban-client status sshd"),
    ("Sicherheit", "Letzte Anmeldungen", "last -n 15 -a"),
    ("Sicherheit", "Fehlgeschlagene SSH-Versuche heute", "journalctl -u ssh --since today | grep -c 'Failed password'"),
    ("Logs", "Fehler im Journal (heute)", "journalctl -p err --since today --no-pager | tail -50"),
    ("Logs", "Proxy-Manager-Protokoll", "docker logs --tail 80 nginxproxymanager"),
    ("Logs", "Dieser Dienst", "journalctl -u server-terminal -n 80 --no-pager"),
]


class CommandLibrary:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("CREATE TABLE IF NOT EXISTS befehle (id TEXT PRIMARY KEY, gruppe TEXT, name TEXT, befehl TEXT)")
        self.connection.commit()

    def all(self) -> list[dict]:
        defaults = [
            {"id": f"std-{index}", "gruppe": group, "name": name, "befehl": command, "eigen": False}
            for index, (group, name, command) in enumerate(DEFAULTS)
        ]
        custom = [
            {"id": row[0], "gruppe": row[1], "name": row[2], "befehl": row[3], "eigen": True}
            for row in self.connection.execute("SELECT id, gruppe, name, befehl FROM befehle ORDER BY gruppe, name")
        ]
        return defaults + custom

    def add(self, group: str, name: str, command: str) -> dict:
        group, name, command = group.strip() or "Eigene", name.strip(), command.strip()
        if not name or not command:
            raise ValueError("Name und Befehl dürfen nicht leer sein.")
        if len(name) > 80 or len(group) > 40 or len(command) > 2000:
            raise ValueError("Name, Gruppe oder Befehl sind zu lang.")
        entry = {"id": uuid.uuid4().hex[:10], "gruppe": group, "name": name, "befehl": command, "eigen": True}
        self.connection.execute("INSERT INTO befehle VALUES (?, ?, ?, ?)", (entry["id"], group, name, command))
        self.connection.commit()
        return entry

    def delete(self, command_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM befehle WHERE id = ?", (command_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self.connection.close()
