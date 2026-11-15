import asyncio
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from server_terminal import totp
from server_terminal.config import Config
from server_terminal.web import create_app

PASSWORD = "ein-sehr-langes-passwort"
CSRF = {"X-Requested-With": "server-terminal"}


class WebTestCase(unittest.IsolatedAsyncioTestCase):
    allowed = frozenset({"127.0.0.1", "terminal.example.de"})

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_calls = []

        async def fake_run(*command, **kwargs):
            self.run_calls.append(command)
            return 0, ""

        self.config = Config(
            data_dir=Path(self._tmp.name),
            allowed_hosts=self.allowed,
            persistent_terminal=False,
            geo_enabled=False,
            watch_ssh=False,
        )
        self.app = create_app(self.config, run=fake_run)
        self.state = self.app["app"]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def create_account(self):
        self.secret = totp.new_secret()
        self.state.account.create("jason", PASSWORD, self.secret)
        self.state.setup_code = None

    def next_code(self):
        """Ein Code, den es noch nicht gab (die Konten verbrauchen jeden Code)."""
        step = totp.current_step(time.time())
        last = self.state.account._read()["letzter_totp_schritt"]
        for candidate in (step - 1, step, step + 1):
            if candidate > last:
                return totp.code_at(self.secret, candidate)
        raise AssertionError("kein freier Code")

    async def login(self):
        self.create_account()
        response = await self.client.post(
            "/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": self.next_code()}, allow_redirects=False
        )
        self.assertEqual(response.status, 303)
        return response

    async def post(self, path, body=None, headers=CSRF):
        return await self.client.post(path, json=body or {}, headers=headers)


class ProtectionTest(WebTestCase):
    async def test_fremder_host_wird_abgewiesen(self):
        response = await self.client.get("/login", headers={"Host": "boese.example"})

        self.assertEqual(response.status, 403)

    async def test_konfigurierter_hostname_ist_erlaubt(self):
        self.create_account()

        response = await self.client.get("/login", headers={"Host": "terminal.example.de"})

        self.assertEqual(response.status, 200)

    async def test_fremder_ursprung_wird_abgewiesen(self):
        self.create_account()

        response = await self.client.post("/login", data={}, headers={"Origin": "https://boese.example"})

        self.assertEqual(response.status, 403)

    async def test_referrer_policy_laesst_den_browser_den_ursprung_senden(self):
        self.create_account()

        response = await self.client.get("/login")

        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")

    async def test_formular_mit_eigenem_ursprung_wird_angenommen(self):
        origin = {"Origin": "https://terminal.example.de", "Host": "terminal.example.de"}

        response = await self.client.post(
            "/setup",
            data={"einrichtungscode": self.state.setup_code, "benutzer": "jason", "passwort": PASSWORD, "wiederholt": PASSWORD},
            headers=origin,
        )

        self.assertEqual(response.status, 200)
        self.assertIn("Zwei-Faktor", await response.text())

    async def test_ursprung_null_wird_abgewiesen(self):
        self.create_account()

        response = await self.client.post("/login", data={}, headers={"Origin": "null"})

        self.assertEqual(response.status, 403)

    async def test_ohne_anmeldung_leitet_auf_die_anmeldung(self):
        self.create_account()

        for path in ("/", "/terminal", "/docker", "/sicherheit"):
            response = await self.client.get(path, allow_redirects=False)
            self.assertEqual((response.status, response.headers["Location"]), (303, "/login"), path)

    async def test_schnittstelle_ohne_anmeldung_gibt_401(self):
        self.create_account()

        for path in (
            "/api/overview",
            "/api/updates",
            "/api/docker",
            "/api/logins",
            "/api/audit",
            "/api/commands",
            "/api/metrics",
        ):
            self.assertEqual((await self.client.get(path)).status, 401, path)
        self.assertEqual((await self.post("/api/updates/run")).status, 401)

    async def test_terminal_ohne_anmeldung_gibt_401(self):
        self.create_account()

        self.assertEqual((await self.client.get("/ws/terminal")).status, 401)

    async def test_statische_dateien_sind_oeffentlich_aber_nur_dort(self):
        self.create_account()

        self.assertEqual((await self.client.get("/static/css/style.css")).status, 200)
        self.assertEqual((await self.client.get("/static/favicon.svg")).status, 200)
        for path in ("/static/..%2faccount.json", "/static/%2e%2e/account.json", "/static/../account.json"):
            response = await self.client.get(path, allow_redirects=False)
            self.assertNotEqual(response.status, 200, path)
            self.assertNotIn("benutzer", await response.text(), path)

    async def test_sicherheitskopfzeilen(self):
        self.create_account()

        response = await self.client.get("/login")
        csp = response.headers["Content-Security-Policy"]

        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline'; style", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_seiten_enthalten_keine_skripte_im_text(self):
        await self.login()

        for path in ("/", "/terminal", "/docker", "/sicherheit", "/login"):
            body = await (await self.client.get(path)).text()
            self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)", body), path)
            self.assertNotRegex(body, r"\bon(click|load|error)=", path)


