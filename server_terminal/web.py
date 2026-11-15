"""Die Web-App: Seiten, JSON-Schnittstelle und das Terminal per WebSocket.

Schutz, von außen nach innen:
 1. Nur bekannte Hostnamen und Ursprünge (gegen fremde Webseiten und DNS-Tricks).
 2. Anmeldung mit Passwort und Zwei-Faktor-Code, Sperre nach Fehlversuchen.
 3. Gefährliche Aktionen (Terminal, Updates, Docker, Neustart) brauchen zusätzlich einen frischen Zwei-Faktor-Code
    („erhöhte“ Stufe, 15 Minuten).
 4. Jede wichtige Aktion steht im Aktivitätsprotokoll.
"""

import asyncio
import json
import secrets
import time
from http.cookies import SimpleCookie
from pathlib import Path

import segno
from aiohttp import WSMsgType, web

from server_terminal import __version__, docker_ctl, logins, pages, system, totp, updates
from server_terminal.audit import AuditLog
from server_terminal.auth import ELEVATED_SECONDS, SESSION_SECONDS, AccountStore, Lockout, new_setup_code
from server_terminal.commands import CommandLibrary
from server_terminal.config import Config
from server_terminal.geo import GeoCache
from server_terminal.jobs import JobBusyError, JobRunner
from server_terminal.metrics import MetricsService, MetricsStore
from server_terminal.notify import EVENTS, Notifier, NotifySettings, new_topic
from server_terminal.ssh_watch import KnownAddresses, SshWatcher
from server_terminal.terminal import MAX_SESSIONS, TerminalSession, shell_command

STATIC = Path(__file__).parent / "static"
COOKIE = "session"
HEADER_CHECK = ("X-Requested-With", "server-terminal")
PENDING_SECONDS = 600
LOGIN_CACHE_SECONDS = 30
PUBLIC_PATHS = {"/login", "/setup", "/setup/bestaetigen"}


def json_error(status: int, message: str, **extra) -> web.Response:
    return web.json_response({"fehler": message, **extra}, status=status)


class App:
    """Hält die Dienste zusammen, damit Tests sie austauschen können."""

    def __init__(self, config: Config, clock=time.time, run=system.run):
        self.config = config
        self.clock = clock
        self.run = run
        self.account = AccountStore(config.account_file)
        self.audit = AuditLog(config.audit_file)
        self.lockout = Lockout()
        self.metrics = MetricsService(MetricsStore(config.metrics_file))
        self.geo = GeoCache(config.geo_file, enabled=config.geo_enabled)
        self.commands = CommandLibrary(config.commands_file)
        self.job = JobRunner()
        self.notify_settings = NotifySettings(config.notify_file)
        self.notifier = Notifier(self.notify_settings, self.audit, click_url=config.public_url)
        self.watcher = SshWatcher(
            self.notifier,
            KnownAddresses(config.known_file),
            lambda ip: self.geo.lookup([ip]).get(ip, {}),
            _journal_history,
        )
        self._tasks: set[asyncio.Task] = set()
        self._lockout_notified: dict[str, float] = {}
        self.sessions = 0
        self.setup_code: str | None = None if self.account.exists() else new_setup_code()
        self.pending: dict[str, dict] = {}
        self._login_cache: dict[int, tuple[float, dict]] = {}

    # --- Hilfen

    def spawn(self, coroutine) -> None:
        """Startet eine Aufgabe im Hintergrund (Nachrichten sollen die Antwort nicht aufhalten)."""
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def tell(self, event: str, title: str, message: str, priority: int = 3, tags: tuple[str, ...] = ()) -> None:
        self.spawn(self.notifier.notify(event, title, message, priority, tags))

    def client_ip(self, request: web.Request) -> str:
        peer = request.remote or ""
        if self.config.is_trusted_proxy(peer):
            forwarded = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            return forwarded or peer
        return peer

    def session(self, request: web.Request) -> dict | None:
        cookies = SimpleCookie(request.headers.get("Cookie", ""))
        morsel = cookies.get(COOKIE)
        return self.account.read_session(morsel.value, self.clock()) if morsel else None

    def set_cookie(self, request: web.Request, response: web.StreamResponse, elevated: bool) -> None:
        token = self.account.new_session(self.clock(), elevated)
        secure = self.config.require_secure_cookie or request.headers.get("X-Forwarded-Proto") == "https"
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="Strict", secure=secure, path="/")

    def elevated(self, session: dict) -> bool:
        return session.get("elev", 0) > self.clock()


