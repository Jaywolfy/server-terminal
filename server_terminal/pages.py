"""HTML-Seiten. Die Seiten enthalten keine Skripte im Text: Alles läuft über Dateien unter /static (siehe CSP)."""

from html import escape

ICON_HOME = '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>'
ICON_TERMINAL = '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>'
ICON_BOX = '<path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>'
ICON_SHIELD = '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>'
ICON_BELL = '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>'
# Adresse, Beschriftung, Symbol. Auf dem Handy wird daraus die Leiste am unteren Rand.
PAGES = [
    ("/", "Dashboard", ICON_HOME),
    ("/terminal", "Terminal", ICON_TERMINAL),
    ("/docker", "Docker", ICON_BOX),
    ("/sicherheit", "Sicherheit", ICON_SHIELD),
    ("/benachrichtigungen", "Alarme", ICON_BELL),
]
FONTS = (
    "https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600"
    "&family=Source+Serif+4:wght@600&family=JetBrains+Mono:wght@400;500&display=swap"
)


def layout(
    title: str,
    body: str,
    active: str = "/",
    scripts: tuple[str, ...] = (),
    user: str | None = None,
    stylesheets: tuple[str, ...] = (),
    classic: tuple[str, ...] = (),
) -> str:
    links = "".join(
        f'<a href="{path}"{" class=active" if path == active else ""}>'
        f'<svg viewBox="0 0 24 24" aria-hidden="true">{icon}</svg><span>{label}</span></a>'
        for path, label, icon in PAGES
    )
    nav = f"<nav>{links}</nav>" if user else ""
    account = (
        f'<span class="who" id="clock" title="Serverzeit"></span><form method="post" action="/logout">'
        f'<button class="ghost" type="submit" title="Abmelden ({escape(user)})">Abmelden</button></form>'
        if user
        else ""
    )
    scripts_html = "".join(f'<script src="/static/vendor/{name}"></script>' for name in classic)
    scripts_html += "".join(f'<script type="module" src="/static/js/{name}"></script>' for name in scripts)
    sheets = "".join(f'<link rel="stylesheet" href="/static/vendor/{name}">' for name in stylesheets)
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#f6f4ef">
<meta name="color-scheme" content="light">
<title>{escape(title)} · Server</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="{FONTS}">
{sheets}<link rel="stylesheet" href="/static/css/style.css">
</head>
<body data-page="{escape(active)}">
<header class="topbar">
<div class="brand"><h1>Server</h1><p id="host">&nbsp;</p></div>
{nav}
<div class="account">{account}</div>
</header>
<main>
{body}
</main>
<div id="modal-root"></div>
{scripts_html}
</body>
</html>
"""


def dashboard() -> str:
    body = """
<section class="widget kpis" id="kpis" aria-label="Kennzahlen"></section>
<div class="grid dash">
  <section class="widget w-graphs">
    <header><h2>Auslastung</h2><div class="ranges" id="ranges" role="tablist" aria-label="Zeitraum"></div></header>
    <div class="body"><div class="charts" id="charts"></div></div>
  </section>
  <section class="widget w-updates" id="updates"><header><h2>Updates</h2><span id="updates-note"></span></header><div class="body" id="updates-body"></div></section>
  <section class="widget w-logins" id="logins"><header><h2>Anmeldeversuche (SSH)</h2><div class="ranges" id="login-ranges"></div></header><div class="body" id="logins-body"></div></section>
  <section class="widget w-docker"><header><h2>Container</h2><a href="/docker">Alle →</a></header><div class="body" id="docker-body"></div></section>
</div>
"""
    return layout("Dashboard", body, "/", ("dashboard.js",), "x")


def terminal() -> str:
    body = """
<div class="term-page">
  <section class="widget w-term">
    <header><h2>Terminal</h2><div class="head-actions"><button class="small ghost only-mobile" id="cmd-open" type="button">Befehle</button><span id="term-status" class="pill">getrennt</span></div></header>
    <div class="body"><div id="term"></div></div>
    <div class="keybar" id="keybar" aria-label="Sondertasten"></div>
  </section>
  <aside class="widget w-cmds">
    <header><h2>Befehle</h2><button class="small" id="cmd-add" type="button">+ Neu</button></header>
    <div class="body" id="cmds"></div>
  </aside>
</div>
"""
    return layout("Terminal", body, "/terminal", ("terminal.js",), "x", ("xterm.css",), ("xterm.js", "addon-fit.js"))


def docker() -> str:
    body = """