class SetupTest(WebTestCase):
    async def test_ohne_konto_leitet_alles_auf_die_einrichtung(self):
        for path in ("/", "/login", "/api/overview"):
            response = await self.client.get(path, allow_redirects=False)
            if path.startswith("/api"):
                self.assertEqual(response.status, 401)
            else:
                self.assertEqual((response.status, response.headers["Location"]), (303, "/setup"), path)

    async def test_einrichtung_mit_falschem_code(self):
        response = await self.client.post(
            "/setup", data={"einrichtungscode": "falsch", "benutzer": "jason", "passwort": PASSWORD, "wiederholt": PASSWORD}
        )

        self.assertEqual(response.status, 400)
        self.assertIn("Einrichtungscode stimmt nicht", await response.text())
        self.assertFalse(self.state.account.exists())

    async def test_einrichtung_prueft_das_passwort(self):
        for password, repeated, message in [("kurz", "kurz", "mindestens"), (PASSWORD, PASSWORD + "x", "nicht gleich")]:
            response = await self.client.post(
                "/setup",
                data={
                    "einrichtungscode": self.state.setup_code,
                    "benutzer": "jason",
                    "passwort": password,
                    "wiederholt": repeated,
                },
            )
            self.assertEqual(response.status, 400)
            self.assertIn(message, await response.text())

    async def test_ganzer_ablauf_bis_zur_anmeldung(self):
        response = await self.client.post(
            "/setup",
            data={"einrichtungscode": self.state.setup_code, "benutzer": "jason", "passwort": PASSWORD, "wiederholt": PASSWORD},
        )
        body = await response.text()
        token = re.search(r'name="token" value="([^"]+)"', body).group(1)
        secret = re.search(r"<code>([A-Z2-7]+)</code>", body).group(1)
        self.assertIn("<svg", body)
        self.assertFalse(self.state.account.exists())

        wrong = await self.client.post("/setup/bestaetigen", data={"token": token, "code": "000000"})
        self.assertEqual(wrong.status, 400)
        self.assertFalse(self.state.account.exists())

        code = totp.code_at(secret, totp.current_step(time.time()))
        ok = await self.client.post("/setup/bestaetigen", data={"token": token, "code": code}, allow_redirects=False)

        self.assertEqual((ok.status, ok.headers["Location"]), (303, "/"))
        self.assertTrue(self.state.account.exists())
        self.assertIsNone(self.state.setup_code)
        self.assertEqual((await self.client.get("/api/overview")).status, 200)

    async def test_nach_der_einrichtung_ist_sie_nicht_mehr_moeglich(self):
        self.create_account()

        response = await self.client.get("/setup", allow_redirects=False)

        self.assertEqual((response.status, response.headers["Location"]), (303, "/login"))

    async def test_abgelaufene_einrichtung(self):
        response = await self.client.post("/setup/bestaetigen", data={"token": "unbekannt", "code": "123456"})

        self.assertEqual(response.status, 400)
        self.assertIn("abgelaufen", await response.text())

    async def test_zu_viele_falsche_einrichtungscodes_sperren(self):
        for _ in range(5):
            await self.client.post("/setup", data={"einrichtungscode": "falsch"})

        response = await self.client.post(
            "/setup",
            data={"einrichtungscode": self.state.setup_code, "benutzer": "jason", "passwort": PASSWORD, "wiederholt": PASSWORD},
        )

        self.assertEqual(response.status, 429)


