import gzip
import tempfile
import unittest
from pathlib import Path

from server_terminal import updates

UPGRADABLE = """Listing... Done
docker-ce/resolute 5:29.9.2-1~ubuntu.26.04~resolute amd64 [upgradable from: 5:29.9.1-1~ubuntu.26.04~resolute]
libssl3/resolute-security 3.5.1-1ubuntu1.2 amd64 [upgradable from: 3.5.1-1ubuntu1]
tmux/resolute-updates 3.6a-2ubuntu0.2 amd64 [upgradable from: 3.6a-2ubuntu0.1]
N: There is 1 additional version.
"""
HISTORY = """Start-Date: 2026-10-23  06:53:50
Commandline: /usr/bin/unattended-upgrade
Upgrade: distro-info-data:amd64 (0.72, 2026.10.12)
End-Date: 2026-10-23  06:53:54

Start-Date: 2026-10-23  12:16:02
Commandline: apt-get -y dist-upgrade
Upgrade: containerd.io:amd64 (2.4.2-2, 2.4.2-3), docker-ce:amd64 (5:29.9.1-1, 5:29.9.2-1), tmux:amd64 (3.6a-2, 3.6a-3)
End-Date: 2026-10-23  12:17:07

Start-Date: 2026-10-24  01:00:00
Commandline: apt-get autoremove
Remove: oldpkg:amd64 (1.0)
End-Date: 2026-10-24  01:00:05
"""


class UpgradableTest(unittest.TestCase):
    def test_pakete_mit_sicherheitsupdates_zuerst(self):
        packages = updates.parse_upgradable(UPGRADABLE)

        self.assertEqual([p["name"] for p in packages], ["libssl3", "docker-ce", "tmux"])
        self.assertTrue(packages[0]["sicherheit"])
        self.assertFalse(packages[1]["sicherheit"])
        self.assertEqual(
            (packages[1]["alt"], packages[1]["neu"]), ("5:29.9.1-1~ubuntu.26.04~resolute", "5:29.9.2-1~ubuntu.26.04~resolute")
        )

    def test_leere_ausgabe(self):
        self.assertEqual(updates.parse_upgradable("Listing... Done\n"), [])


class HistoryTest(unittest.TestCase):
    def test_laeufe_neueste_zuerst_mit_paketnamen(self):
        runs = updates.parse_history(HISTORY)

        self.assertEqual(runs[0]["start"], "2026-10-24 01:00:00")
        second = runs[1]
        self.assertEqual(second["pakete"], ["containerd.io", "docker-ce", "tmux"])
        self.assertEqual(second["anzahl"], 3)
        self.assertEqual(second["befehl"], "apt-get -y dist-upgrade")

    def test_laeufe_ohne_aenderung_werden_beim_lesen_ausgelassen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.log"
            path.write_text(HISTORY, encoding="utf-8")

            runs = updates.read_history([path])

        self.assertEqual([run["start"] for run in runs], ["2026-10-23 12:16:02", "2026-10-23 06:53:50"])

    def test_gepackte_alte_protokolle_werden_gelesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            current, old = Path(tmp) / "history.log", Path(tmp) / "history.log.1.gz"
            current.write_text("", encoding="utf-8")
            with gzip.open(old, "wt", encoding="utf-8") as handle:
                handle.write(HISTORY)

            runs = updates.read_history([current, old])

        self.assertEqual(len(runs), 2)

    def test_fehlende_datei(self):
        self.assertEqual(updates.read_history([Path("/nicht/da/history.log")]), [])

    def test_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.log"
            path.write_text(HISTORY, encoding="utf-8")

            self.assertEqual(len(updates.read_history([path], limit=1)), 1)


class UpdateJobTest(unittest.IsolatedAsyncioTestCase):
    async def test_updatebefehle_laufen_nicht_interaktiv(self):
        self.assertEqual(updates.UPDATE_STEPS[0], ["apt-get", "update"])
        self.assertIn("dist-upgrade", updates.UPDATE_STEPS[1])
        self.assertIn("-y", updates.UPDATE_STEPS[1])
        self.assertEqual(updates.UPDATE_ENV["DEBIAN_FRONTEND"], "noninteractive")


if __name__ == "__main__":
    unittest.main()
