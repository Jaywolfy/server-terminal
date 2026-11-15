"""Einstellungen des Dienstes."""

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5938
# Der Reverse Proxy läuft in Docker. Nur von dort werden die weitergeleiteten Client-Adressen geglaubt.
TRUSTED_PROXIES = ("172.16.0.0/12", "127.0.0.0/8", "::1/128")


@dataclass
class Config:
    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allowed_hosts: frozenset[str] = field(default_factory=lambda: frozenset({"127.0.0.1", "localhost"}))
    persistent_terminal: bool = True
    geo_enabled: bool = True
    require_secure_cookie: bool = False  # hinter dem Proxy bestimmt X-Forwarded-Proto das Secure-Flag
    disk_path: str = "/"
    watch_ssh: bool = True  # SSH-Anmeldungen beobachten und melden

    @property
    def account_file(self) -> Path:
        return self.data_dir / "account.json"

    @property
    def metrics_file(self) -> Path:
        return self.data_dir / "metrics.sqlite"

    @property
    def audit_file(self) -> Path:
        return self.data_dir / "audit.sqlite"

    @property
    def geo_file(self) -> Path:
        return self.data_dir / "geo.sqlite"

    @property
    def commands_file(self) -> Path:
        return self.data_dir / "commands.sqlite"

    @property
    def notify_file(self) -> Path:
        return self.data_dir / "notify.json"

    @property
    def known_file(self) -> Path:
        return self.data_dir / "bekannte-adressen.sqlite"

    @property
    def public_url(self) -> str | None:
        """Adresse, die ein Tipp auf die Nachricht öffnet (erster Hostname außer localhost)."""
        hosts = sorted(h for h in self.allowed_hosts if h not in ("127.0.0.1", "localhost"))
        return f"https://{hosts[0]}/sicherheit" if hosts else None

    def is_trusted_proxy(self, address: str | None) -> bool:
        try:
            ip = ipaddress.ip_address(address or "")
        except ValueError:
            return False
        return any(ip in ipaddress.ip_network(network) for network in TRUSTED_PROXIES)