class LoginTest(WebTestCase):
    async def test_anmeldung_mit_passwort_und_code(self):
        response = await self.login()

        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
        self.assertEqual((await self.client.get("/")).status, 200)

    async def test_falsches_passwort(self):
        self.create_account()

        response = await self.client.post("/login", data={"benutzer": "jason", "passwort": "falsch", "code": self.next_code()})

        self.assertEqual(response.status, 401)
        self.assertNotIn("Set-Cookie", response.headers)

    async def test_falscher_code(self):
        self.create_account()

        response = await self.client.post("/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": "000000"})

        self.assertEqual(response.status, 401)

    async def test_fehlermeldung_verraet_nicht_was_falsch_war(self):
        self.create_account()
        wrong_password = await (
            await self.client.post("/login", data={"benutzer": "jason", "passwort": "x", "code": self.next_code()})
        ).text()
        wrong_code = await (
            await self.client.post("/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": "000000"})
        ).text()

        self.assertIn("Anmeldung fehlgeschlagen", wrong_password)
        self.assertIn("Anmeldung fehlgeschlagen", wrong_code)

    async def test_ein_code_gilt_nur_einmal(self):
        self.create_account()
        code = self.next_code()
        first = await self.client.post(
            "/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": code}, allow_redirects=False
        )

        second = await self.client.post(
            "/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": code}, allow_redirects=False
        )

        self.assertEqual((first.status, second.status), (303, 401))

    async def test_nach_fuenf_fehlversuchen_gesperrt(self):
        self.create_account()
        for _ in range(5):
            await self.client.post("/login", data={"benutzer": "jason", "passwort": "falsch", "code": "000000"})

        response = await self.client.post("/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": self.next_code()})

        self.assertEqual(response.status, 429)

    async def test_abmelden_loescht_das_cookie(self):
        await self.login()

        response = await self.client.post("/logout", allow_redirects=False)

        self.assertEqual((response.status, response.headers["Location"]), (303, "/login"))
        self.assertEqual((await self.client.get("/api/overview")).status, 401)

    async def test_gefaelschtes_cookie(self):
        self.create_account()

        for cookie in ("session=9999999999.abc", "session=", "session=x.y"):
            response = await self.client.get("/api/overview", headers={"Cookie": cookie})
            self.assertEqual(response.status, 401, cookie)

    async def test_anmeldungen_stehen_im_protokoll(self):
        self.create_account()
        await self.client.post("/login", data={"benutzer": "jason", "passwort": "falsch", "code": "000000"})
        await self.client.post("/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": self.next_code()})

        events = [e["ereignis"] for e in self.state.audit.recent()]

        self.assertEqual(events[:2], ["angemeldet", "anmeldung_fehlgeschlagen"])

    async def test_client_adresse_nur_von_vertrauten_proxys(self):
        self.create_account()
        request = mock.Mock(remote="172.17.0.2", headers={"X-Real-IP": "203.0.113.9"})
        stranger = mock.Mock(remote="203.0.113.50", headers={"X-Real-IP": "6.6.6.6"})

        self.assertEqual(self.state.client_ip(request), "203.0.113.9")
        self.assertEqual(self.state.client_ip(stranger), "203.0.113.50")