<section class="widget"><header><h2>Docker-Container</h2><span id="docker-note"></span></header><div class="body" id="docker-table"></div></section>
<section class="widget"><header><h2>Prozesse</h2><span>nach CPU</span></header><div class="body" id="processes"></div></section>
"""
    return layout("Docker", body, "/docker", ("docker.js",), "x")


def security() -> str:
    body = """
<div class="grid sec">
  <section class="widget"><header><h2>SSH-Versuche</h2><div class="ranges" id="sec-ranges"></div></header><div class="body" id="sec-summary"></div></section>
  <section class="widget"><header><h2>Gesperrt (fail2ban)</h2><span id="ban-note"></span></header><div class="body" id="bans"></div></section>
  <section class="widget"><header><h2>Erfolgreiche Anmeldungen</h2></header><div class="body" id="sec-ok"></div></section>
  <section class="widget"><header><h2>Aktivitätsprotokoll</h2><span>dieser Webseite</span></header><div class="body" id="audit"></div></section>
</div>
"""
    return layout("Sicherheit", body, "/sicherheit", ("security.js",), "x")


def notifications() -> str:
    body = """
<div class="grid sec">
  <section class="widget"><header><h2>Handy-Nachrichten</h2><span id="notify-status"></span></header><div class="body" id="notify-form"></div></section>
  <section class="widget"><header><h2>So richten Sie das iPhone ein</h2></header><div class="body" id="notify-help"></div></section>
</div>
"""
    return layout("Alarme", body, "/benachrichtigungen", ("notify.js",), "x")


def _notice(error: str | None) -> str:
    return f'<p class="error">{escape(error)}</p>' if error else ""


def login(error: str | None = None) -> str:
    form = f"""{_notice(error)}<form class="auth-form" method="post" action="/login">
<label>Benutzername<input name="benutzer" autocomplete="username" required autofocus></label>
<label>Passwort<input name="passwort" type="password" autocomplete="current-password" required></label>
<label>Zwei-Faktor-Code<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{{6,7}}" required></label>
<button type="submit">Anmelden</button>
</form>"""
    return layout(
        "Anmelden",
        f'<div class="auth"><section class="widget"><header><h2>Anmelden</h2></header><div class="body">{form}</div></section></div>',
    )


def setup(error: str | None = None) -> str:
    form = f"""{_notice(error)}<p class="muted">Der Einrichtungscode steht im Protokoll des Dienstes:
<code>journalctl -u server-terminal -n 20</code>. Er verhindert, dass jemand anderes zuerst einrichtet.</p>
<form class="auth-form" method="post" action="/setup">
<label>Einrichtungscode<input name="einrichtungscode" autocomplete="off" required autofocus></label>
<label>Benutzername<input name="benutzer" autocomplete="username" required></label>
<label>Passwort (mindestens 12 Zeichen)<input name="passwort" type="password" autocomplete="new-password" required></label>
<label>Passwort wiederholen<input name="wiederholt" type="password" autocomplete="new-password" required></label>
<button type="submit">Weiter</button>
</form>"""
    return layout(
        "Einrichtung",
        f'<div class="auth"><section class="widget"><header><h2>Einrichtung</h2></header><div class="body">{form}</div></section></div>',
    )


def setup_totp(token: str, qr_svg: str, secret: str, error: str | None = None) -> str:
    form = f"""{_notice(error)}<p class="muted">Scannen Sie den QR-Code mit einer Authenticator-App
(z. B. Aegis, 2FAS oder Google Authenticator) und geben Sie den angezeigten Code ein.</p>
<div class="qr">{qr_svg}</div>
<p class="muted">Oder tragen Sie den Schlüssel von Hand ein: <code>{escape(secret)}</code></p>
<form class="auth-form" method="post" action="/setup/bestaetigen">
<input type="hidden" name="token" value="{escape(token, quote=True)}">
<label>Code aus der App<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{{6,7}}" required autofocus></label>
<button type="submit">Zugang aktivieren</button>
</form>"""
    return layout(
        "Zwei-Faktor",
        f'<div class="auth"><section class="widget"><header><h2>Zwei-Faktor-Code</h2></header><div class="body">{form}</div></section></div>',
    )


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="15" fill="#182337"/>
<rect x="10" y="12" width="44" height="40" rx="6" fill="none" stroke="#c2a05e" stroke-width="3.5"/>
<path d="M18 26l9 7-9 7" fill="none" stroke="#c2a05e" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M31 42h14" stroke="#97742f" stroke-width="4" stroke-linecap="round"/>
</svg>
"""
