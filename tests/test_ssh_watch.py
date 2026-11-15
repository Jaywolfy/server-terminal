import tempfile
import unittest
from pathlib import Path

from server_terminal.ssh_watch import KnownAddresses, SshWatcher, parse_accepted

KEY = "2026-10-23T11:00:00+00:00 ubuntu sshd-session[5]: Accepted publickey for root from 192.0.2.44 port 54764 ssh2"
PASSWORD = "2026-10-23T11:05:00+00:00 ubuntu sshd-session[6]: Accepted password for root from 198.51.100.9 port 4000 ssh2"
FAILED = "2026-10-23T11:06:00+00:00 ubuntu sshd-session[7]: Failed password for root from 198.51.100.9 port 4001 ssh2"
OTHER = (
    "2026-10-23T11:00:01+00:00 ubuntu sshd-session[5]: pam_unix(sshd:session): session opened for user root(uid=0) by root(uid=0)"
)


class FakeNotifier:
    def __init__(self, enabled=("ssh_neue_adresse", "ssh_passwort")):
        self.enabled = set(enabled)
        self.sent = []

    async def notify(self, event, title, message, priority=3, tags=()):
        if event not in self.enabled:
            return False
        self.sent.append((event, title, message, priority))
        return True


class ParseTest(unittest.TestCase):
    def test_erfolgreiche_anmeldung(self):
        event = parse_accepted(KEY)

        self.assertEqual((event["user"], event["ip"], event["method"]), ("root", "192.0.2.44", "publickey"))

    def test_andere_zeilen_werden_ignoriert(self):
        self.assertIsNone(parse_accepted(FAILED))
        self.assertIsNone(parse_accepted(OTHER))
        self.assertIsNone(parse_accepted(""))


class WatcherTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.known = KnownAddresses(Path(self._tmp.name) / "bekannt.sqlite")
        self.addCleanup(self.known.close)
        self.now = 1_000_000.0
        self.notifier = FakeNotifier()

    def watcher(self, history="", notifier=None, lookup=None, follow=None):
        return SshWatcher(
            notifier or self.notifier,
            self.known,
            lookup or (lambda ip: {"land": "Deutschland", "stadt": "Frankfurt", "flagge": "🇩🇪", "anbieter": "Telekom"}),
            lambda: history,
            follow or (lambda: self._empty()),
            clock=lambda: self.now,
        )

    async def _empty(self):
        return
        yield  # pragma: no cover

    async def test_neue_adresse_loest_eine_dringende_nachricht_aus(self):
        watcher = self.watcher()

        sent = await watcher.handle_line(KEY)

        self.assertTrue(sent)
        event, title, message, priority = self.notifier.sent[0]
        self.assertEqual((event, priority), ("ssh_neue_adresse", 5))
        self.assertIn("unbekannte Adresse", title)
        self.assertIn("192.0.2.44", message)
        self.assertIn("Frankfurt", message)
        self.assertIn("per Schlüssel", message)

    async def test_bekannte_adresse_loest_nichts_aus(self):
        watcher = self.watcher()
        await watcher.handle_line(KEY)
        self.now += 1000

        sent = await watcher.handle_line(KEY)

        self.assertFalse(sent)
        self.assertEqual(len(self.notifier.sent), 1)

    async def test_bekannte_adresse_mit_alle_anmeldungen_an(self):
        watcher = self.watcher(notifier=FakeNotifier(("ssh_neue_adresse", "ssh_alle")))
        await watcher.handle_line(KEY)
        self.now += 1000

        await watcher.handle_line(KEY)

        self.assertEqual([s[0] for s in watcher.notifier.sent], ["ssh_neue_adresse", "ssh_alle"])

    async def test_wiederholung_innerhalb_zwei_minuten_wird_unterdrueckt(self):
        watcher = self.watcher(notifier=FakeNotifier(("ssh_neue_adresse", "ssh_alle")))
        await watcher.handle_line(KEY)
        self.now += 30

        await watcher.handle_line(KEY)

        self.assertEqual(len(watcher.notifier.sent), 1)

    async def test_passwort_anmeldung_ist_immer_dringend(self):
        watcher = self.watcher()
        await watcher.handle_line(KEY)
        self.known.touch("198.51.100.9", self.now)  # Adresse ist bekannt

        await watcher.handle_line(PASSWORD)

        event, title, message, priority = self.notifier.sent[-1]
        self.assertEqual((event, priority), ("ssh_passwort", 5))
        self.assertIn("mit Passwort", message)

    async def test_fehlgeschlagene_versuche_und_sitzungszeilen_melden_nichts(self):
        watcher = self.watcher()

        self.assertFalse(await watcher.handle_line(FAILED))
        self.assertFalse(await watcher.handle_line(OTHER))
        self.assertEqual(self.notifier.sent, [])

    async def test_herkunft_ist_optional(self):
        def broken(ip):
            raise OSError("keine Verbindung")

        watcher = self.watcher(lookup=broken)

        await watcher.handle_line(KEY)

        self.assertIn("192.0.2.44", self.notifier.sent[0][2])

    async def test_erster_start_fuellt_bekannte_adressen_ohne_nachrichten(self):
        history = "\n".join([KEY, PASSWORD, FAILED])
        watcher = self.watcher(history=history)

        added = await watcher.seed()

        self.assertEqual(added, 2)
        self.assertTrue(self.known.known("192.0.2.44"))
        self.assertTrue(self.known.known("198.51.100.9"))
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(await watcher.seed(), 0)  # nur einmal

    async def test_gescheiterte_adressen_gelten_nicht_als_bekannt(self):
        watcher = self.watcher(history=FAILED)

        await watcher.seed()

        self.assertFalse(self.known.known("198.51.100.9"))

    async def test_lauf_verarbeitet_zeilen_aus_dem_journal(self):
        async def follow():
            yield OTHER
            yield KEY

        watcher = self.watcher(follow=follow)
        watcher_task = watcher.start

        await watcher.seed()
        async for line in follow():
            await watcher.handle_line(line)

        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIsNotNone(watcher_task)


if __name__ == "__main__":
    unittest.main()