def security_headers(request: web.Request) -> dict[str, str]:
    host = request.host.split(":")[0]
    return {
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' wss://{host} ws://{host}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        # Nicht "no-referrer": Dann sendet der Browser bei Formularen Origin: null, und der Ursprungsschutz würde sie abweisen
        "Referrer-Policy": "same-origin",
        "Cache-Control": "no-store",
        "Cross-Origin-Opener-Policy": "same-origin",
    }


@web.middleware
async def protect(request: web.Request, handler):
    app: App = request.app["app"]
    allowed = app.config.allowed_hosts
    if request.host.rsplit(":", 1)[0].strip("[]") not in allowed and request.host not in allowed:
        return web.Response(status=403, text="Über diesen Namen nicht erreichbar")
    origin = request.headers.get("Origin")
    if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0] not in allowed and origin.split("://", 1)[-1] not in allowed:
        return web.Response(status=403, text="Fremder Ursprung")

    path = request.path
    if path not in PUBLIC_PATHS and not path.startswith("/static/"):
        session = app.session(request)
        if session is None:
            if path.startswith("/api/") or path.startswith("/ws/"):
                return json_error(401, "Nicht angemeldet")
            if not app.account.exists():
                raise web.HTTPSeeOther("/setup")
            raise web.HTTPSeeOther("/login")
        request["session"] = session
        if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/"):
            if request.headers.get(HEADER_CHECK[0]) != HEADER_CHECK[1]:
                return json_error(403, "Anfrage nicht zugelassen")
    response = await handler(request)
    for name, value in security_headers(request).items():
        response.headers.setdefault(name, value)
    return response


def html(content: str, status: int = 200) -> web.Response:
    return web.Response(text=content, status=status, content_type="text/html")


def needs_elevation(app: App, request: web.Request) -> web.Response | None:
    if not app.elevated(request["session"]):
        return json_error(403, "Bitte mit einem frischen Zwei-Faktor-Code bestätigen.", erhoehen=True)
    return None


# --- Anmeldung und Einrichtung