class ApiTest(WebTestCase):
    async def test_post_ohne_kopfzeile_wird_abgewiesen(self):
        await self.login()

        response = await self.post("/api/commands", {"name": "x", "befehl": "y"}, headers={})

        self.assertEqual(response.status, 403)

    async def test_uebersicht(self):
        await self.login()

        data = await (await self.client.get("/api/overview")).json()

        for key in ("zeit", "hostname", "laufzeit_s", "last", "festplatten", "erhoeht", "version"):
            self.assertIn(key, data)
        self.assertTrue(data["erhoeht"])

    async def test_metriken_alle_ansichten(self):
        await self.login()

        for view in ("1min", "5min", "1h", "12h", "1d", "7d"):
            data = await (await self.client.get(f"/api/metrics?ansicht={view}")).json()
            self.assertEqual(data["ansicht"], view)
            self.assertIn("punkte", data)
        self.assertEqual((await self.client.get("/api/metrics?ansicht=10min")).status, 400)

    async def test_befehle_anlegen_und_loeschen(self):
        await self.login()

        created = await self.post("/api/commands", {"gruppe": "Test", "name": "Hallo", "befehl": "echo hallo"})
        entry = await created.json()
        listing = await (await self.client.get("/api/commands")).json()
        deleted = await self.client.delete(f"/api/commands/{entry['id']}", headers=CSRF)

        self.assertEqual(created.status, 201)
        self.assertIn(entry["id"], [c["id"] for c in listing["befehle"]])
        self.assertEqual(deleted.status, 200)
        self.assertEqual((await self.client.delete("/api/commands/std-0", headers=CSRF)).status, 404)
        self.assertEqual((await self.post("/api/commands", {"name": "", "befehl": ""})).status, 400)

    async def test_anmeldeversuche_werden_kurz_zwischengespeichert(self):
        await self.login()
        journal = "2026-10-23T10:00:02+00:00 u sshd-session[1]: Failed password for root from 203.0.113.5 port 4000 ssh2\n"
        with mock.patch("server_terminal.logins.read_journal", mock.AsyncMock(return_value=journal)) as read:
            first = await (await self.client.get("/api/logins?stunden=24")).json()
            second = await (await self.client.get("/api/logins?stunden=24")).json()
            await self.client.get("/api/logins?stunden=1")

        self.assertEqual((first["fehlgeschlagen"], first["ips"]), (1, 1))
        self.assertEqual(first, second)
        self.assertEqual(read.await_count, 2)

    async def test_protokoll(self):
        await self.login()

        data = await (await self.client.get("/api/audit")).json()

        self.assertEqual(data["eintraege"][0]["ereignis"], "angemeldet")

    async def test_docker_namen_werden_geprueft(self):
        await self.login()

        for name in ("a;b", "-x", "a%20b"):
            response = await self.post(f"/api/docker/{name}/restart")
            self.assertEqual(response.status, 400, name)
        self.assertEqual((await self.post("/api/docker/web/rm")).status, 400)
        self.assertEqual(self.run_calls, [])

    async def test_docker_aktion_ruft_docker_auf_und_wird_protokolliert(self):
        await self.login()
        with mock.patch("server_terminal.docker_ctl.run", mock.AsyncMock(return_value=(0, "web\n"))) as run:
            response = await self.post("/api/docker/web/restart")

        self.assertEqual(response.status, 200)
        run.assert_awaited_once_with("docker", "restart", "web", timeout=90)
        self.assertEqual(self.state.audit.recent()[0]["ereignis"], "docker_restart")


