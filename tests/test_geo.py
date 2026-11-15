import tempfile
import unittest
from pathlib import Path

from server_terminal.geo import GeoCache, flag, is_public


class HelperTest(unittest.TestCase):
    def test_oeffentliche_adressen(self):
        self.assertTrue(is_public("8.8.8.8"))
        for ip in ("10.0.0.1", "192.168.1.1", "172.17.0.1", "127.0.0.1", "::1", "kaputt", ""):
            self.assertFalse(is_public(ip), ip)

    def test_flagge(self):
        self.assertEqual(flag("DE"), "🇩🇪")
        self.assertEqual(flag("de"), "🇩🇪")
        self.assertEqual(flag(""), "")
        self.assertEqual(flag("XYZ"), "")


class GeoCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.calls = []
        self.now = 1_000_000.0

    def fetch(self, ips):
        self.calls.append(list(ips))
        return [
            {
                "status": "success",
                "query": ip,
                "country": "Deutschland",
                "countryCode": "DE",
                "regionName": "Berlin",
                "city": "Berlin",
                "isp": "X",
            }
            for ip in ips
        ]

    def cache(self, enabled=True, fetch=None):
        cache = GeoCache(Path(self._tmp.name) / "geo.sqlite", enabled, fetch or self.fetch, lambda: self.now)
        self.addCleanup(cache.close)
        return cache

    def test_abfrage_und_zwischenspeicher(self):
        cache = self.cache()

        first = cache.lookup(["8.8.8.8", "1.1.1.1"])
        second = cache.lookup(["8.8.8.8"])

        self.assertEqual(first["8.8.8.8"]["land"], "Deutschland")
        self.assertEqual(first["8.8.8.8"]["flagge"], "🇩🇪")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second["8.8.8.8"]["stadt"], "Berlin")

    def test_private_adressen_werden_nie_abgefragt(self):
        cache = self.cache()

        result = cache.lookup(["10.0.0.1", "192.168.0.5", "8.8.8.8"])

        self.assertEqual(self.calls, [["8.8.8.8"]])
        self.assertNotIn("10.0.0.1", result)

    def test_doppelte_adressen_werden_einmal_abgefragt(self):
        self.cache().lookup(["8.8.8.8", "8.8.8.8"])

        self.assertEqual(self.calls, [["8.8.8.8"]])

    def test_mehr_als_hundert_adressen_gehen_in_mehreren_paketen(self):
        ips = [f"8.8.{a}.{b}" for a in range(2) for b in range(1, 101)]

        self.cache().lookup(ips)

        self.assertEqual([len(call) for call in self.calls], [100, 100])

    def test_abgeschaltet_fragt_nichts_ab(self):
        cache = self.cache(enabled=False)

        self.assertEqual(cache.lookup(["8.8.8.8"]), {})
        self.assertEqual(self.calls, [])

    def test_fehlgeschlagene_antwort_wird_als_leer_gemerkt(self):
        cache = self.cache(fetch=lambda ips: [{"status": "fail", "query": ip} for ip in ips])

        result = cache.lookup(["8.8.8.8"])

        self.assertEqual(result["8.8.8.8"]["land"], "")

    def test_alter_eintrag_wird_neu_abgefragt(self):
        cache = self.cache()
        cache.lookup(["8.8.8.8"])
        self.now += 31 * 24 * 3600

        cache.lookup(["8.8.8.8"])

        self.assertEqual(len(self.calls), 2)

    def test_netzwerkfehler_wird_weitergegeben(self):
        def broken(ips):
            raise OSError("Netz weg")

        with self.assertRaises(OSError):
            self.cache(fetch=broken).lookup(["8.8.8.8"])

    def test_zwischenspeicher_ueberlebt_neustart(self):
        self.cache().lookup(["8.8.8.8"])

        again = GeoCache(Path(self._tmp.name) / "geo.sqlite", True, self.fetch, lambda: self.now)
        self.addCleanup(again.close)
        again.lookup(["8.8.8.8"])

        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