async def login_page(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if not app.account.exists():
        raise web.HTTPSeeOther("/setup")
    return html(pages.login())


async def login_submit(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    ip, now = app.client_ip(request), app.clock()
    if not app.account.exists():
        raise web.HTTPSeeOther("/setup")
    if app.lockout.blocked(ip, now):
        app.audit.add("anmeldung_gesperrt", ip)
        if now - app._lockout_notified.get(ip, 0) > 1800:
            app._lockout_notified[ip] = now
            app.tell(
                "web_gesperrt",
                "Dashboard: viele falsche Anmeldungen",
                f"Die Adresse {ip} wurde nach zu vielen Fehlversuchen gesperrt.",
                4,
                ("warning",),
            )
        return html(pages.login("Zu viele Versuche. Bitte in einigen Minuten erneut versuchen."), 429)
    form = await request.post()
    ok = app.account.verify_password(str(form.get("benutzer", "")), str(form.get("passwort", "")))
    ok = app.account.verify_totp(str(form.get("code", ""))) and ok  # Code immer prüfen, damit nichts verraten wird
    if not ok:
        app.lockout.fail(ip, now)
        app.audit.add("anmeldung_fehlgeschlagen", ip, str(form.get("benutzer", ""))[:64])
        return html(pages.login("Anmeldung fehlgeschlagen."), 401)
    app.lockout.succeed(ip)
    app.audit.add("angemeldet", ip)
    app.tell(
        "web_anmeldung",
        "Anmeldung am Server-Dashboard",
        f"Von {ip} um {time.strftime('%H:%M', time.localtime(now))} Uhr.",
        4,
        ("unlock",),
    )
    response = web.HTTPSeeOther("/")
    app.set_cookie(request, response, elevated=True)
    return response


async def logout(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    app.audit.add("abgemeldet", app.client_ip(request))
    response = web.HTTPSeeOther("/login")
    response.del_cookie(COOKIE, path="/")
    return response


async def setup_page(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if app.account.exists():
        raise web.HTTPSeeOther("/login")
    return html(pages.setup())


async def setup_submit(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    ip, now = app.client_ip(request), app.clock()
    if app.account.exists():
        raise web.HTTPSeeOther("/login")
    if app.lockout.blocked(ip, now):
        return html(pages.setup("Zu viele Versuche. Bitte in einigen Minuten erneut versuchen."), 429)
    form = await request.post()
    code = str(form.get("einrichtungscode", "")).strip()
    if not app.setup_code or not secrets.compare_digest(code.encode(), app.setup_code.encode()):
        app.lockout.fail(ip, now)
        app.audit.add("einrichtung_falscher_code", ip)
        return html(pages.setup("Der Einrichtungscode stimmt nicht."), 400)
    user, password = str(form.get("benutzer", "")).strip(), str(form.get("passwort", ""))
    error = AccountStore.check_new(user, password, str(form.get("wiederholt", "")))
    if error:
        return html(pages.setup(error), 400)
    token, secret = secrets.token_urlsafe(24), totp.new_secret()
    app.pending = {token: {"benutzer": user, "passwort": password, "totp": secret, "bis": now + PENDING_SECONDS}}
    return html(_totp_page(app, token, secret, user))


def _totp_page(app: App, token: str, secret: str, user: str, error: str | None = None) -> str:
    uri = totp.provisioning_uri(secret, user, "Server")
    qr = segno.make(uri, error="m").svg_inline(scale=5, dark="#182337", light=None, omitsize=True)
    return pages.setup_totp(token, qr, secret, error)


async def setup_confirm(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if app.account.exists():
        raise web.HTTPSeeOther("/login")
    form = await request.post()
    token = str(form.get("token", ""))
    pending = app.pending.get(token)
    if not pending or pending["bis"] < app.clock():
        app.pending.clear()
        return html(pages.setup("Die Einrichtung ist abgelaufen. Bitte noch einmal beginnen."), 400)
    step = totp.verify(pending["totp"], str(form.get("code", "")), app.clock())
    if step is None:
        return html(
            _totp_page(app, token, pending["totp"], pending["benutzer"], "Der Code stimmt nicht. Bitte noch einmal versuchen."),
            400,
        )
    app.account.create(pending["benutzer"], pending["passwort"], pending["totp"])
    app.account.verify_totp(totp.code_at(pending["totp"], step), app.clock())  # verbraucht den Code
    app.pending.clear()
    app.setup_code = None
    app.audit.add("konto_eingerichtet", app.client_ip(request))
    response = web.HTTPSeeOther("/")
    app.set_cookie(request, response, elevated=True)
    return response


async def elevate(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    ip, now = app.client_ip(request), app.clock()
    if app.lockout.blocked(ip, now):
        return json_error(429, "Zu viele Versuche. Bitte warten.")
    body = await request.json()
    if not app.account.verify_totp(str(body.get("code", ""))):
        app.lockout.fail(ip, now)
        app.audit.add("freischaltung_fehlgeschlagen", ip)
        return json_error(403, "Der Code stimmt nicht.")
    app.lockout.succeed(ip)
    app.audit.add("freigeschaltet", ip)
    response = web.json_response({"ok": True, "sekunden": ELEVATED_SECONDS})
    app.set_cookie(request, response, elevated=True)
    return response


# --- Seiten


def page(render):
    async def handler(request: web.Request) -> web.Response:
        return html(render())

    return handler


# --- Schnittstelle (lesend)


async def api_overview(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    data = await asyncio.get_running_loop().run_in_executor(None, system.overview, app.clock())
    last = app.metrics.recent[-1] if app.metrics.recent else None
    data["jetzt"] = last
    data["version"] = __version__
    data["erhoeht"] = app.elevated(request["session"])
    return web.json_response(data)


async def api_metrics(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    try:
        return web.json_response(app.metrics.series(request.query.get("ansicht", "5min")))
    except ValueError as error:
        return json_error(400, str(error))


async def api_updates(request: web.Request) -> web.Response:
    packages = await updates.available()
    history = await asyncio.get_running_loop().run_in_executor(None, updates.read_history)
    reboot = Path("/var/run/reboot-required").exists()
    return web.json_response({"verfuegbar": packages, "verlauf": history, "neustart_noetig": reboot})


async def api_updates_check(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    code, output = await updates.check_for_updates()
    app.audit.add("updates_geprueft", app.client_ip(request), f"exit {code}")
    return web.json_response({"ok": code == 0, "ausgabe": output[-2000:]})


async def api_updates_run(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if (denied := needs_elevation(app, request)) is not None:
        return denied
    try:
        updates.start_update(app.job)
    except JobBusyError as error:
        return json_error(409, str(error))
    app.audit.add("updates_gestartet", app.client_ip(request))
    return web.json_response({"ok": True})


async def api_job(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    try:
        since = max(int(request.query.get("seit", "0")), 0)
    except ValueError:
        since = 0
    return web.json_response(app.job.status(since))


async def api_reboot(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if (denied := needs_elevation(app, request)) is not None:
        return denied
    body = await request.json()
    if body.get("bestaetigung") != "NEUSTART":
        return json_error(400, "Zur Bestätigung bitte NEUSTART eingeben.")
    app.audit.add("neustart", app.client_ip(request))
    code, output = await app.run("systemd-run", "--on-active=5", "--unit=server-terminal-reboot", "systemctl", "reboot")
    return web.json_response({"ok": code == 0, "ausgabe": output.strip()}, status=200 if code == 0 else 500)


async def api_logins(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    try:
        hours = min(max(int(request.query.get("stunden", "24")), 1), 24 * 30)
    except ValueError:
        hours = 24
    cached = app._login_cache.get(hours)
    if cached and app.clock() - cached[0] < LOGIN_CACHE_SECONDS:
        return web.json_response(cached[1])
    text = await logins.read_journal(hours)
    events = logins.parse_events(text)
    ips = sorted({e["ip"] for e in events})
    try:
        geo = await asyncio.get_running_loop().run_in_executor(None, app.geo.lookup, ips)
        geo_error = None
    except OSError as error:
        geo, geo_error = app.geo._cached(ips), f"Herkunft konnte nicht abgefragt werden: {error}"
    result = logins.summarize(events, geo)
    result.update({"stunden": hours, "herkunft_aktiv": app.geo.enabled, "herkunft_fehler": geo_error})
    app._login_cache[hours] = (app.clock(), result)
    return web.json_response(result)


async def api_bans(request: web.Request) -> web.Response:
    return web.json_response({"gesperrt": await logins.banned_ips()})


async def api_unban(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if (denied := needs_elevation(app, request)) is not None:
        return denied
    ip = str((await request.json()).get("ip", ""))
    if ip not in await logins.banned_ips():
        return json_error(404, "Diese Adresse ist nicht gesperrt.")
    code, output = await app.run("fail2ban-client", "set", "sshd", "unbanip", ip)
    app.audit.add("entsperrt", app.client_ip(request), ip)
    return web.json_response({"ok": code == 0, "ausgabe": output.strip()})


async def api_docker(request: web.Request) -> web.Response:
    try:
        return web.json_response({"container": await docker_ctl.containers()})
    except RuntimeError as error:
        return json_error(502, str(error))


async def api_docker_action(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if (denied := needs_elevation(app, request)) is not None:
        return denied
    name, action = request.match_info["name"], request.match_info["aktion"]
    try:
        output = await docker_ctl.act(name, action)
    except ValueError as error:
        return json_error(400, str(error))
    except RuntimeError as error:
        return json_error(500, str(error))
    app.audit.add(f"docker_{action}", app.client_ip(request), name)
    return web.json_response({"ok": True, "ausgabe": output})


async def api_docker_logs(request: web.Request) -> web.Response:
    try:
        return web.json_response({"logs": await docker_ctl.logs(request.match_info["name"])})
    except ValueError as error:
        return json_error(400, str(error))


async def api_processes(request: web.Request) -> web.Response:
    return web.json_response({"prozesse": await system.processes(), "dienste_fehlgeschlagen": await system.failed_services()})


async def api_audit(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    return web.json_response({"eintraege": app.audit.recent(100)})


async def api_commands(request: web.Request) -> web.Response:
    return web.json_response({"befehle": request.app["app"].commands.all()})


async def api_commands_add(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    body = await request.json()
    try:
        entry = app.commands.add(str(body.get("gruppe", "")), str(body.get("name", "")), str(body.get("befehl", "")))
    except ValueError as error:
        return json_error(400, str(error))
    return web.json_response(entry, status=201)


async def api_commands_delete(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if not app.commands.delete(request.match_info["id"]):
        return json_error(404, "Befehl nicht gefunden oder vorgegeben.")
    return web.json_response({"ok": True})


# --- Terminal


async def terminal_socket(request: web.Request) -> web.StreamResponse:
    app: App = request.app["app"]
    if not app.elevated(request["session"]):
        return json_error(403, "Bitte mit einem frischen Zwei-Faktor-Code bestätigen.", erhoehen=True)
    if app.sessions >= MAX_SESSIONS:
        return json_error(429, f"Höchstens {MAX_SESSIONS} Terminals gleichzeitig.")
    socket = web.WebSocketResponse(heartbeat=25, max_msg_size=1_000_000)
    await socket.prepare(request)
    ip = app.client_ip(request)
    app.sessions += 1
    app.audit.add("terminal_geoeffnet", ip)
    app.tell("terminal", "Terminal geöffnet", f"Ein Terminal im Browser wurde von {ip} geöffnet.", 4, ("computer",))
    try:
        columns, rows = int(request.query.get("c", 100)), int(request.query.get("r", 30))
        session = TerminalSession(shell_command(app.config.persistent_terminal), columns, rows)
    except (OSError, ValueError) as error:
        app.sessions -= 1
        await socket.send_str(f"\r\nTerminal konnte nicht gestartet werden: {error}\r\n")
        await socket.close()
        return socket

    async def pump() -> None:
        while (chunk := await session.read()) is not None:
            await socket.send_str(chunk)
        await socket.close()

    pump_task = asyncio.create_task(pump())
    try:
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(message.data)
            except json.JSONDecodeError:
                continue
            if data.get("t") == "i" and isinstance(data.get("d"), str):
                session.write(data["d"])
            elif data.get("t") == "r":
                session.resize(int(data.get("c", 80)), int(data.get("r", 24)))
    finally:
        pump_task.cancel()
        session.close()
        app.sessions -= 1
        app.audit.add("terminal_geschlossen", ip)
    return socket


# --- Handy-Nachrichten


def notify_state(app: App) -> dict:
    settings = app.notify_settings.read()
    qr = ""
    if settings["topic"]:
        host = settings["server"].split("://", 1)[1]
        qr = segno.make(f"ntfy://{host}/{settings['topic']}", error="m").svg_inline(
            scale=4, dark="#182337", light=None, omitsize=True
        )
    return {**settings, "beschreibung": {key: label for key, (label, _) in EVENTS.items()}, "qr": qr}


async def api_notify(request: web.Request) -> web.Response:
    return web.json_response(notify_state(request.app["app"]))


async def api_notify_save(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    if (denied := needs_elevation(app, request)) is not None:
        return denied
    body = await request.json()
    current = app.notify_settings.read()
    topic = new_topic() if body.get("neues_thema") else str(body.get("topic", current["topic"]))
    try:
        app.notify_settings.save(
            bool(body.get("aktiv")), str(body.get("server", current["server"])), topic, body.get("ereignisse", {})
        )
    except ValueError as error:
        return json_error(400, str(error))
    app.audit.add("benachrichtigungen_geaendert", app.client_ip(request), "aktiv" if body.get("aktiv") else "aus")
    return web.json_response(notify_state(app))


async def api_notify_test(request: web.Request) -> web.Response:
    app: App = request.app["app"]
    try:
        await app.notifier.test()
    except ValueError as error:
        return json_error(400, str(error))
    except OSError as error:
        return json_error(502, f"Die Nachricht konnte nicht gesendet werden: {error}")
    return web.json_response({"ok": True})


def _journal_history() -> str:
    import subprocess

    try:
        return subprocess.run(
            ["journalctl", "-u", "ssh", "--since", "30 days ago", "-o", "short-iso", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --- Aufbau


async def favicon(request: web.Request) -> web.Response:
    return web.Response(text=pages.FAVICON, content_type="image/svg+xml")


def create_app(config: Config, **kwargs) -> web.Application:
    app = web.Application(middlewares=[protect])
    state = App(config, **kwargs)
    app["app"] = state
    app.add_routes(
        [
            web.get("/login", login_page),
            web.post("/login", login_submit),
            web.post("/logout", logout),
            web.get("/setup", setup_page),
            web.post("/setup", setup_submit),
            web.post("/setup/bestaetigen", setup_confirm),
            web.get("/static/favicon.svg", favicon),
            web.get("/", page(pages.dashboard)),
            web.get("/terminal", page(pages.terminal)),
            web.get("/docker", page(pages.docker)),
            web.get("/sicherheit", page(pages.security)),
            web.get("/benachrichtigungen", page(pages.notifications)),
            web.get("/api/notify", api_notify),
            web.post("/api/notify", api_notify_save),
            web.post("/api/notify/test", api_notify_test),
            web.get("/api/overview", api_overview),
            web.get("/api/metrics", api_metrics),
            web.get("/api/updates", api_updates),
            web.post("/api/updates/check", api_updates_check),
            web.post("/api/updates/run", api_updates_run),
            web.get("/api/job", api_job),
            web.post("/api/reboot", api_reboot),
            web.post("/api/elevate", elevate),
            web.get("/api/logins", api_logins),
            web.get("/api/bans", api_bans),
            web.post("/api/unban", api_unban),
            web.get("/api/docker", api_docker),
            web.post("/api/docker/{name}/{aktion}", api_docker_action),
            web.get("/api/docker/{name}/logs", api_docker_logs),
            web.get("/api/processes", api_processes),
            web.get("/api/audit", api_audit),
            web.get("/api/commands", api_commands),
            web.post("/api/commands", api_commands_add),
            web.delete("/api/commands/{id}", api_commands_delete),
            web.get("/ws/terminal", terminal_socket),
        ]
    )
    app.router.add_static("/static/", STATIC, follow_symlinks=False)

    async def on_startup(_: web.Application) -> None:
        state.metrics.start()
        if config.watch_ssh:
            state.watcher.start()

    async def on_cleanup(_: web.Application) -> None:
        await state.metrics.stop()
        if config.watch_ssh:
            await state.watcher.stop()
        else:
            state.watcher.known.close()
        state.audit.close()
        state.geo.close()
        state.commands.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app