class ElevationTest(WebTestCase):
    DANGEROUS = [
        ("/api/updates/run", {}),
        ("/api/reboot", {"bestaetigung": "NEUSTART"}),
        ("/api/docker/web/restart", {}),
        ("/api/unban", {"ip": "1.2.3.4"}),
    ]

    async def drop_elevation(self):
        """Eine Sitzung ohne erhöhte Stufe, wie nach 15 Minuten."""
        token = self.state.account.new_session(time.time(), elevated=False)
        self.client.session.cookie_jar.clear()
        self.client.session.cookie_jar.update_cookies({"session": token}, self.client.make_url("/"))

    async def test_gefaehrliche_aktionen_brauchen_die_erhoehte_stufe(self):
        await self.login()
        await self.drop_elevation()

        for path, body in self.DANGEROUS:
            response = await self.post(path, body)
            data = await response.json()
            self.assertEqual(response.status, 403, path)
            self.assertTrue(data["erhoehen"], path)
        self.assertEqual(self.run_calls, [])

    async def test_terminal_braucht_die_erhoehte_stufe(self):
        await self.login()
        await self.drop_elevation()

        response = await self.client.get("/ws/terminal")

        self.assertEqual(response.status, 403)

    async def test_freischaltung_mit_frischem_code(self):
        await self.login()
        await self.drop_elevation()
        self.assertFalse((await (await self.client.get("/api/overview")).json())["erhoeht"])

        response = await self.post("/api/elevate", {"code": self.next_code()})

        self.assertEqual(response.status, 200)
        self.assertTrue((await (await self.client.get("/api/overview")).json())["erhoeht"])

    async def test_freischaltung_mit_falschem_code(self):
        await self.login()
        await self.drop_elevation()

        response = await self.post("/api/elevate", {"code": "000000"})

        self.assertEqual(response.status, 403)
        self.assertFalse((await (await self.client.get("/api/overview")).json())["erhoeht"])

    async def test_freischaltung_wird_gesperrt(self):
        await self.login()
        await self.drop_elevation()
        for _ in range(5):
            await self.post("/api/elevate", {"code": "000000"})

        self.assertEqual((await self.post("/api/elevate", {"code": self.next_code()})).status, 429)

    async def test_neustart_braucht_die_bestaetigung(self):
        await self.login()

        self.assertEqual((await self.post("/api/reboot", {"bestaetigung": "ja"})).status, 400)
        self.assertEqual(self.run_calls, [])
        ok = await self.post("/api/reboot", {"bestaetigung": "NEUSTART"})

        self.assertEqual(ok.status, 200)
        self.assertEqual(self.run_calls[0][0], "systemd-run")
        self.assertIn("reboot", self.run_calls[0])

    async def test_updates_starten_und_status_abfragen(self):
        await self.login()
        with mock.patch("server_terminal.updates.UPDATE_STEPS", [["printf", "aktualisiert\\n"]]):
            response = await self.post("/api/updates/run")
            await self.state.job.wait()

        status = await (await self.client.get("/api/job")).json()

        self.assertEqual(response.status, 200)
        self.assertTrue(status["erfolg"])
        self.assertIn("aktualisiert", status["zeilen"])
        self.assertEqual(self.state.audit.recent()[0]["ereignis"], "updates_gestartet")

    async def test_zweiter_updatelauf_waehrend_einer_laeuft(self):
        await self.login()
        with mock.patch("server_terminal.updates.UPDATE_STEPS", [["sleep", "1"]]):
            first = await self.post("/api/updates/run")
            second = await self.post("/api/updates/run")
            await self.state.job.wait()

        self.assertEqual((first.status, second.status), (200, 409))


