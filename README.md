# Server-Dashboard mit Terminal

[![Tests](https://github.com/Jaywolfy/server-terminal/actions/workflows/ci.yml/badge.svg)](https://github.com/Jaywolfy/server-terminal/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-182337)

Webseite für den eigenen Linux-Server: Uhrzeit, Laufzeit, Auslastung mit Verlaufsdiagrammen (1 Min bis 7 Tage), Updates mit
Knopf „Jetzt Updates machen“, Anmeldeversuche per SSH mit Herkunft, Docker-Verwaltung und ein Terminal im Browser.
Geschrieben in Python mit aiohttp, ohne Frontend-Framework und ohne Build-Schritt. Farben und Schriften wie
[jasonwolf.de](https://jasonwolf.de).

> **Achtung:** Der Dienst läuft als root und enthält ein Terminal. Wer sich anmelden kann, hat den ganzen Server.
> Deshalb gilt: Passwort **und** Zwei-Faktor-Code, Sperre nach Fehlversuchen, zusätzlicher Code für gefährliche
> Aktionen, Aktivitätsprotokoll. Betreiben Sie den Dienst nur hinter HTTPS.

## Seiten

| Seite | Inhalt |
|---|---|
| Dashboard | Kennzahlen, Diagramme (CPU, Speicher, Last, Netzwerk, Festplatten-I/O, Belegung), Updates, SSH-Versuche, Container |
| Terminal | Echte Shell (mit tmux, bleibt beim Schließen des Tabs bestehen) und eine Befehlsbibliothek zum Anklicken, auch mit eigenen Befehlen |
| Docker | Container starten, stoppen, neu starten, Logs lesen; Prozesse und fehlgeschlagene Dienste |
| Sicherheit | SSH-Versuche nach Adresse und Herkunft, erfolgreiche Anmeldungen, fail2ban-Sperren (entsperren), Aktivitätsprotokoll |

## Schutz

1. Nur bekannte Hostnamen und Ursprünge (`--allowed-host`).
2. Anmeldung mit Passwort (scrypt) und Zwei-Faktor-Code (TOTP, jeder Code gilt nur einmal). Nach 5 Fehlversuchen
   10 Minuten gesperrt.
3. Terminal, Updates, Docker-Aktionen und Neustart brauchen zusätzlich einen frischen Code (gilt 15 Minuten).
4. Sitzung: Cookie mit `HttpOnly`, `SameSite=Strict`, `Secure`, 8 Stunden.
5. Strenge Content-Security-Policy: Skripte nur aus eigenen Dateien, keine Skripte im Seitentext.
6. Jede wichtige Aktion steht im Aktivitätsprotokoll (Seite „Sicherheit“).

## Aufbau

| Modul | Aufgabe |
|---|---|
| `web.py`, `pages.py` | Web-App mit Seiten, JSON-Schnittstelle und WebSocket für das Terminal |
| `auth.py`, `totp.py` | Anmeldung mit Passwort (scrypt) und Einmalcode nach RFC 6238, Sperre nach Fehlversuchen |
| `audit.py` | Protokoll aller wichtigen Aktionen |
| `metrics.py`, `system.py` | Auslastung (CPU, Speicher, Last, Netzwerk, Festplatte) samt Verlauf, allgemeine Systemdaten |
| `updates.py`, `jobs.py` | Paketupdates über apt als Hintergrundauftrag mit mitlaufender Ausgabe |
| `logins.py`, `ssh_watch.py`, `geo.py` | SSH-Anmeldeversuche aus dem Journal, Meldung bei Anmeldungen von unbekannten Adressen, Herkunft der Adressen |
| `notify.py` | Handy-Nachrichten über ntfy |
| `docker_ctl.py` | Container anzeigen, starten, stoppen, Logs lesen |
| `terminal.py`, `commands.py` | Shell auf einem Pseudo-Terminal und Befehlsbibliothek |

Die Oberfläche unter `server_terminal/static` besteht aus einfachem JavaScript und CSS. Zu jedem Modul gibt es Tests
unter `tests/` (203 Tests, nur Standardbibliothek).

## Einrichtung

Beim ersten Start gibt der Dienst einen **Einrichtungscode** aus (`journalctl -u server-terminal -n 20`). Beim ersten
Besuch legen Sie damit Benutzername und Passwort fest und scannen den QR-Code mit einer Authenticator-App.
Zurücksetzen: `data/account.json` löschen und den Dienst neu starten.

## Betrieb

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp segno
cp deploy/server-terminal.service /etc/systemd/system/   # Pfade und --allowed-host vorher anpassen
systemctl daemon-reload && systemctl enable --now server-terminal
ufw allow from 172.17.0.0/16 to 172.17.0.1 port 5938 proto tcp   # nur nötig, wenn die Firewall Docker-Verkehr blockt
```

Im Nginx Proxy Manager: Proxy Host auf `172.17.0.1:5938`, **Websockets Support an**, SSL-Zertifikat, Force SSL.

## Herkunft von IP-Adressen

Die Herkunft (Land, Stadt, Anbieter) kommt von ip-api.com. Es werden nur öffentliche Adressen gesendet, jede einmal,
das Ergebnis wird 30 Tage gespeichert. Mit `--no-geo` bleibt alles lokal.

## Entwicklung

```bash
.venv/bin/pip install ruff
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/ruff check .
```

xterm.js liegt unter `server_terminal/static/vendor`. Version und Prüfsumme stehen in `MANIFEST.json`.
