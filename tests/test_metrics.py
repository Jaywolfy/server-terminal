import tempfile
import unittest
from pathlib import Path

from server_terminal.metrics import (
    FIELDS,
    MetricsService,
    MetricsStore,
    Sampler,
    parse_cpu,
    parse_diskstats,
    parse_meminfo,
    parse_net,
)

STAT = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 100 0 50 800 50 0 0 0 0 0\n"
MEMINFO = "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nSwapTotal: 200 kB\nSwapFree: 150 kB\n"
NET = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0
  ens6: 1000 10 0 0 0 0 0 0 500 5 0 0 0 0 0 0
docker0: 777 7 0 0 0 0 0 0 777 7 0 0 0 0 0 0
veth12: 555 5 0 0 0 0 0 0 555 5 0 0 0 0 0 0
br-abc: 444 4 0 0 0 0 0 0 444 4 0 0 0 0 0 0
"""
DISKSTATS = """   7       0 loop0 10 0 100 0 0 0 0 0 0 0 0
 252       0 vda 100 0 2000 0 50 0 1000 0 0 0 0
 252       1 vda1 90 0 1800 0 40 0 900 0 0 0 0
"""


class ParserTest(unittest.TestCase):
    def test_cpu(self):
        self.assertEqual(parse_cpu(STAT), (1000, 850, 50))

    def test_speicher_und_swap(self):
        mem, swap = parse_meminfo(MEMINFO)

        self.assertAlmostEqual(mem, 60.0)
        self.assertAlmostEqual(swap, 25.0)

    def test_ohne_swap(self):
        self.assertEqual(parse_meminfo("MemTotal: 100 kB\nMemAvailable: 50 kB\n")[1], 0.0)

    def test_netzwerk_zaehlt_nur_echte_karten(self):
        self.assertEqual(parse_net(NET), (1000, 500))

    def test_festplatten_zaehlen_nur_ganze_geraete(self):
        self.assertEqual(parse_diskstats(DISKSTATS), (2000 * 512, 1000 * 512))


class FakeProc:
    def __init__(self):
        self.files = {}
        self.time = 100.0

    def reader(self, path):
        return self.files[path]

    def clock(self):
        return self.time

    def set(self, cpu, net_rx, net_tx, io_read, io_write, load="0.50 0.40 0.30 1/100 1"):
        total, idle = cpu
        self.files["/proc/stat"] = f"cpu {total - idle} 0 0 {idle} 0 0 0 0 0 0\n"
        self.files["/proc/net/dev"] = f"h\nh\n  ens6: {net_rx} 1 0 0 0 0 0 0 {net_tx} 1 0 0 0 0 0 0\n"
        self.files["/proc/diskstats"] = f" 252 0 vda 1 0 {io_read // 512} 0 1 0 {io_write // 512} 0 0 0 0\n"
        self.files["/proc/meminfo"] = MEMINFO
        self.files["/proc/loadavg"] = load


class SamplerTest(unittest.TestCase):
    def test_erste_messung_hat_keinen_vergleich(self):
        proc = FakeProc()
        proc.set((1000, 800), 0, 0, 0, 0)

        self.assertIsNone(Sampler(proc.reader, "/", proc.clock).sample())

    def test_werte_pro_sekunde(self):
        proc = FakeProc()
        sampler = Sampler(proc.reader, "/", proc.clock)
        proc.set((1000, 800), 1000, 500, 0, 0)
        sampler.sample()
        proc.time = 102.0
        proc.set((1200, 900), 3000, 1500, 4096, 8192)

        sample = sampler.sample()

        self.assertAlmostEqual(sample["cpu"], 50.0)
        self.assertAlmostEqual(sample["rx"], 1000.0)
        self.assertAlmostEqual(sample["tx"], 500.0)
        self.assertAlmostEqual(sample["read"], 2048.0)
        self.assertAlmostEqual(sample["write"], 4096.0)
        self.assertAlmostEqual(sample["load1"], 0.5)
        self.assertAlmostEqual(sample["mem"], 60.0)
        self.assertTrue(0 <= sample["disk"] <= 100)

    def test_zaehler_der_zurueckspringt_ergibt_null_statt_negativ(self):
        proc = FakeProc()
        sampler = Sampler(proc.reader, "/", proc.clock)
        proc.set((1000, 800), 5000, 5000, 0, 0)
        sampler.sample()
        proc.time = 102.0
        proc.set((1100, 850), 100, 100, 0, 0)

        sample = sampler.sample()

        self.assertEqual((sample["rx"], sample["tx"]), (0, 0))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = MetricsStore(Path(self._tmp.name) / "daten" / "m.sqlite")
        self.addCleanup(self.store.close)

    def values(self, number):
        return {name: float(number) for name in FIELDS}

    def test_speichern_und_abfragen_mit_mittelwert_pro_fenster(self):
        for ts, value in [(100, 10), (110, 20), (160, 30)]:
            self.store.add(ts, self.values(value))

        points = self.store.query(0, 60)

        self.assertEqual([p["t"] for p in points], [60, 120])
        self.assertEqual([p["cpu"] for p in points], [15.0, 30.0])

    def test_abfrage_beginnt_ab_dem_zeitpunkt(self):
        for ts in (100, 200, 300):
            self.store.add(ts, self.values(1))

        self.assertEqual(len(self.store.query(150, 15)), 2)

    def test_alte_werte_werden_geloescht(self):
        self.store.add(100, self.values(1))
        self.store.add(100 + 9 * 24 * 3600, self.values(2))

        self.assertEqual(len(self.store.query(0, 15)), 1)


class ServiceTest(unittest.TestCase):
    def make(self):
        proc = FakeProc()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = MetricsStore(Path(tmp.name) / "m.sqlite")
        self.addCleanup(store.close)
        service = MetricsService(store, Sampler(proc.reader, "/", proc.clock), clock=proc.clock)
        return proc, service

    def advance(self, proc, service, seconds, step=2):
        total, rx = 1000, 0
        for _ in range(0, seconds, step):
            proc.time += step
            total += 100
            rx += 100
            proc.set((total, total - 50), rx, rx, 0, 0)
            service.tick()

    def test_kurze_ansichten_kommen_aus_dem_speicher(self):
        proc, service = self.make()
        proc.set((1000, 900), 0, 0, 0, 0)
        service.tick()

        self.advance(proc, service, 40)
        result = service.series("1min")

        self.assertEqual(result["ansicht"], "1min")
        self.assertEqual(len(result["punkte"]), 20)
        self.assertEqual(result["intervall"], 2)

    def test_alle_15_sekunden_geht_ein_mittelwert_in_die_datenbank(self):
        proc, service = self.make()
        proc.set((1000, 900), 0, 0, 0, 0)
        service.tick()

        self.advance(proc, service, 120)
        result = service.series("1h")

        self.assertGreaterEqual(len(result["punkte"]), 6)
        self.assertLessEqual(len(result["punkte"]), 9)

    def test_unbekannte_ansicht(self):
        _, service = self.make()

        with self.assertRaisesRegex(ValueError, "Ansicht"):
            service.series("10min")

    def test_alle_sechs_ansichten_sind_definiert(self):
        _, service = self.make()

        for view in ("1min", "5min", "1h", "12h", "1d", "7d"):
            self.assertEqual(service.series(view)["ansicht"], view)


if __name__ == "__main__":
    unittest.main()
