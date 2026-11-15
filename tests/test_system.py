import unittest

from server_terminal import system

MOUNTS = """/dev/vda1 / ext4 rw 0 0
proc /proc proc rw 0 0
/dev/vda1 /var/lib/docker/overlay2/x ext4 rw 0 0
/dev/sdb1 /DATA xfs rw 0 0
/dev/vda1 /DATA/websites ext4 rw 0 0
/dev/loop1 /snap/core/1 squashfs ro 0 0
tmpfs /run tmpfs rw 0 0
"""
TOP = """top - 14:00:00 up 1 day,  1 user,  load average: 0.10, 0.20, 0.30
Tasks: 100 total
%Cpu(s):  1.0 us

    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
   9999 root      20   0    1000    100     50 R  99.0   0.1   0:00.10 top
      1 root      20   0    1000    100     50 S   0.0   0.1   0:01.00 systemd

top - 14:00:01 up 1 day,  1 user,  load average: 0.10, 0.20, 0.30
Tasks: 100 total
%Cpu(s):  1.0 us

    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
   9999 root      20   0    1000    100     50 R  50.0   0.1   0:00.20 top
   1234 www-data  20   0    1000    100     50 S  12,5   3.1  10:02.03 python3 -m web
      1 root      20   0    1000    100     50 S   0.0   0.1   0:01.00 systemd
"""


class SystemTest(unittest.IsolatedAsyncioTestCase):
    def test_einhaengepunkte_echter_dateisysteme_ohne_doppelte(self):
        self.assertEqual(system.parse_mounts(MOUNTS), ["/", "/DATA"])

    def test_prozessliste_nimmt_die_zweite_runde_und_ignoriert_top_selbst(self):
        processes = system.parse_top(TOP)

        self.assertEqual(
            processes[0],
            {"pid": 1234, "benutzer": "www-data", "cpu": 12.5, "speicher": 3.1, "laeuft": "10:02.03", "name": "python3 -m web"},
        )
        self.assertEqual([p["pid"] for p in processes], [1234, 1])

    def test_prozessliste_mit_limit_und_muell(self):
        self.assertEqual(len(system.parse_top(TOP, limit=1)), 1)
        self.assertEqual(system.parse_top("kopf\nkaputt\n"), [])
        self.assertEqual(system.parse_top(""), [])

    def test_laufzeit(self):
        self.assertEqual(system.uptime_seconds(lambda: "12345.67 54321.00\n"), 12345.67)

    def test_uebersicht_hat_die_wichtigen_felder(self):
        data = system.overview(1_800_000_000)

        for key in (
            "zeit",
            "hostname",
            "betriebssystem",
            "kernel",
            "laufzeit_s",
            "start_zeit",
            "last",
            "neustart_noetig",
            "festplatten",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["zeit"], 1_800_000_000)
        self.assertAlmostEqual(data["start_zeit"], 1_800_000_000 - data["laufzeit_s"], places=3)
        self.assertEqual(len(data["last"]), 3)

    async def test_befehl_ausfuehren(self):
        code, output = await system.run("printf", "hallo")

        self.assertEqual((code, output), (0, "hallo"))

    async def test_fehlerhafter_befehl_liefert_exit_code(self):
        code, _ = await system.run("sh", "-c", "exit 3")

        self.assertEqual(code, 3)

    async def test_unbekannter_befehl(self):
        code, output = await system.run("gibt-es-nicht-xyz")

        self.assertEqual(code, 127)
        self.assertIn("nicht gefunden", output)

    async def test_zeitueberschreitung(self):
        code, output = await system.run("sleep", "5", timeout=0.2)

        self.assertEqual(code, 124)
        self.assertIn("Zeitüberschreitung", output)

    async def test_eingabe_wird_weitergegeben(self):
        code, output = await system.run("cat", input_text="durchgereicht")

        self.assertEqual((code, output), (0, "durchgereicht"))


if __name__ == "__main__":
    unittest.main()
