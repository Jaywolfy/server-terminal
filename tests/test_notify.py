import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_terminal import notify
from server_terminal.audit import AuditLog
from server_terminal.notify import Notifier, NotifySettings, new_topic, validate_server


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "daten" / "notify.json"
        self.settings = NotifySettings(self.path)

    def test_standard_ist_aus_mit_sinnvollen_ereignissen(self):
        data = self.settings.read()

        self.assertFalse(data["aktiv"])
        self.assertEqual(data["server"], "https://ntfy.sh")
        self.assertTrue(data["ereignisse"]["ssh_neue_adresse"])
        self.assertTrue(data["ereignisse"]["ssh_passwort"])
        self.assertFalse(data["ereignisse"]["ssh_alle"])

    def test_speichern_und_lesen(self):
        self.settings.save(True, "https://ntfy.example.de/", "server-abcdefgh12345", {"ssh_alle": True, "terminal": False})

        data = self.settings.read()

        self.assertEqual(
            (data["aktiv"], data["server"], data["topic"]), (True, "https://ntfy.example.de", "server-abcdefgh12345")
        )
        self.assertTrue(data["ereignisse"]["ssh_alle"])
        self.assertFalse(data["ereignisse"]["terminal"])

    def test_datei_ist_nur_fuer_den_besitzer_lesbar(self):
        self.settings.save(False, "https://ntfy.sh", "", {})

        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_aktivieren_braucht_ein_thema(self):
        with self.assertRaisesRegex(ValueError, "Thema"):
            self.settings.save(True, "https://ntfy.sh", "", {})

    def test_ungueltiges_thema(self):
        for topic in ("kurz", "mit leerzeichen!", "x" * 65, "ä" * 10):
            with self.assertRaises(ValueError, msg=topic):
                self.settings.save(False, "https://ntfy.sh", topic, {})

    def test_serveradresse_wird_geprueft(self):
        self.assertEqual(validate_server(" https://ntfy.sh/ "), "https://ntfy.sh")
        for server in (
            "ntfy.sh",
            "ftp://ntfy.sh",
            "https://",
            "https://ntfy.sh/pfad",
            "https://ntfy.sh?x=1",
            "javascript:alert(1)",
        ):
            with self.assertRaises(ValueError, msg=server):
                validate_server(server)

    def test_neues_thema_ist_lang_und_zufaellig(self):
        first, second = new_topic(), new_topic()

        self.assertNotEqual(first, second)
        self.assertTrue(notify.TOPIC.match(first))
        self.assertGreaterEqual(len(first), 20)

    def test_unbekannte_ereignisse_werden_ignoriert(self):
        self.settings.save(False, "https://ntfy.sh", "", {"gibt_es_nicht": True})

        self.assertNotIn("gibt_es_nicht", self.settings.read()["ereignisse"])

    def test_kaputte_datei_wird_gemeldet(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "notify.json"):
            self.settings.read()


class PublishTest(unittest.TestCase):
    def test_sendet_json_an_den_server(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            notify.publish("https://ntfy.sh", "thema12345", "Titel", "Text", 5, ("warning",), "https://x.de/sicherheit")

        request = urlopen.call_args.args[0]
        self.assertEqual((request.full_url, request.get_method()), ("https://ntfy.sh", "POST"))
        body = __import__("json").loads(request.data)
        self.assertEqual(
            body,
            {
                "topic": "thema12345",
                "title": "Titel",
                "message": "Text",
                "priority": 5,
                "tags": ["warning"],
                "click": "https://x.de/sicherheit",
            },
        )


class NotifierTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = NotifySettings(Path(self._tmp.name) / "notify.json")
        self.audit = AuditLog(Path(self._tmp.name) / "audit.sqlite")
        self.addCleanup(self.audit.close)
        self.sent = []
        self.notifier = Notifier(
            self.settings, self.audit, send=lambda *args: self.sent.append(args), click_url="https://x.de/sicherheit"
        )

    async def test_ohne_aktivierung_wird_nichts_gesendet(self):
        self.settings.save(False, "https://ntfy.sh", "server-abcdefgh12345", {})

        self.assertFalse(await self.notifier.notify("ssh_neue_adresse", "T", "M"))
        self.assertEqual(self.sent, [])

    async def test_aktiv_und_ereignis_an_sendet(self):
        self.settings.save(True, "https://ntfy.sh", "server-abcdefgh12345", {})

        self.assertTrue(await self.notifier.notify("ssh_neue_adresse", "Titel", "Text", 5, ("warning",)))

        self.assertEqual(
            self.sent, [("https://ntfy.sh", "server-abcdefgh12345", "Titel", "Text", 5, ("warning",), "https://x.de/sicherheit")]
        )

    async def test_ausgeschaltetes_ereignis_sendet_nicht(self):
        self.settings.save(True, "https://ntfy.sh", "server-abcdefgh12345", {})

        self.assertFalse(await self.notifier.notify("ssh_alle", "T", "M"))

    async def test_netzfehler_geht_ins_protokoll_und_wirft_nicht(self):
        self.settings.save(True, "https://ntfy.sh", "server-abcdefgh12345", {})

        def broken(*args):
            raise OSError("Netz weg")

        notifier = Notifier(self.settings, self.audit, send=broken)

        self.assertFalse(await notifier.notify("terminal", "T", "M"))
        self.assertEqual(self.audit.recent()[0]["ereignis"], "benachrichtigung_fehlgeschlagen")
        self.assertIn("Netz weg", self.audit.recent()[0]["detail"])

    async def test_test_sendet_auch_bei_ausgeschalteten_ereignissen(self):
        self.settings.save(False, "https://ntfy.sh", "server-abcdefgh12345", {})

        await self.notifier.test()

        self.assertEqual(len(self.sent), 1)

    async def test_test_ohne_thema(self):
        with self.assertRaisesRegex(ValueError, "Thema"):
            await self.notifier.test()


if __name__ == "__main__":
    unittest.main()