class NotifyApiTest(WebTestCase):
    async def test_ohne_anmeldung_gesperrt(self):
        self.create_account()

        self.assertEqual((await self.client.get("/api/notify")).status, 401)
        self.assertEqual((await self.post("/api/notify/test")).status, 401)

    async def test_standardzustand(self):
        await self.login()

        data = await (await self.client.get("/api/notify")).json()

        self.assertFalse(data["aktiv"])
        self.assertEqual(data["topic"], "")
        self.assertIn("ssh_neue_adresse", data["beschreibung"])
        self.assertEqual(data["qr"], "")

    async def test_thema_erzeugen_aktivieren_und_qr(self):
        await self.login()

        response = await self.post(
            "/api/notify", {"aktiv": False, "server": "https://ntfy.sh", "neues_thema": True, "ereignisse": {}}
        )
        data = await response.json()
        saved = await self.post(
            "/api/notify", {"aktiv": True, "server": "https://ntfy.sh", "topic": data["topic"], "ereignisse": {"ssh_alle": True}}
        )
        final = await saved.json()

        self.assertEqual(response.status, 200)
        self.assertTrue(data["topic"].startswith("server-"))
        self.assertTrue(final["aktiv"])
        self.assertTrue(final["ereignisse"]["ssh_alle"])
        self.assertIn("<svg", final["qr"])
        self.assertEqual(self.state.audit.recent()[0]["ereignis"], "benachrichtigungen_geaendert")

    async def test_aenderungen_brauchen_die_erhoehte_stufe(self):
        await self.login()
        token = self.state.account.new_session(time.time(), elevated=False)
        self.client.session.cookie_jar.clear()
        self.client.session.cookie_jar.update_cookies({"session": token}, self.client.make_url("/"))

        response = await self.post("/api/notify", {"aktiv": False, "server": "https://ntfy.sh", "neues_thema": True})

        self.assertEqual(response.status, 403)
        self.assertEqual(self.state.notify_settings.read()["topic"], "")

    async def test_ungueltige_eingaben(self):
        await self.login()

        for body in (
            {"aktiv": True, "server": "https://ntfy.sh"},  # aktiv ohne Thema
            {"aktiv": False, "server": "javascript:alert(1)", "topic": ""},
            {"aktiv": False, "server": "https://ntfy.sh", "topic": "zu kurz"},
        ):
            self.assertEqual((await self.post("/api/notify", body)).status, 400, body)

    async def test_testnachricht(self):
        await self.login()
        self.state.notify_settings.save(True, "https://ntfy.sh", "server-abcdefgh12345", {})
        sent = []
        self.state.notifier.send = lambda *args: sent.append(args)

        response = await self.post("/api/notify/test")

        self.assertEqual(response.status, 200)
        self.assertEqual(sent[0][1], "server-abcdefgh12345")

    async def test_testnachricht_ohne_thema_und_bei_netzfehler(self):
        await self.login()
        self.assertEqual((await self.post("/api/notify/test")).status, 400)
        self.state.notify_settings.save(True, "https://ntfy.sh", "server-abcdefgh12345", {})

        def broken(*args):
            raise OSError("Netz weg")

        self.state.notifier.send = broken

        response = await self.post("/api/notify/test")

        self.assertEqual(response.status, 502)
        self.assertIn("Netz weg", (await response.json())["fehler"])

    async def test_anmeldung_am_dashboard_loest_eine_nachricht_aus(self):
        self.create_account()
        self.state.notify_settings.save(True, "https://ntfy.sh", "server-abcdefgh12345", {})
        sent = []
        self.state.notifier.send = lambda *args: sent.append(args)

        await self.client.post(
            "/login", data={"benutzer": "jason", "passwort": PASSWORD, "code": self.next_code()}, allow_redirects=False
        )
        await asyncio.gather(*self.state._tasks)

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2], "Anmeldung am Server-Dashboard")

    async def test_terminal_oeffnen_loest_eine_nachricht_aus(self):
        await self.login()
        self.state.notify_settings.save(True, "https://ntfy.sh", "server-abcdefgh12345", {})
        sent = []
        self.state.notifier.send = lambda *args: sent.append(args)

        async with self.client.ws_connect("/ws/terminal?c=80&r=24"):
            await asyncio.sleep(0.2)
        await asyncio.gather(*self.state._tasks)

        self.assertIn("Terminal geöffnet", [s[2] for s in sent])

    async def test_seite_ist_erreichbar_und_ohne_skripte_im_text(self):
        await self.login()

        body = await (await self.client.get("/benachrichtigungen")).text()

        self.assertIn("notify.js", body)
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)", body))


class TerminalSocketTest(WebTestCase):
    async def test_terminal_ueber_websocket(self):
        await self.login()

        async with self.client.ws_connect("/ws/terminal?c=100&r=30") as socket:
            await socket.send_str(json.dumps({"t": "i", "d": "echo web-$((6*7))\n"}))
            collected = ""
            for _ in range(50):
                message = await socket.receive(timeout=5)
                collected += message.data if message.type == web.WSMsgType.TEXT else ""
                if "web-42" in collected:
                    break
            await socket.send_str(json.dumps({"t": "r", "c": 90, "r": 20}))

        self.assertIn("web-42", collected)
        events = [e["ereignis"] for e in self.state.audit.recent()]
        self.assertIn("terminal_geoeffnet", events)

    async def test_websocket_mit_fremdem_ursprung_wird_abgewiesen(self):
        await self.login()

        with self.assertRaises(WSServerHandshakeError) as caught:
            await self.client.ws_connect("/ws/terminal", headers={"Origin": "https://boese.example"})

        self.assertEqual(caught.exception.status, 403)

    async def test_hoechstens_drei_terminals(self):
        await self.login()
        self.state.sessions = 3

        response = await self.client.get("/ws/terminal")

        self.assertEqual(response.status, 429)


if __name__ == "__main__":
    unittest.main()
