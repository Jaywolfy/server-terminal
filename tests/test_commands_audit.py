import tempfile
import unittest
from pathlib import Path

from server_terminal.audit import AuditLog
from server_terminal.commands import DEFAULTS, CommandLibrary


class CommandLibraryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.library = CommandLibrary(Path(self._tmp.name) / "daten" / "c.sqlite")
        self.addCleanup(self.library.close)

    def test_vorgegebene_befehle_sind_da(self):
        commands = self.library.all()

        self.assertEqual(len(commands), len(DEFAULTS))
        self.assertTrue(all(not c["eigen"] for c in commands))
        self.assertTrue({"System", "Docker", "Netzwerk", "Sicherheit", "Updates", "Logs"} <= {c["gruppe"] for c in commands})

    def test_eigenen_befehl_anlegen_und_loeschen(self):
        entry = self.library.add("Backup", "Prüfen", "ls -la /root/backups")

        self.assertTrue(entry["eigen"])
        self.assertIn(entry["id"], {c["id"] for c in self.library.all()})
        self.assertTrue(self.library.delete(entry["id"]))
        self.assertNotIn(entry["id"], {c["id"] for c in self.library.all()})

    def test_leere_gruppe_wird_eigene(self):
        self.assertEqual(self.library.add("", "Name", "echo")["gruppe"], "Eigene")

    def test_ungueltige_eingaben(self):
        for args in [
            ("G", "", "echo"),
            ("G", "Name", "  "),
            ("G", "x" * 81, "echo"),
            ("g" * 41, "n", "echo"),
            ("G", "n", "x" * 2001),
        ]:
            with self.assertRaises(ValueError, msg=args):
                self.library.add(*args)

    def test_vorgegebene_befehle_lassen_sich_nicht_loeschen(self):
        self.assertFalse(self.library.delete("std-0"))
        self.assertFalse(self.library.delete("gibtsnicht"))

    def test_befehle_bleiben_nach_neuem_oeffnen(self):
        self.library.add("G", "Name", "echo hallo")

        again = CommandLibrary(Path(self._tmp.name) / "daten" / "c.sqlite")
        self.addCleanup(again.close)

        self.assertTrue(any(c["name"] == "Name" for c in again.all()))


class AuditLogTest(unittest.TestCase):
    def test_eintraege_neueste_zuerst(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            log = AuditLog(Path(tmp) / "a.sqlite", clock=lambda: now[0])
            log.add("angemeldet", "1.2.3.4")
            now[0] = 200.0
            log.add("updates_gestartet", "1.2.3.4", "detail")

            entries = log.recent()
            log.close()

        self.assertEqual([e["ereignis"] for e in entries], ["updates_gestartet", "angemeldet"])
        self.assertEqual(entries[0], {"zeit": 200.0, "ereignis": "updates_gestartet", "ip": "1.2.3.4", "detail": "detail"})

    def test_limit_und_laengenbegrenzung(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(Path(tmp) / "a.sqlite")
            for index in range(5):
                log.add("x", "ip", "d" * 1000 if index == 0 else "")

            self.assertEqual(len(log.recent(3)), 3)
            self.assertEqual(len(log.recent(10)[-1]["detail"]), 500)
            log.close()


if __name__ == "__main__":
    unittest.main()
